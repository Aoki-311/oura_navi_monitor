#!/usr/bin/env python3
"""Crash-safe intent/final receipt state machine for Monitor traffic promotion."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple

try:
    from scripts.validate_refresh_job import validate_refresh_job
    from scripts.verify_service_access_contract import (
        require_exact_tagged_revision,
        require_reconciled_traffic,
        traffic_planes,
    )
except ModuleNotFoundError:  # Direct execution from scripts/.
    from validate_refresh_job import validate_refresh_job
    from verify_service_access_contract import (
        require_exact_tagged_revision,
        require_reconciled_traffic,
        traffic_planes,
    )


INTENT_TYPE = "monitor_candidate_promotion_intent_v1"
FINAL_TYPE = "monitor_candidate_promotion_v2"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_BUILD_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_FIRESTORE_DATABASE_RE = re.compile(r"^[a-z][a-z0-9-]{2,62}$")
_FIRESTORE_COLLECTION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,199}$")
RELEASE_LOCK_DATABASE = "lcs-user-data"
RELEASE_LOCK_COLLECTION = "monitor_release_locks"
CANDIDATE_TAG = "candidate"
_RECEIPT_ARGUMENTS = (
    ("schema", "schema_receipt", "schema receipt"),
    ("api", "api_receipt", "candidate API receipt"),
    ("backfill", "backfill_receipt", "backfill receipt"),
    ("acceptance", "acceptance_receipt", "candidate acceptance receipt"),
    ("activation", "activation_receipt", "activation receipt"),
    ("pause", "dts_pause_snapshot", "DTS pause snapshot"),
    ("dts45", "dts_45m_receipt", "45-minute observation"),
    ("dts72", "dts_72h_receipt", "72-hour observation"),
)
_STATIC_CONTRACT_KEYS = (
    "project",
    "region",
    "service",
    "targetRevision",
    "image",
    "gitSha",
    "buildId",
    "serviceAccount",
    "schemaReceiptSha256",
    "apiReceiptSha256",
    "backfillReceiptSha256",
    "acceptanceReceiptSha256",
    "activationReceiptSha256",
    "dtsPauseSnapshotSha256",
    "dts45mReceiptSha256",
    "dts72hReceiptSha256",
    "firestoreDatabase",
    "releaseLockCollection",
    "canonicalSchedulerGovernance",
    "canonicalJobGovernance",
    "legacyTransferGovernance",
)


class ReceiptSnapshot(NamedTuple):
    raw: dict[str, bytes]
    payloads: dict[str, dict[str, Any]]
    hashes: dict[str, str]


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _observed_resource_names(payload: dict[str, Any]) -> set[str]:
    metadata = payload.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("resource metadata is not an object")
    observed = {
        str(value).strip()
        for value in (
            (metadata or {}).get("name"),
            payload.get("name"),
        )
        if str(value or "").strip()
    }
    if not observed:
        raise ValueError("resource readback has no identity")
    return observed


def _require_service_identity(
    payload: dict[str, Any], *, project: str, region: str, service: str
) -> None:
    observed = _observed_resource_names(payload)
    allowed = {
        service,
        f"projects/{project}/locations/{region}/services/{service}",
    }
    if not observed <= allowed:
        raise ValueError("service readback identity mismatch")


def _require_revision_identity(
    payload: dict[str, Any],
    *,
    project: str,
    region: str,
    service: str,
    revision: str,
) -> None:
    observed = _observed_resource_names(payload)
    allowed = {
        revision,
        (
            f"projects/{project}/locations/{region}/services/{service}/"
            f"revisions/{revision}"
        ),
    }
    if not observed <= allowed:
        raise ValueError("revision readback identity mismatch")


def _validate_pre_service(
    service: dict[str, Any],
    *,
    project: str,
    region: str,
    service_name: str,
    target_revision: str,
) -> list[dict[str, Any]]:
    _require_service_identity(
        service,
        project=project,
        region=region,
        service=service_name,
    )
    planes = traffic_planes(service, label="pre-promotion service")
    positive = require_reconciled_traffic(planes, label="pre-promotion service")
    if not positive or sum(item["percent"] for item in positive) != 100:
        raise ValueError("pre-promotion traffic must have old revisions totaling 100%")
    if any(item["revisionName"] == target_revision for item in positive):
        raise ValueError("candidate already has positive production traffic")
    require_exact_tagged_revision(
        planes,
        label="pre-promotion candidate",
        tag=CANDIDATE_TAG,
        revision=target_revision,
    )
    for plane, rows in planes.items():
        target_rows = [row for row in rows if row["revisionName"] == target_revision]
        if len(target_rows) != 1:
            raise ValueError(
                f"pre-promotion candidate {plane} traffic must resolve exactly once"
            )
    return positive


def _validate_target_service(
    service: dict[str, Any],
    *,
    project: str,
    region: str,
    service_name: str,
    target_revision: str,
) -> None:
    _require_service_identity(
        service,
        project=project,
        region=region,
        service=service_name,
    )
    try:
        planes = traffic_planes(service, label="post-promotion service")
        target_traffic = require_reconciled_traffic(
            planes, label="post-promotion service"
        )
    except ValueError as exc:
        raise ValueError(
            "post-promotion traffic is not exactly target revision at 100%"
        ) from exc
    if target_traffic != [{"revisionName": target_revision, "percent": 100}]:
        raise ValueError("post-promotion traffic is not exactly target revision at 100%")
    require_exact_tagged_revision(
        planes,
        label="post-promotion candidate",
        tag=CANDIDATE_TAG,
        revision=target_revision,
    )


def _expected_revision_name(
    *, service: str, git_sha: str, build_id: str
) -> str:
    if not _GIT_SHA_RE.fullmatch(git_sha):
        raise ValueError("expected Git SHA is invalid")
    if not _BUILD_ID_RE.fullmatch(build_id):
        raise ValueError("expected Cloud Build ID is invalid")
    return f"{service}-{git_sha[:7]}-{build_id[:8]}"


def _validate_revision(
    revision: dict[str, Any],
    *,
    project: str,
    region: str,
    service: str,
    target_revision: str,
    image: str,
    git_sha: str,
    build_id: str,
    service_account: str,
) -> None:
    if target_revision != _expected_revision_name(
        service=service,
        git_sha=git_sha,
        build_id=build_id,
    ):
        raise ValueError("target revision does not bind the Git SHA and Cloud Build ID")
    _require_revision_identity(
        revision,
        project=project,
        region=region,
        service=service,
        revision=target_revision,
    )
    spec = revision.get("spec") or {}
    containers = spec.get("containers") or []
    if len(containers) != 1 or not isinstance(containers[0], dict):
        raise ValueError("candidate revision must have exactly one container")
    if str(containers[0].get("image") or "") != image:
        raise ValueError("candidate revision container image mismatch")
    status = revision.get("status") or {}
    if not isinstance(status, dict):
        raise ValueError("candidate revision status is not an object")
    if "imageDigest" in status:
        observed_digest = status.get("imageDigest")
        expected_digest = image.rsplit("@", 1)[-1]
        if observed_digest not in {image, expected_digest}:
            raise ValueError("candidate revision status image digest mismatch")
    if spec.get("serviceAccountName") != service_account:
        raise ValueError("candidate revision runtime identity mismatch")
    labels = (revision.get("metadata") or {}).get("labels") or {}
    if labels.get("git-sha") != git_sha:
        raise ValueError("candidate revision Git SHA mismatch")
    conditions = status.get("conditions") or []
    if not any(
        item.get("type") == "Ready" and str(item.get("status") or "").lower() == "true"
        for item in conditions
        if isinstance(item, dict)
    ):
        raise ValueError("candidate revision is not Ready")


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(label + " is not an object")
    return value


def _require_text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(label + " is not a string")
    if not allow_empty and not value.strip():
        raise ValueError(label + " is empty")
    return value


def _scheduler_governance(payload: dict[str, Any]) -> dict[str, Any]:
    scheduler = _require_object(payload, "canonical Scheduler readback")
    retry = _require_object(scheduler.get("retryConfig"), "Scheduler retryConfig")
    retry_count = retry.get("retryCount")
    if isinstance(retry_count, bool) or not isinstance(retry_count, int):
        raise ValueError("Scheduler retryCount is not an integer")
    target = _require_object(scheduler.get("httpTarget"), "Scheduler httpTarget")
    oauth = _require_object(target.get("oauthToken"), "Scheduler oauthToken")
    return {
        "state": _require_text(scheduler.get("state"), "Scheduler state"),
        "schedule": _require_text(scheduler.get("schedule"), "Scheduler schedule"),
        "timeZone": _require_text(scheduler.get("timeZone"), "Scheduler timeZone"),
        "attemptDeadline": _require_text(
            scheduler.get("attemptDeadline"), "Scheduler attemptDeadline"
        ),
        "retryCount": retry_count,
        "httpTargetUri": _require_text(target.get("uri"), "Scheduler target URI"),
        "oauthServiceAccount": _require_text(
            oauth.get("serviceAccountEmail"), "Scheduler OAuth service account"
        ),
    }


def _legacy_transfer_governance(payload: dict[str, Any]) -> dict[str, Any]:
    transfer = _require_object(payload, "legacy DTS readback")
    disabled = transfer.get("disabled")
    if not isinstance(disabled, bool):
        raise ValueError("legacy DTS disabled flag is not a boolean")
    return {
        "name": _require_text(transfer.get("name"), "legacy DTS name"),
        "displayName": _require_text(
            transfer.get("displayName"), "legacy DTS display name"
        ),
        "dataSourceId": _require_text(
            transfer.get("dataSourceId"), "legacy DTS data source"
        ),
        "disabled": disabled,
        "destinationDatasetId": _require_text(
            transfer.get("destinationDatasetId", ""),
            "legacy DTS destination dataset",
            allow_empty=True,
        ),
        "schedule": _require_text(transfer.get("schedule"), "legacy DTS schedule"),
        "serviceAccountName": transfer.get("serviceAccountName"),
        "ownerInfo": transfer.get("ownerInfo"),
        "userId": transfer.get("userId"),
        "params": transfer.get("params"),
    }


def _parse_utc(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(label + " has no valid timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(label + " timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _read_receipt_snapshot(args: argparse.Namespace) -> ReceiptSnapshot:
    """Read every receipt exactly once and retain the bytes used for validation/hash."""

    raw: dict[str, bytes] = {}
    payloads: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    for key, argument, label in _RECEIPT_ARGUMENTS:
        receipt_bytes = Path(getattr(args, argument)).read_bytes()
        try:
            payload = json.loads(receipt_bytes.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValueError(label + " is not UTF-8 JSON") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(label + " is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError(label + " is not a JSON object")
        raw[key] = receipt_bytes
        payloads[key] = payload
        hashes[key] = _sha_bytes(receipt_bytes)
    return ReceiptSnapshot(raw=raw, payloads=payloads, hashes=hashes)


def _walk_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_objects(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_objects(nested)


def _execution_contract(payload: dict[str, Any]) -> tuple[str, str]:
    container = next(
        (
            node["containers"][0]
            for node in _walk_objects(payload)
            if isinstance(node.get("containers"), list)
            and len(node["containers"]) == 1
            and isinstance(node["containers"][0], dict)
        ),
        {},
    )
    identity = next(
        (
            str(node.get("serviceAccount") or node.get("serviceAccountName") or "")
            for node in _walk_objects(payload)
            if node.get("serviceAccount") or node.get("serviceAccountName")
        ),
        "",
    )
    return str(container.get("image") or ""), identity


def _receipt_count(payload: dict[str, Any], name: str, *, label: str) -> int:
    value = payload.get(name)
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise ValueError(label + " has invalid " + name)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(label + " has invalid " + name) from exc


def _nested_value(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = payload
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _validate_receipt_snapshot(
    args: argparse.Namespace,
    snapshot: ReceiptSnapshot,
    *,
    require_freshness: bool,
    now: datetime | None = None,
) -> None:
    """Validate the complete release receipt chain from one immutable byte snapshot."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("receipt validation reference time must include a timezone")
    current = current.astimezone(timezone.utc)
    payloads = snapshot.payloads

    schema = payloads["schema"]
    schema_expected = {
        "receiptType": "monitor_data_contract_v1",
        "project": args.project,
        "dataset": args.dataset,
        "location": args.location,
        "gitSha": args.git_sha,
        "image": args.image,
    }
    if any(schema.get(key) != value for key, value in schema_expected.items()):
        raise ValueError("schema receipt does not match this exact candidate and dataset")
    for key in (
        "schemaReady",
        "sourceViewsReady",
        "apiRoutinesReady",
        "apiRoutinesReadable",
        "publishedStateReadable",
    ):
        if schema.get(key) is not True:
            raise ValueError("schema receipt is missing " + key)
    routine_reads = _require_object(schema.get("apiRoutineReads"), "API routine reads")
    for routine_name in (
        "dashboard_events",
        "dashboard_user_list",
        "dashboard_events_v2",
        "dashboard_user_list_v2",
    ):
        routine = _require_object(
            routine_reads.get(routine_name), "API routine read " + routine_name
        )
        if routine.get("readable") is not True:
            raise ValueError("schema receipt has no real read for " + routine_name)
    _require_text(schema.get("capturedAt"), "schema receipt capture time")

    api = payloads["api"]
    api_expected = {
        "receiptType": "monitor_candidate_api_v1",
        "project": args.project,
        "region": args.region,
        "service": args.service,
        "revision": args.revision,
        "image": args.image,
        "gitSha": args.git_sha,
        "serviceAccount": args.service_account,
    }
    if any(api.get(key) != value for key, value in api_expected.items()):
        raise ValueError("API receipt does not match this exact candidate")
    if api.get("authenticatedApiAcceptance") is not True:
        raise ValueError("authenticated candidate API acceptance is missing")
    statuses = _require_object(api.get("endpointStatus"), "candidate API endpoint status")
    for endpoint in ("overview", "regions", "users", "userDetail"):
        if statuses.get(endpoint) != 200:
            raise ValueError(
                "candidate API receipt is missing a 200 readback for " + endpoint
            )
    for key in ("overviewHistoryVisible", "userHistoryVisible", "sourceDiagnosticsExplicit"):
        if api.get(key) is not True:
            raise ValueError("candidate API receipt is missing " + key)
    _require_text(api.get("verifiedBy"), "candidate API verifier")
    api_captured_at = _parse_utc(api.get("capturedAt"), "candidate API receipt")

    backfill = payloads["backfill"]
    backfill_expected = {
        "project": args.project,
        "region": args.region,
        "dataset": args.dataset,
        "location": args.location,
        "expected_image": args.image,
    }
    if any(backfill.get(key) != value for key, value in backfill_expected.items()):
        raise ValueError("backfill receipt does not match this release target")
    execution = _require_object(backfill.get("execution"), "backfill execution")
    execution_status = execution.get("status")
    if execution_status is None:
        execution_status = execution
    execution_status = _require_object(execution_status, "backfill execution status")
    execution_name = str(
        execution.get("name")
        or (_require_object(execution.get("metadata") or {}, "execution metadata")).get(
            "name"
        )
        or ""
    )
    if (
        not execution_name
        or _receipt_count(execution_status, "succeededCount", label="backfill execution")
        != 1
        or _receipt_count(execution_status, "failedCount", label="backfill execution")
        != 0
    ):
        raise ValueError("backfill receipt has no terminal successful execution")
    completed = next(
        (
            item
            for item in execution_status.get("conditions", [])
            if isinstance(item, dict) and item.get("type") == "Completed"
        ),
        None,
    )
    if completed is not None and str(completed.get("status") or "").lower() != "true":
        raise ValueError("backfill receipt execution is not terminal-successful")
    job_contract = _require_object(
        backfill.get("validated_job_contract"), "validated backfill Job contract"
    )
    if job_contract.get("image") != args.image:
        raise ValueError("backfill Job did not use the candidate image digest")
    execution_provenance = _require_object(
        backfill.get("validated_execution_provenance"),
        "validated backfill execution provenance",
    )
    if execution_provenance.get("image") != args.image:
        raise ValueError("backfill execution did not use the candidate image digest")
    if execution_provenance.get("serviceAccount") != job_contract.get("serviceAccount"):
        raise ValueError("backfill execution identity does not match the validated Job")
    if (
        _receipt_count(
            execution_provenance, "succeededCount", label="backfill execution provenance"
        )
        < 1
        or _receipt_count(
            execution_provenance, "failedCount", label="backfill execution provenance"
        )
        != 0
    ):
        raise ValueError("backfill execution provenance is not terminal-successful")
    _require_text(backfill.get("target_at"), "backfill target watermark")
    pipeline_after = backfill.get("pipeline_after")
    if not isinstance(pipeline_after, list) or len(pipeline_after) != 1:
        raise ValueError("backfill receipt has no single published readback")
    published = _require_object(pipeline_after[0], "backfill published readback")
    lease_active = str(published.get("lease_active") or "").strip().lower()
    if (
        published.get("source") != "published"
        or published.get("status") != "succeeded"
        or not published.get("published_run_id")
        or not published.get("data_through")
        or lease_active not in {"false", "0"}
    ):
        raise ValueError("backfill receipt does not contain a released successful publication")
    reconciliation = backfill.get("reconciliation")
    if not isinstance(reconciliation, list) or len(reconciliation) != 1:
        raise ValueError("backfill receipt has no reconciliation readback")
    reconciliation_row = _require_object(reconciliation[0], "backfill reconciliation")
    if (
        _receipt_count(
            reconciliation_row, "successful_run_count", label="backfill receipt"
        )
        < 1
        or _receipt_count(
            reconciliation_row, "blocking_failure_count", label="backfill receipt"
        )
        != 0
    ):
        raise ValueError("backfill receipt has no clean successful canonical run")
    for family in ("question", "answer", "action"):
        if _receipt_count(
            reconciliation_row, f"canonical_{family}_count", label="backfill receipt"
        ) != _receipt_count(
            reconciliation_row, f"matched_{family}_count", label="backfill receipt"
        ):
            raise ValueError("backfill receipt does not reconcile " + family + " facts")

    acceptance = payloads["acceptance"]
    acceptance_expected = {
        "project": args.project,
        "region": args.region,
        "service": args.service,
        "revision": args.revision,
        "image": args.image,
        "gitSha": args.git_sha,
        "serviceAccount": args.service_account,
    }
    if any(
        acceptance.get(key) != value for key, value in acceptance_expected.items()
    ):
        raise ValueError("acceptance receipt does not match this exact candidate")
    for key, message in (
        ("authenticatedAcceptance", "authenticated candidate acceptance is missing"),
        ("loggedInBrowserAcceptance", "logged-in browser candidate acceptance is missing"),
        ("historicalDataAcceptance", "historical data candidate acceptance is missing"),
        ("businessAcceptance", "business candidate acceptance is missing"),
    ):
        if acceptance.get(key) is not True:
            raise ValueError(message)
    _require_text(acceptance.get("acceptedBy"), "candidate acceptance operator")
    acceptance_captured_at = _parse_utc(
        acceptance.get("capturedAt"), "candidate acceptance receipt"
    )

    activation = payloads["activation"]
    activation_expected = {
        "project": args.project,
        "region": args.region,
        "dataset": args.dataset,
        "location": args.location,
        "source_service": args.source_service,
        "job": args.job,
        "new_scheduler": args.scheduler,
        "image": args.image,
        "expected_job_service_account": args.expected_job_service_account,
    }
    if any(activation.get(key) != value for key, value in activation_expected.items()):
        raise ValueError("activation receipt does not match this exact candidate")
    if activation.get("backfill_receipt_sha256") != snapshot.hashes["backfill"]:
        raise ValueError("activation receipt is not chained to this backfill receipt")
    _require_text(activation.get("canonical_start_at"), "activation canonical start")
    activation_scheduler = _require_object(
        activation.get("new_scheduler_readback"), "activation Scheduler readback"
    )
    if activation_scheduler.get("state") != "ENABLED":
        raise ValueError("activation receipt did not enable the canonical Scheduler")
    current_scheduler = _require_object(
        json.loads(os.environ["CURRENT_SCHEDULER_JSON"]),
        "current canonical Scheduler readback",
    )
    for path in (
        ("state",),
        ("schedule",),
        ("timeZone",),
        ("attemptDeadline",),
        ("retryConfig", "retryCount"),
        ("httpTarget", "uri"),
        ("httpTarget", "oauthToken", "serviceAccountEmail"),
    ):
        if (_nested_value(current_scheduler, path) or 0) != (
            _nested_value(activation_scheduler, path) or 0
        ):
            raise ValueError("canonical Scheduler drifted after the 72-hour receipt")
    if current_scheduler.get("state") != "ENABLED":
        raise ValueError("canonical Scheduler is not ENABLED immediately before traffic")

    pause = payloads["pause"]
    pause_expected = {
        "project": args.project,
        "region": args.region,
        "dataset": args.dataset,
        "location": args.location,
        "canonical_start_at": activation["canonical_start_at"],
    }
    if any(pause.get(key) != value for key, value in pause_expected.items()):
        raise ValueError("DTS pause snapshot does not match this activation chain")
    if pause.get("activation_receipt") != activation:
        raise ValueError("DTS pause snapshot embeds a different activation receipt")
    transfer_after = _require_object(
        pause.get("transfer_config_after"), "DTS pause transfer readback"
    )
    if transfer_after.get("disabled") is not True:
        raise ValueError("DTS pause snapshot does not prove disabled automatic scheduling")
    if pause.get("transfer_config_resource") != args.legacy_transfer_resource:
        raise ValueError("DTS pause snapshot has an unexpected transfer resource")
    current_transfer = _require_object(
        json.loads(os.environ["CURRENT_TRANSFER_JSON"]),
        "current legacy DTS readback",
    )
    if current_transfer.get("name") != args.legacy_transfer_resource:
        raise ValueError("DTS readback returned another transfer resource")
    if current_transfer.get("disabled") is not True:
        raise ValueError("legacy DTS is not disabled immediately before traffic")
    for key in (
        "name",
        "displayName",
        "dataSourceId",
        "destinationDatasetId",
        "schedule",
        "serviceAccountName",
        "ownerInfo",
        "userId",
        "params",
    ):
        if transfer_after.get(key) != current_transfer.get(key):
            raise ValueError("legacy DTS drifted after the 72-hour receipt: " + key)
    paused_at = _parse_utc(pause.get("paused_at"), "DTS pause snapshot")

    def validate_observation(key: str, required_minutes: int, label: str) -> datetime:
        observation = payloads[key]
        if observation.get("status") != "passed":
            raise ValueError(label + " observation did not pass")
        minimum = _receipt_count(
            observation, "minimum_observation_minutes", label=label + " observation"
        )
        elapsed = _receipt_count(
            observation, "elapsed_minutes", label=label + " observation"
        )
        if minimum < required_minutes or elapsed < required_minutes:
            raise ValueError(label + " observation window is too short")
        if observation.get("pause_snapshot_sha256") != snapshot.hashes["pause"]:
            raise ValueError(label + " observation is not chained to this pause snapshot")
        verified_at = _parse_utc(
            observation.get("verified_at"), label + " observation"
        )
        if verified_at < paused_at:
            raise ValueError(label + " observation predates the DTS pause")
        if verified_at > current + timedelta(minutes=1):
            raise ValueError(label + " observation timestamp is in the future")
        actual_elapsed = int((verified_at - paused_at).total_seconds() // 60)
        if actual_elapsed < required_minutes:
            raise ValueError(
                label + " observation timestamps do not cover the required window"
            )
        if abs(elapsed - actual_elapsed) > 1:
            raise ValueError(
                label + " observation elapsed minutes disagree with timestamps"
            )
        scheduler = _require_object(
            observation.get("canonical_scheduler"),
            label + " observation Scheduler",
        )
        for path in (
            ("state",),
            ("schedule",),
            ("timeZone",),
            ("attemptDeadline",),
            ("retryConfig", "retryCount"),
            ("httpTarget", "uri"),
            ("httpTarget", "oauthToken", "serviceAccountEmail"),
        ):
            if (_nested_value(scheduler, path) or 0) != (
                _nested_value(activation_scheduler, path) or 0
            ):
                raise ValueError(
                    label + " observation Scheduler drifted from activation"
                )
        observed_image, observed_identity = _execution_contract(
            _require_object(
                observation.get("canonical_job"), label + " observation Job"
            )
        )
        if observed_image != args.image:
            raise ValueError(
                label + " observation Job image drifted from the candidate"
            )
        if observed_identity != args.expected_job_service_account:
            raise ValueError(label + " observation Job identity drifted from activation")
        return verified_at

    verified_45m_at = validate_observation("dts45", 45, "45-minute")
    verified_72h_at = validate_observation("dts72", 4320, "72-hour")
    if verified_72h_at < verified_45m_at:
        raise ValueError("72-hour observation predates the 45-minute observation")
    for captured_at, label in (
        (api_captured_at, "candidate API receipt"),
        (acceptance_captured_at, "candidate acceptance receipt"),
    ):
        if captured_at < verified_72h_at:
            raise ValueError(label + " must be captured after the 72-hour observation")
        if captured_at > current + timedelta(minutes=1):
            raise ValueError(label + " timestamp is in the future")
        if require_freshness and current - captured_at > timedelta(minutes=60):
            raise ValueError(label + " is older than 60 minutes")


def _validated_receipt_snapshot(
    args: argparse.Namespace,
    *,
    require_freshness: bool = False,
    now: datetime | None = None,
) -> ReceiptSnapshot:
    snapshot = _read_receipt_snapshot(args)
    _validate_receipt_snapshot(
        args,
        snapshot,
        require_freshness=require_freshness,
        now=now,
    )
    return snapshot


def _validate_lock_namespace(database: str, collection: str) -> None:
    if database == "(default)" or not _FIRESTORE_DATABASE_RE.fullmatch(database):
        raise ValueError("Firestore release lock requires an exact named database")
    if not _FIRESTORE_COLLECTION_RE.fullmatch(collection):
        raise ValueError("Firestore release lock collection is invalid")
    if database != RELEASE_LOCK_DATABASE or collection != RELEASE_LOCK_COLLECTION:
        raise ValueError("Firestore release lock namespace is not the governed owner")


def _static_contract(
    args: argparse.Namespace,
    *,
    receipt_snapshot: ReceiptSnapshot,
) -> dict[str, Any]:
    _validate_lock_namespace(args.firestore_database, args.release_lock_collection)
    snapshot = receipt_snapshot
    scheduler = json.loads(os.environ["CURRENT_SCHEDULER_JSON"])
    job = json.loads(os.environ["CURRENT_JOB_JSON"])
    transfer = json.loads(os.environ["CURRENT_TRANSFER_JSON"])
    if args.job_timeout_minutes <= 0:
        raise ValueError("refresh Job timeout minutes must be positive")
    return {
        "project": args.project,
        "region": args.region,
        "service": args.service,
        "targetRevision": args.revision,
        "image": args.image,
        "gitSha": args.git_sha,
        "buildId": args.build_id,
        "serviceAccount": args.service_account,
        "schemaReceiptSha256": snapshot.hashes["schema"],
        "apiReceiptSha256": snapshot.hashes["api"],
        "backfillReceiptSha256": snapshot.hashes["backfill"],
        "acceptanceReceiptSha256": snapshot.hashes["acceptance"],
        "activationReceiptSha256": snapshot.hashes["activation"],
        "dtsPauseSnapshotSha256": snapshot.hashes["pause"],
        "dts45mReceiptSha256": snapshot.hashes["dts45"],
        "dts72hReceiptSha256": snapshot.hashes["dts72"],
        "firestoreDatabase": args.firestore_database,
        "releaseLockCollection": args.release_lock_collection,
        "canonicalSchedulerGovernance": _scheduler_governance(scheduler),
        "canonicalJobGovernance": validate_refresh_job(
            job,
            expected_image=args.image,
            expected_service_account=args.expected_job_service_account,
            project_id=args.project,
            dataset_id=args.dataset,
            location=args.location,
            source_service=args.source_service,
            timeout_minutes=args.job_timeout_minutes,
        ),
        "legacyTransferGovernance": _legacy_transfer_governance(transfer),
    }


def _intent_hash(payload: dict[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned.pop("intentPayloadSha256", None)
    return _sha_bytes(_canonical(unsigned).encode("utf-8"))


def _validate_intent_integrity(payload: dict[str, Any]) -> None:
    observed = str(payload.get("intentPayloadSha256") or "")
    if not _SHA256_RE.fullmatch(observed) or observed != _intent_hash(payload):
        raise ValueError("promotion intent integrity mismatch")


def _validate_static(payload: dict[str, Any], expected: dict[str, Any]) -> None:
    for key, value in expected.items():
        if payload.get(key) != value:
            raise ValueError("promotion intent does not match the exact release: " + key)


def _static_contract_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    missing = [key for key in _STATIC_CONTRACT_KEYS if key not in payload]
    if missing:
        raise ValueError("promotion state is missing static contract: " + missing[0])
    return {key: payload[key] for key in _STATIC_CONTRACT_KEYS}


def _static_contract_hash(payload: dict[str, Any]) -> str:
    contract = _static_contract_from_payload(payload)
    return _sha_bytes(_canonical(contract).encode("utf-8"))


def _read_payload(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("promotion output is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("promotion output is not a JSON object")
    return payload, raw


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_new_intent(path: Path, payload: dict[str, Any]) -> bool:
    payload["intentPayloadSha256"] = _intent_hash(payload)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.intent-",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        _fsync_directory(path.parent)
        return True
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_finalize(path: Path, *, expected_raw: bytes, payload: dict[str, Any]) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    descriptor, temporary = tempfile.mkstemp(
        prefix=path.name + ".final-",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if path.read_bytes() != expected_raw:
            raise ValueError("promotion intent changed during finalization")
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--path", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--service-account", required=True)
    parser.add_argument("--expected-job-service-account", required=True)
    parser.add_argument("--legacy-transfer-resource", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--source-service", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--scheduler", required=True)
    parser.add_argument("--job-timeout-minutes", type=int, required=True)
    parser.add_argument("--firestore-database", required=True)
    parser.add_argument("--release-lock-collection", required=True)
    parser.add_argument("--schema-receipt", required=True)
    parser.add_argument("--api-receipt", required=True)
    parser.add_argument("--backfill-receipt", required=True)
    parser.add_argument("--acceptance-receipt", required=True)
    parser.add_argument("--activation-receipt", required=True)
    parser.add_argument("--dts-pause-snapshot", required=True)
    parser.add_argument("--dts-45m-receipt", required=True)
    parser.add_argument("--dts-72h-receipt", required=True)
    return parser


def _validate_existing(
    payload: dict[str, Any], *, expected: dict[str, Any]
) -> str:
    receipt_type = payload.get("receiptType")
    if receipt_type not in {INTENT_TYPE, FINAL_TYPE}:
        raise ValueError("promotion output has an unsupported receipt type")
    _validate_static(payload, expected)
    _validate_intent_integrity(payload)
    return str(receipt_type)


def _release_lock_identity(payload: dict[str, Any]) -> dict[str, str]:
    """Return the exact durable-lock identity from one validated payload."""

    receipt_type = payload.get("receiptType")
    expected_state = {
        INTENT_TYPE: "intent",
        FINAL_TYPE: "complete",
    }.get(receipt_type)
    if expected_state is None or payload.get("state") != expected_state:
        raise ValueError("promotion state has an unsupported receipt state")
    _validate_intent_integrity(payload)
    database = _require_text(
        payload.get("firestoreDatabase"), "Firestore release lock database"
    )
    collection = _require_text(
        payload.get("releaseLockCollection"), "Firestore release lock collection"
    )
    _validate_lock_namespace(database, collection)
    if receipt_type == INTENT_TYPE:
        intent_hash = str(payload.get("intentPayloadSha256") or "")
    else:
        intent_hash = str(payload.get("lockIntentPayloadSha256") or "")
    if not _SHA256_RE.fullmatch(intent_hash):
        raise ValueError("promotion state has no exact lock intent hash")
    identity = {
        "project": _require_text(payload.get("project"), "promotion project"),
        "region": _require_text(payload.get("region"), "promotion region"),
        "service": _require_text(payload.get("service"), "promotion service"),
        "targetRevision": _require_text(
            payload.get("targetRevision"), "promotion target revision"
        ),
        "intentPayloadSha256": intent_hash,
        "staticContractSha256": _static_contract_hash(payload),
        "firestoreDatabase": database,
        "releaseLockCollection": collection,
    }
    return identity


def read_release_lock_identity(path: str | Path) -> dict[str, str]:
    """Return the durable-lock identity from one state-file byte snapshot."""

    payload, _ = _read_payload(Path(path))
    return _release_lock_identity(payload)


def _is_final_release_payload(payload: dict[str, Any]) -> bool:
    """Return whether one integrity-checked promotion payload is already final."""

    receipt_type = payload.get("receiptType")
    expected_state = {
        INTENT_TYPE: "intent",
        FINAL_TYPE: "complete",
    }.get(receipt_type)
    if expected_state is None or payload.get("state") != expected_state:
        raise ValueError("promotion state has an unsupported receipt state")
    _validate_intent_integrity(payload)
    if receipt_type == INTENT_TYPE:
        return False

    lock_intent_hash = str(payload.get("lockIntentPayloadSha256") or "")
    raw_intent_hash = str(payload.get("intentSha256") or "")
    if not _SHA256_RE.fullmatch(lock_intent_hash):
        raise ValueError("final promotion state has no lock intent hash")
    if not _SHA256_RE.fullmatch(raw_intent_hash):
        raise ValueError("final promotion state has no raw intent hash")
    original_intent = dict(payload)
    for key in (
        "intentSha256",
        "promotedAt",
        "trafficReadbackAt",
        "serviceAfter",
        "revisionAfter",
        "updateCommandReturnCode",
        "lockIntentPayloadSha256",
    ):
        original_intent.pop(key, None)
    original_intent.update(
        {
            "receiptType": INTENT_TYPE,
            "state": "intent",
            "intentPayloadSha256": lock_intent_hash,
        }
    )
    _validate_intent_integrity(original_intent)
    _static_contract_from_payload(original_intent)
    encoded_intent = (
        json.dumps(original_intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if _sha_bytes(encoded_intent) != raw_intent_hash:
        raise ValueError("final promotion state does not bind the raw intent")

    before = _require_object(payload.get("serviceBefore"), "pre-promotion service")
    expected_old_traffic = _validate_pre_service(
        before,
        project=_require_text(payload.get("project"), "promotion project"),
        region=_require_text(payload.get("region"), "promotion region"),
        service_name=_require_text(payload.get("service"), "promotion service"),
        target_revision=_require_text(
            payload.get("targetRevision"), "promotion target revision"
        ),
    )
    if expected_old_traffic != payload.get("oldPositiveTraffic"):
        raise ValueError("final promotion state changed the original traffic allocation")
    _validate_target_service(
        _require_object(payload.get("serviceAfter"), "post-promotion service"),
        project=payload["project"],
        region=payload["region"],
        service_name=payload["service"],
        target_revision=payload["targetRevision"],
    )
    for field in ("revisionBefore", "revisionAfter"):
        _validate_revision(
            _require_object(payload.get(field), field),
            project=payload["project"],
            region=payload["region"],
            service=payload["service"],
            target_revision=payload["targetRevision"],
            image=_require_text(payload.get("image"), "promotion image"),
            git_sha=_require_text(payload.get("gitSha"), "promotion Git SHA"),
            build_id=_require_text(payload.get("buildId"), "promotion build ID"),
            service_account=_require_text(
                payload.get("serviceAccount"), "promotion service account"
            ),
        )
    timestamps: list[datetime] = []
    for field in ("capturedAt", "promotedAt", "trafficReadbackAt"):
        raw_timestamp = _require_text(payload.get(field), field)
        try:
            timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(field + " is not an ISO timestamp") from exc
        if timestamp.tzinfo is None:
            raise ValueError(field + " must include a timezone")
        timestamps.append(timestamp.astimezone(timezone.utc))
    if not timestamps[0] <= timestamps[1] <= timestamps[2]:
        raise ValueError("traffic readback predates promotion")
    if timestamps[2] > datetime.now(timezone.utc) + timedelta(minutes=1):
        raise ValueError("final promotion timestamps are in the future")
    update_return_code = payload.get("updateCommandReturnCode")
    if isinstance(update_return_code, bool) or not isinstance(update_return_code, int):
        raise ValueError("final promotion state has no update return code")
    return True


def is_final_release_state(path: str | Path) -> bool:
    """Return whether one state-file byte snapshot is already final."""

    payload, _ = _read_payload(Path(path))
    return _is_final_release_payload(payload)


def read_release_lock_contract(path: str | Path) -> tuple[dict[str, str], bool]:
    """Read lock identity and final-state status from the exact same bytes."""

    payload, _ = _read_payload(Path(path))
    return _release_lock_identity(payload), _is_final_release_payload(payload)


def prepare(args: argparse.Namespace) -> str:
    path = Path(args.path)
    snapshot = _validated_receipt_snapshot(
        args,
        require_freshness=not path.exists(),
    )
    expected = _static_contract(args, receipt_snapshot=snapshot)
    service = json.loads(os.environ["SERVICE_JSON"])
    revision = json.loads(os.environ["REVISION_JSON"])
    _validate_revision(
        revision,
        project=args.project,
        region=args.region,
        service=args.service,
        target_revision=args.revision,
        image=args.image,
        git_sha=args.git_sha,
        build_id=args.build_id,
        service_account=args.service_account,
    )
    if path.exists():
        payload, _ = _read_payload(path)
        return _validate_existing(payload, expected=expected)
    old_traffic = _validate_pre_service(
        service,
        project=args.project,
        region=args.region,
        service_name=args.service,
        target_revision=args.revision,
    )
    payload = {
        "receiptType": INTENT_TYPE,
        "state": "intent",
        **expected,
        "capturedAt": _utc_now(),
        "serviceBefore": service,
        "revisionBefore": revision,
        "oldPositiveTraffic": old_traffic,
        "initialGovernanceEvidence": {
            "canonicalScheduler": json.loads(
                os.environ["CURRENT_SCHEDULER_JSON"]
            ),
            "canonicalJob": json.loads(os.environ["CURRENT_JOB_JSON"]),
            "legacyTransfer": json.loads(os.environ["CURRENT_TRANSFER_JSON"]),
        },
    }
    if _write_new_intent(path, payload):
        return INTENT_TYPE
    published, _ = _read_payload(path)
    return _validate_existing(published, expected=expected)


def classify(args: argparse.Namespace) -> str:
    path = Path(args.path)
    payload, _ = _read_payload(path)
    snapshot = _validated_receipt_snapshot(args)
    receipt_type = _validate_existing(
        payload,
        expected=_static_contract(args, receipt_snapshot=snapshot),
    )
    service = json.loads(os.environ["SERVICE_JSON"])
    revision = json.loads(os.environ["REVISION_JSON"])
    _validate_revision(
        revision,
        project=args.project,
        region=args.region,
        service=args.service,
        target_revision=args.revision,
        image=args.image,
        git_sha=args.git_sha,
        build_id=args.build_id,
        service_account=args.service_account,
    )
    if receipt_type == FINAL_TYPE:
        _validate_target_service(
            service,
            project=args.project,
            region=args.region,
            service_name=args.service,
            target_revision=args.revision,
        )
        return "final"
    try:
        current_old_traffic = _validate_pre_service(
            service,
            project=args.project,
            region=args.region,
            service_name=args.service,
            target_revision=args.revision,
        )
    except ValueError:
        current_old_traffic = None
    if current_old_traffic == payload.get("oldPositiveTraffic"):
        return "pre"
    try:
        _validate_target_service(
            service,
            project=args.project,
            region=args.region,
            service_name=args.service,
            target_revision=args.revision,
        )
    except ValueError as exc:
        raise ValueError("live service drifted from both intent states") from exc
    return "post"


def validate_freshness(
    args: argparse.Namespace, *, now: datetime | None = None
) -> str:
    """Validate freshness against the exact receipt bytes bound into the intent."""

    payload, _ = _read_payload(Path(args.path))
    snapshot = _validated_receipt_snapshot(
        args,
        require_freshness=True,
        now=now,
    )
    _validate_existing(
        payload,
        expected=_static_contract(args, receipt_snapshot=snapshot),
    )
    return "fresh"


def finalize(args: argparse.Namespace) -> None:
    path = Path(args.path)
    intent, raw = _read_payload(path)
    snapshot = _validated_receipt_snapshot(args)
    if (
        _validate_existing(
            intent,
            expected=_static_contract(args, receipt_snapshot=snapshot),
        )
        != INTENT_TYPE
    ):
        raise ValueError("only an exact promotion intent can be finalized")
    service = json.loads(os.environ["SERVICE_JSON"])
    revision = json.loads(os.environ["REVISION_JSON"])
    _validate_target_service(
        service,
        project=args.project,
        region=args.region,
        service_name=args.service,
        target_revision=args.revision,
    )
    _validate_revision(
        revision,
        project=args.project,
        region=args.region,
        service=args.service,
        target_revision=args.revision,
        image=args.image,
        git_sha=args.git_sha,
        build_id=args.build_id,
        service_account=args.service_account,
    )
    final = dict(intent)
    final.update(
        {
            "receiptType": FINAL_TYPE,
            "state": "complete",
            "intentSha256": _sha_bytes(raw),
            "promotedAt": _utc_now(),
            "trafficReadbackAt": _utc_now(),
            "serviceAfter": service,
            "revisionAfter": revision,
            "updateCommandReturnCode": args.update_return_code,
            "lockIntentPayloadSha256": intent["intentPayloadSha256"],
        }
    )
    final["intentPayloadSha256"] = _intent_hash(final)
    _atomic_finalize(path, expected_raw=raw, payload=final)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "classify", "freshness"):
        child = subparsers.add_parser(name, parents=[_common_parser()])
        child.set_defaults(
            handler={
                "prepare": prepare,
                "classify": classify,
                "freshness": validate_freshness,
            }[name]
        )
    final = subparsers.add_parser("finalize", parents=[_common_parser()])
    final.add_argument("--update-return-code", type=int, required=True)
    final.set_defaults(handler=finalize)
    args = parser.parse_args()
    try:
        result = args.handler(args)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit("promotion_state_invalid: " + str(exc)) from exc
    if isinstance(result, str):
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
