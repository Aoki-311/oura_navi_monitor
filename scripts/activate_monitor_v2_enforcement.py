#!/usr/bin/env python3
"""Activate post-cutover monitor.v2 enforcement from one drained LCS promotion."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from google.cloud import bigquery
from google.oauth2 import service_account

try:
    from scripts.credential_preflight import approved_credential_path
    from scripts.verify_service_access_contract import (
        require_exact_tagged_revision,
        require_reconciled_traffic,
        traffic_planes,
    )
except ModuleNotFoundError:
    from credential_preflight import approved_credential_path
    from verify_service_access_contract import (
        require_exact_tagged_revision,
        require_reconciled_traffic,
        traffic_planes,
    )


_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_DATASET_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")
_REVISION_RE = re.compile(r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_PROMOTION_RECEIPT_TYPE = "lcs_candidate_promotion_v2"
_ACTIVATION_SOURCE = "lcs_promotion_v2_drained_live_readback"
_ACTIVATION_RECEIPT_TYPE = "monitor_v2_enforcement_activation_v1"
_CANDIDATE_TAG = "candidate"
_MAX_RECEIPT_BYTES = 2_000_000
_GCLOUD_TIMEOUT_SECONDS = 60


def _parse_utc(value: Any, *, field: str) -> datetime:
    text = str(value or "").strip()
    if not _UTC_RE.fullmatch(text):
        raise ValueError(f"{field} must be an exact UTC timestamp ending in Z")
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _reconciled_positive_traffic(
    service: dict[str, Any], *, label: str
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    planes = traffic_planes(service, label=label)
    return planes, require_reconciled_traffic(planes, label=label)


def _require_service_identity(
    service: dict[str, Any], *, project: str, region: str, service_name: str
) -> None:
    metadata = service.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("Cloud Run service metadata must be an object")
    observed_names = {
        str(value).strip()
        for value in (
            (metadata or {}).get("name"),
            service.get("name"),
        )
        if str(value or "").strip()
    }
    allowed_names = {
        service_name,
        f"projects/{project}/locations/{region}/services/{service_name}",
    }
    if not observed_names or not observed_names <= allowed_names:
        raise ValueError("Cloud Run service readback returned another service")


def _require_exact_target_traffic(
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
        service_name=service_name,
    )
    planes, positive = _reconciled_positive_traffic(
        service, label="Cloud Run service"
    )
    if positive != [
        {"revisionName": target_revision, "percent": 100}
    ]:
        raise ValueError("Cloud Run service traffic is not exactly target revision at 100%")
    require_exact_tagged_revision(
        planes,
        label="Cloud Run service",
        tag=_CANDIDATE_TAG,
        revision=target_revision,
        percent=0,
    )


def _validate_old_positive_revisions(
    value: Any,
    *,
    target_revision: str,
    service_before: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(value, list):
        raise ValueError("oldPositiveRevisions must be a list")
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "revisionName",
            "percent",
            "timeoutSeconds",
        }:
            raise ValueError("oldPositiveRevisions entries have an invalid schema")
        revision = str(item.get("revisionName") or "").strip()
        percent = item.get("percent")
        timeout = item.get("timeoutSeconds")
        if not _REVISION_RE.fullmatch(revision) or revision == target_revision:
            raise ValueError("oldPositiveRevisions contains an invalid old revision")
        if isinstance(percent, bool) or not isinstance(percent, int) or not 1 <= percent <= 100:
            raise ValueError("oldPositiveRevisions percent must be between 1 and 100")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
            raise ValueError("oldPositiveRevisions timeoutSeconds must be between 1 and 3600")
        normalized.append(
            {
                "revisionName": revision,
                "percent": percent,
                "timeoutSeconds": timeout,
            }
        )
    if normalized != sorted(
        normalized,
        key=lambda item: (
            item["revisionName"],
            item["percent"],
            item["timeoutSeconds"],
        ),
    ):
        raise ValueError("oldPositiveRevisions must use stable sorted order")
    if len({item["revisionName"] for item in normalized}) != len(normalized):
        raise ValueError("oldPositiveRevisions contains a duplicate revision")
    if not normalized:
        raise ValueError("oldPositiveRevisions must contain the prior positive revision")
    before_planes, before_traffic = _reconciled_positive_traffic(
        service_before, label="serviceBefore"
    )
    require_exact_tagged_revision(
        before_planes,
        label="serviceBefore",
        tag=_CANDIDATE_TAG,
        revision=target_revision,
        percent=0,
    )
    if not before_traffic or sum(item["percent"] for item in before_traffic) != 100:
        raise ValueError("serviceBefore positive traffic must total exactly 100%")
    if any(item["revisionName"] == target_revision for item in before_traffic):
        raise ValueError("target revision must not have positive serviceBefore traffic")
    before_pairs = [
        {"revisionName": item["revisionName"], "percent": item["percent"]}
        for item in normalized
    ]
    if before_pairs != sorted(
        before_traffic,
        key=lambda item: (item["revisionName"], item["percent"]),
    ):
        raise ValueError("oldPositiveRevisions does not match serviceBefore traffic")
    return normalized, max(item["timeoutSeconds"] for item in normalized)


def validate_promotion_receipt(
    *,
    raw_bytes: bytes,
    project: str,
    region: str,
    service: str,
    now: datetime,
) -> dict[str, Any]:
    try:
        receipt = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("promotion receipt is not valid UTF-8 JSON") from exc
    if not isinstance(receipt, dict):
        raise ValueError("promotion receipt must be a JSON object")
    expected = {
        "receiptType": _PROMOTION_RECEIPT_TYPE,
        "project": project,
        "region": region,
        "service": service,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError("promotion receipt identity or type does not match")
    revision = str(receipt.get("targetRevision") or "").strip()
    if not _REVISION_RE.fullmatch(revision):
        raise ValueError("promotion receipt targetRevision is invalid")
    service_before = receipt.get("serviceBefore")
    service_after = receipt.get("serviceAfter")
    if not isinstance(service_before, dict) or not isinstance(service_after, dict):
        raise ValueError("promotion receipt must contain serviceBefore and serviceAfter")
    _require_service_identity(
        service_before,
        project=project,
        region=region,
        service_name=service,
    )
    _require_exact_target_traffic(
        service_after,
        project=project,
        region=region,
        service_name=service,
        target_revision=revision,
    )
    old_positive, derived_timeout = _validate_old_positive_revisions(
        receipt.get("oldPositiveRevisions"),
        target_revision=revision,
        service_before=service_before,
    )
    max_timeout = receipt.get("maxRequestTimeoutSeconds")
    if isinstance(max_timeout, bool) or not isinstance(max_timeout, int):
        raise ValueError("maxRequestTimeoutSeconds must be an integer")
    if max_timeout != derived_timeout:
        raise ValueError("maxRequestTimeoutSeconds does not match oldPositiveRevisions")
    traffic_readback_at = _parse_utc(
        receipt.get("trafficReadbackAt"), field="trafficReadbackAt"
    )
    drain_until = _parse_utc(receipt.get("drainUntil"), field="drainUntil")
    if drain_until != traffic_readback_at + timedelta(seconds=max_timeout):
        raise ValueError("drainUntil must equal trafficReadbackAt plus the maximum timeout")
    if now.astimezone(timezone.utc) < drain_until:
        raise ValueError("promotion drainUntil has not elapsed")
    return {
        "receipt": receipt,
        "receipt_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "target_revision": revision,
        "traffic_readback_at": traffic_readback_at,
        "drain_until": drain_until,
        "max_request_timeout_seconds": max_timeout,
        "old_positive_revisions": old_positive,
        "old_positive_revisions_json": _canonical_json(old_positive),
    }


def _credential_environment(explicit_path: str) -> tuple[dict[str, str], Any]:
    approved = approved_credential_path(explicit_path)
    credentials = service_account.Credentials.from_service_account_file(str(approved))
    return (
        {
            **os.environ,
            "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": str(approved),
            "GOOGLE_APPLICATION_CREDENTIALS": str(approved),
        },
        credentials,
    )


def describe_live_service(
    *, project: str, region: str, service: str, env: dict[str, str]
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "gcloud",
                f"--project={project}",
                "run",
                "services",
                "describe",
                service,
                f"--region={region}",
                "--format=json",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=_GCLOUD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit("live_service_describe_timeout") from exc
    if result.returncode != 0:
        raise SystemExit("live_service_describe_failed")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit("live Cloud Run service describe returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise SystemExit("live Cloud Run service describe did not return an object")
    return value


def render_activation_sql(*, project: str, dataset: str, apply: bool) -> str:
    ledger = f"`{project}.{dataset}.monitor_contract_revision_ledger`"
    registration_predicate = """
monitor_contract_version = 'monitor.v2'
AND registration_source = 'candidate_v2_exact_http_question_sample'
AND sample_endpoint_class IN ('ask', 'ask_stream')
AND REGEXP_CONTAINS(sample_cloud_trace, r'^projects/@PROJECT@/traces/[0-9a-f]{32}$')
AND REGEXP_CONTAINS(sample_cloud_span_id, r'^[0-9a-f]{16}$')
AND sample_correlation_hash = TO_HEX(SHA256(CONCAT(
  revision_name, '|', sample_cloud_trace, '|', sample_cloud_span_id, '|',
  sample_endpoint_class
)))
""".strip().replace("@PROJECT@", project)
    activation_match = f"""
activation_source = '{_ACTIVATION_SOURCE}'
AND promotion_receipt_type = '{_PROMOTION_RECEIPT_TYPE}'
AND promotion_receipt_sha256 = @promotion_receipt_sha256
AND promotion_project = @promotion_project
AND promotion_region = @promotion_region
AND promotion_service = @promotion_service
AND promotion_target_revision = @target_revision
AND promotion_traffic_readback_at = @promotion_traffic_readback_at
AND promotion_max_request_timeout_seconds = @promotion_max_timeout_seconds
AND promotion_drain_until = @promotion_drain_until
AND promotion_old_positive_revisions_json = @old_positive_revisions_json
AND enforcement_start >= promotion_drain_until
AND REGEXP_CONTAINS(activation_service_readback_sha256, r'^[0-9a-f]{{64}}$')
""".strip()
    assertions = f"""
ASSERT CURRENT_TIMESTAMP() >= @promotion_drain_until
  AS 'promotion drainUntil has not elapsed at BigQuery commit time';
ASSERT (
  SELECT COUNT(*)
  FROM {ledger}
  WHERE revision_name = @target_revision
    AND {registration_predicate}
) = 1 AS 'target revision is not uniquely registered from an exact v2 candidate proof';
ASSERT NOT EXISTS (
  SELECT 1
  FROM {ledger}
  WHERE revision_name = @target_revision
    AND (
      enforcement_start IS NULL
      AND (
        activation_source IS NOT NULL
        OR promotion_receipt_type IS NOT NULL
        OR promotion_receipt_sha256 IS NOT NULL
        OR promotion_project IS NOT NULL
        OR promotion_region IS NOT NULL
        OR promotion_service IS NOT NULL
        OR promotion_target_revision IS NOT NULL
        OR promotion_traffic_readback_at IS NOT NULL
        OR promotion_max_request_timeout_seconds IS NOT NULL
        OR promotion_drain_until IS NOT NULL
        OR promotion_old_positive_revisions_json IS NOT NULL
        OR activation_service_readback_sha256 IS NOT NULL
      )
    )
) AS 'monitor.v2 enforcement has a partial activation';
ASSERT NOT EXISTS (
  SELECT 1
  FROM {ledger}
  WHERE revision_name = @target_revision
    AND enforcement_start IS NOT NULL
    AND NOT ({activation_match})
) AS 'monitor.v2 enforcement conflicts with this promotion receipt';
""".strip()
    plan = f"""
SELECT
  revision_name,
  monitor_contract_version,
  enforcement_start IS NOT NULL AS activated,
  COALESCE(
    promotion_receipt_sha256,
    @promotion_receipt_sha256
  ) AS promotion_receipt_sha256,
  COALESCE(
    activation_service_readback_sha256,
    @activation_service_readback_sha256
  ) AS activation_service_readback_sha256,
  FALSE AS activation_write_performed
FROM {ledger}
WHERE revision_name = @target_revision
  AND {registration_predicate};
""".strip()
    if not apply:
        return "\n".join((assertions, plan))
    mutation = f"""
DECLARE activation_write_performed BOOL DEFAULT FALSE;
BEGIN TRANSACTION;
{assertions}
UPDATE {ledger}
SET
  enforcement_start = CURRENT_TIMESTAMP(),
  activation_source = '{_ACTIVATION_SOURCE}',
  promotion_receipt_type = '{_PROMOTION_RECEIPT_TYPE}',
  promotion_receipt_sha256 = @promotion_receipt_sha256,
  promotion_project = @promotion_project,
  promotion_region = @promotion_region,
  promotion_service = @promotion_service,
  promotion_target_revision = @target_revision,
  promotion_traffic_readback_at = @promotion_traffic_readback_at,
  promotion_max_request_timeout_seconds = @promotion_max_timeout_seconds,
  promotion_drain_until = @promotion_drain_until,
  promotion_old_positive_revisions_json = @old_positive_revisions_json,
  activation_service_readback_sha256 = @activation_service_readback_sha256,
  updated_at = CURRENT_TIMESTAMP()
WHERE revision_name = @target_revision
  AND enforcement_start IS NULL
  AND activation_source IS NULL
  AND promotion_receipt_type IS NULL
  AND promotion_receipt_sha256 IS NULL
  AND promotion_project IS NULL
  AND promotion_region IS NULL
  AND promotion_service IS NULL
  AND promotion_target_revision IS NULL
  AND promotion_traffic_readback_at IS NULL
  AND promotion_max_request_timeout_seconds IS NULL
  AND promotion_drain_until IS NULL
  AND promotion_old_positive_revisions_json IS NULL
  AND activation_service_readback_sha256 IS NULL;
SET activation_write_performed = (@@row_count = 1);
ASSERT activation_write_performed OR EXISTS (
  SELECT 1
  FROM {ledger}
  WHERE revision_name = @target_revision
    AND enforcement_start IS NOT NULL
    AND {activation_match}
) AS 'enforcement activation is neither newly written nor exactly recoverable';
COMMIT TRANSACTION;
SELECT
  revision_name,
  monitor_contract_version,
  enforcement_start,
  activation_source,
  promotion_receipt_type,
  promotion_receipt_sha256,
  promotion_project,
  promotion_region,
  promotion_service,
  promotion_target_revision,
  promotion_traffic_readback_at,
  promotion_max_request_timeout_seconds,
  promotion_drain_until,
  promotion_old_positive_revisions_json,
  activation_service_readback_sha256,
  TRUE AS activated,
  activation_write_performed
FROM {ledger}
WHERE revision_name = @target_revision
  AND activation_source = '{_ACTIVATION_SOURCE}'
  AND {activation_match};
""".strip()
    return mutation


def _required_confirmation(
    *, project: str, region: str, service: str, revision: str, receipt_sha256: str
) -> str:
    return (
        f"projects/{project}/locations/{region}/services/{service}/"
        f"monitor-v2-enforcement:{revision}:{receipt_sha256}"
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _write_new_receipt(path: Path, output: dict[str, Any]) -> None:
    """Publish a complete receipt without exposing a partial destination file."""
    encoded = (
        json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(
        prefix=path.name + ".pending-",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise SystemExit("activation receipt appeared during publication") from exc
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Activate strict unknown-revision checks after drained LCS promotion"
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", default="us-central1")
    parser.add_argument("--service", default="lcs-rag-app")
    parser.add_argument("--dataset", default="oura_navi_monitor")
    parser.add_argument("--location", default="US")
    parser.add_argument("--promotion-receipt", required=True)
    parser.add_argument("--receipt-output")
    parser.add_argument("--confirm-activate")
    parser.add_argument("--maximum-bytes-billed", type=int, default=64_000_000)
    parser.add_argument("--credential-file", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not _PROJECT_RE.fullmatch(args.project):
        raise SystemExit("--project is invalid")
    if not _DATASET_RE.fullmatch(args.dataset):
        raise SystemExit("--dataset is invalid")
    receipt_path = Path(args.promotion_receipt)
    if not receipt_path.is_file():
        raise SystemExit("--promotion-receipt must be an existing file")
    if receipt_path.stat().st_size > _MAX_RECEIPT_BYTES:
        raise SystemExit("--promotion-receipt exceeds the 2 MB safety limit")
    if args.maximum_bytes_billed <= 0:
        raise SystemExit("--maximum-bytes-billed must be positive")
    proof = validate_promotion_receipt(
        raw_bytes=receipt_path.read_bytes(),
        project=args.project,
        region=args.region,
        service=args.service,
        now=datetime.now(timezone.utc),
    )
    confirmation = _required_confirmation(
        project=args.project,
        region=args.region,
        service=args.service,
        revision=proof["target_revision"],
        receipt_sha256=proof["receipt_sha256"],
    )
    if args.apply:
        if args.confirm_activate != confirmation:
            raise SystemExit(f"--confirm-activate must equal {confirmation}")
        if not args.receipt_output:
            raise SystemExit("--receipt-output is required with --apply")
        output_path = Path(args.receipt_output)
        if not output_path.parent.is_dir():
            raise SystemExit("activation receipt parent directory does not exist")

    env, credentials = _credential_environment(args.credential_file)
    live_service = describe_live_service(
        project=args.project,
        region=args.region,
        service=args.service,
        env=env,
    )
    _require_exact_target_traffic(
        live_service,
        project=args.project,
        region=args.region,
        service_name=args.service,
        target_revision=proof["target_revision"],
    )
    live_service_sha256 = hashlib.sha256(
        _canonical_json(live_service).encode("utf-8")
    ).hexdigest()

    client = bigquery.Client(project=args.project, credentials=credentials)
    parameters = [
        bigquery.ScalarQueryParameter(
            "promotion_receipt_sha256", "STRING", proof["receipt_sha256"]
        ),
        bigquery.ScalarQueryParameter(
            "activation_service_readback_sha256", "STRING", live_service_sha256
        ),
        bigquery.ScalarQueryParameter("promotion_project", "STRING", args.project),
        bigquery.ScalarQueryParameter("promotion_region", "STRING", args.region),
        bigquery.ScalarQueryParameter("promotion_service", "STRING", args.service),
        bigquery.ScalarQueryParameter(
            "target_revision", "STRING", proof["target_revision"]
        ),
        bigquery.ScalarQueryParameter(
            "promotion_traffic_readback_at",
            "TIMESTAMP",
            proof["traffic_readback_at"],
        ),
        bigquery.ScalarQueryParameter(
            "promotion_max_timeout_seconds",
            "INT64",
            proof["max_request_timeout_seconds"],
        ),
        bigquery.ScalarQueryParameter(
            "promotion_drain_until", "TIMESTAMP", proof["drain_until"]
        ),
        bigquery.ScalarQueryParameter(
            "old_positive_revisions_json",
            "STRING",
            proof["old_positive_revisions_json"],
        ),
    ]
    config = bigquery.QueryJobConfig(
        query_parameters=parameters,
        maximum_bytes_billed=args.maximum_bytes_billed,
        use_query_cache=False,
    )
    rows = list(
        client.query(
            render_activation_sql(
                project=args.project,
                dataset=args.dataset,
                apply=args.apply,
            ),
            job_config=config,
            location=args.location,
        ).result()
    )
    if len(rows) != 1:
        raise SystemExit("enforcement activation query did not return one exact row")
    readback = _json_value(dict(rows[0].items()))
    if readback.get("promotion_receipt_sha256") != proof["receipt_sha256"]:
        raise SystemExit("activation readback is not bound to the promotion receipt")
    write_performed = readback.pop("activation_write_performed", False) is True
    stored_live_service_sha256 = str(
        readback.get("activation_service_readback_sha256") or ""
    )
    if not re.fullmatch(r"[0-9a-f]{64}", stored_live_service_sha256):
        raise SystemExit("activation readback has no original live service hash")
    if write_performed and stored_live_service_sha256 != live_service_sha256:
        raise SystemExit("new activation readback is not bound to this live service")
    if args.apply and readback.get("activated") is not True:
        raise SystemExit("enforcement activation was not committed")

    output = {
        "receiptType": _ACTIVATION_RECEIPT_TYPE,
        "mode": "apply" if args.apply else "plan",
        "project": args.project,
        "region": args.region,
        "service": args.service,
        "dataset": args.dataset,
        "targetRevision": proof["target_revision"],
        "promotionReceiptSha256": proof["receipt_sha256"],
        "trafficReadbackAt": _json_value(proof["traffic_readback_at"]),
        "drainUntil": _json_value(proof["drain_until"]),
        "oldPositiveRevisions": proof["old_positive_revisions"],
        "liveServiceReadbackSha256": stored_live_service_sha256,
        "recoveryLiveServiceReadbackSha256": live_service_sha256,
        "activationRecovered": args.apply and not write_performed,
        "requiredConfirmation": confirmation,
        "ledgerReadback": readback,
        "capturedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if args.apply:
        output_path = Path(args.receipt_output)
        if output_path.exists():
            try:
                existing = json.loads(output_path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SystemExit("existing activation receipt is invalid") from exc
            immutable_keys = (
                "receiptType",
                "mode",
                "project",
                "region",
                "service",
                "dataset",
                "targetRevision",
                "promotionReceiptSha256",
                "trafficReadbackAt",
                "drainUntil",
                "oldPositiveRevisions",
                "liveServiceReadbackSha256",
                "requiredConfirmation",
                "ledgerReadback",
            )
            if not isinstance(existing, dict) or any(
                existing.get(key) != output.get(key) for key in immutable_keys
            ) or not existing.get("capturedAt"):
                raise SystemExit("existing activation receipt conflicts with ledger authority")
            output = existing
        else:
            _write_new_receipt(output_path, output)
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
