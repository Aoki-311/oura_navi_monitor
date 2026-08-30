#!/usr/bin/env python3
"""Prove a candidate deploy did not mutate Cloud Run service access policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ACCESS_ANNOTATIONS = (
    "run.googleapis.com/ingress",
    "run.googleapis.com/custom-audiences",
    "run.googleapis.com/invoker-iam-disabled",
)


class ReconciliationPending(ValueError):
    """The Cloud Run controller has not observed the current generation yet."""


def _load(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("service access evidence is not an object")
    return value


def _access(service: dict[str, Any]) -> dict[str, Any]:
    metadata = service.get("metadata") or {}
    annotations = metadata.get("annotations") or {}
    spec = service.get("spec") or {}
    if not all(isinstance(value, dict) for value in (metadata, annotations, spec)):
        raise ValueError("service access configuration has an invalid shape")
    return {
        "annotations": {key: annotations.get(key) for key in ACCESS_ANNOTATIONS},
        "ingress": service.get("ingress", spec.get("ingress")),
        "customAudiences": service.get(
            "customAudiences", spec.get("customAudiences")
        ),
        "invokerIamDisabled": service.get(
            "invokerIamDisabled", spec.get("invokerIamDisabled")
        ),
        "defaultUriDisabled": service.get(
            "defaultUriDisabled", spec.get("defaultUriDisabled")
        ),
    }


def _iam(policy: dict[str, Any]) -> dict[str, Any]:
    bindings = policy.get("bindings") or []
    if not isinstance(bindings, list):
        raise ValueError("service IAM bindings are not a list")
    normalized = []
    for binding in bindings:
        if not isinstance(binding, dict) or not isinstance(binding.get("members") or [], list):
            raise ValueError("service IAM binding has an invalid shape")
        normalized.append(
            {
                "role": binding.get("role"),
                "members": sorted(binding.get("members") or []),
                "condition": binding.get("condition"),
            }
        )
    normalized.sort(key=lambda row: json.dumps(row, sort_keys=True))
    audits = policy.get("auditConfigs") or []
    if not isinstance(audits, list):
        raise ValueError("service IAM audit configuration has an invalid shape")
    return {
        "version": policy.get("version"),
        "bindings": normalized,
        "auditConfigs": sorted(
            audits, key=lambda row: json.dumps(row, sort_keys=True)
        ),
    }


def _positive_generation(value: Any, *, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(label + " is not a positive integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.isdigit():
        parsed = int(value)
    else:
        raise ValueError(label + " is not a positive integer")
    if parsed <= 0:
        raise ValueError(label + " is not a positive integer")
    return parsed


def require_reconciled_ready(resource: dict[str, Any], *, label: str) -> None:
    metadata = resource.get("metadata")
    status = resource.get("status")
    if not isinstance(metadata, dict) or not isinstance(status, dict):
        raise ValueError(label + " metadata/status has an invalid shape")
    generation = _positive_generation(
        metadata.get("generation"), label=label + " metadata generation"
    )
    try:
        observed_generation = _positive_generation(
            status.get("observedGeneration"),
            label=label + " observed generation",
        )
    except ValueError as exc:
        raise ReconciliationPending(str(exc)) from exc
    if observed_generation != generation:
        raise ReconciliationPending(label + " generation has not been fully observed")

    conditions = status.get("conditions")
    if conditions is None:
        raise ReconciliationPending(label + " Ready condition is not available yet")
    if not isinstance(conditions, list):
        raise ValueError(label + " conditions are not a list")
    ready = [
        condition
        for condition in conditions
        if isinstance(condition, dict) and condition.get("type") == "Ready"
    ]
    if not ready:
        raise ReconciliationPending(label + " Ready condition is not available yet")
    if len(ready) != 1:
        raise ValueError(label + " Ready condition must occur exactly once")
    ready_status = str(ready[0].get("status") or "").lower()
    if ready_status == "true":
        return
    if ready_status in {"", "unknown"}:
        raise ReconciliationPending(label + " Ready condition is still Unknown")
    if ready_status == "false":
        reason = str(ready[0].get("reason") or "unspecified")
        message = str(ready[0].get("message") or "")
        detail = f" reason={reason}"
        if message:
            detail += f" message={message}"
        raise ValueError(label + " reconciliation failed:" + detail)
    raise ValueError(label + " Ready condition has an invalid status")


def traffic_planes(
    service: dict[str, Any], *, label: str
) -> dict[str, list[dict[str, Any]]]:
    """Return reconciliable desired/observed traffic from one service readback."""

    metadata = service.get("metadata")
    spec = service.get("spec")
    status = service.get("status")
    if not all(isinstance(value, dict) for value in (metadata, spec, status)):
        raise ValueError(label + " metadata/spec/status has an invalid shape")
    generation = _positive_generation(
        metadata.get("generation"), label=label + " metadata generation"
    )
    observed_generation = _positive_generation(
        status.get("observedGeneration"),
        label=label + " observed generation",
    )
    if observed_generation != generation:
        raise ValueError(label + " generation has not been fully observed")

    latest_ready_revision = status.get("latestReadyRevisionName")
    if latest_ready_revision is not None and (
        not isinstance(latest_ready_revision, str) or not latest_ready_revision
    ):
        raise ValueError(label + " latest Ready revision has an invalid shape")

    normalized: dict[str, list[dict[str, Any]]] = {}
    for plane, container in (("spec", spec), ("status", status)):
        traffic = container.get("traffic")
        if not isinstance(traffic, list) or not traffic:
            raise ValueError(label + f" {plane} traffic is not a non-empty list")
        rows: list[dict[str, Any]] = []
        for row in traffic:
            if not isinstance(row, dict):
                raise ValueError(label + f" {plane} traffic entry is not an object")
            tag = row.get("tag")
            if tag is not None and (not isinstance(tag, str) or not tag):
                raise ValueError(label + f" {plane} traffic tag has an invalid shape")
            percent = row.get("percent")
            if "percent" not in row and tag:
                percent = 0
            if isinstance(percent, bool) or not isinstance(percent, int):
                raise ValueError(label + f" {plane} traffic percentage is not an integer")
            if not 0 <= percent <= 100:
                raise ValueError(label + f" {plane} traffic percentage is outside 0..100")

            explicit_revision = row.get("revisionName")
            if explicit_revision is not None and (
                not isinstance(explicit_revision, str) or not explicit_revision
            ):
                raise ValueError(label + f" {plane} traffic revision has an invalid shape")
            latest_revision = row.get("latestRevision", False)
            if not isinstance(latest_revision, bool):
                raise ValueError(
                    label + f" {plane} latestRevision flag is not a boolean"
                )
            revision = explicit_revision
            if revision is None and plane == "spec" and latest_revision:
                if not isinstance(latest_ready_revision, str) or not latest_ready_revision:
                    raise ValueError(
                        label + " spec latestRevision traffic cannot be resolved exactly"
                    )
                revision = latest_ready_revision
            if not isinstance(revision, str) or not revision:
                raise ValueError(label + f" {plane} traffic has no exact revision")
            url = row.get("url")
            if url is not None and not isinstance(url, str):
                raise ValueError(label + f" {plane} traffic URL is not a string")
            rows.append(
                {
                    "revisionName": revision,
                    "explicitRevisionName": explicit_revision,
                    "latestRevision": latest_revision,
                    "percent": percent,
                    "tag": tag,
                    "url": url,
                }
            )
        normalized[plane] = rows
    return normalized


def positive_traffic(
    rows: list[dict[str, Any]], *, label: str
) -> list[dict[str, Any]]:
    normalized = [
        {"revisionName": row["revisionName"], "percent": row["percent"]}
        for row in rows
        if row["percent"] > 0
    ]
    normalized.sort(key=lambda row: (row["revisionName"], row["percent"]))
    if sum(row["percent"] for row in normalized) != 100:
        raise ValueError(label + " positive traffic does not total 100 percent")
    return normalized


def require_reconciled_traffic(
    planes: dict[str, list[dict[str, Any]]], *, label: str
) -> list[dict[str, Any]]:
    desired = positive_traffic(planes["spec"], label=label + " desired")
    observed = positive_traffic(planes["status"], label=label + " observed")
    if desired != observed:
        raise ValueError(label + " desired and observed positive traffic disagree")
    return desired


def require_exact_tagged_revision(
    planes: dict[str, list[dict[str, Any]]],
    *,
    label: str,
    tag: str,
    revision: str,
    percent: int = 0,
) -> dict[str, dict[str, Any]]:
    """Require one explicitly pinned tagged revision in both traffic planes."""

    if not isinstance(tag, str) or not tag:
        raise ValueError(label + " traffic tag is invalid")
    if not isinstance(revision, str) or not revision:
        raise ValueError(label + " traffic revision is invalid")
    if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100:
        raise ValueError(label + " tagged traffic percentage is invalid")
    matched: dict[str, dict[str, Any]] = {}
    for plane in ("spec", "status"):
        display_plane = "desired" if plane == "spec" else "observed"
        tagged = [row for row in planes[plane] if row.get("tag") == tag]
        if len(tagged) != 1:
            raise ValueError(
                label + f" {display_plane} traffic tag must resolve exactly once"
            )
        row = tagged[0]
        if row.get("revisionName") != revision:
            raise ValueError(
                label + f" {display_plane} traffic tag points to a stale revision"
            )
        if row.get("explicitRevisionName") != revision or row.get("latestRevision") is True:
            raise ValueError(
                label
                + f" {display_plane} traffic tag is not pinned to the exact revision"
            )
        if row.get("percent") != percent:
            if percent == 0:
                raise ValueError(
                    label
                    + f" {display_plane} traffic unexpectedly gives the revision production traffic"
                )
            raise ValueError(
                label
                + f" {display_plane} tagged traffic percentage is not {percent}"
            )
        matched[plane] = row
    return matched


def verify(before_service: dict[str, Any], after_service: dict[str, Any], before_iam: dict[str, Any], after_iam: dict[str, Any]) -> None:
    if _access(before_service) != _access(after_service):
        raise ValueError("candidate deploy changed Cloud Run access configuration")
    if _iam(before_iam) != _iam(after_iam):
        raise ValueError("candidate deploy changed Cloud Run service IAM policy")
    require_reconciled_ready(before_service, label="predeploy service")
    require_reconciled_ready(after_service, label="candidate service")
    before_planes = traffic_planes(before_service, label="predeploy service")
    after_planes = traffic_planes(after_service, label="candidate service")
    before_positive = require_reconciled_traffic(
        before_planes, label="predeploy service"
    )
    after_positive = require_reconciled_traffic(
        after_planes, label="candidate service"
    )
    if before_positive != after_positive:
        raise ValueError("candidate deploy changed positive Cloud Run traffic")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before-service", required=True)
    parser.add_argument("--after-service", required=True)
    parser.add_argument("--before-iam", required=True)
    parser.add_argument("--after-iam", required=True)
    args = parser.parse_args()
    try:
        verify(
            _load(args.before_service),
            _load(args.after_service),
            _load(args.before_iam),
            _load(args.after_iam),
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit("candidate_access_contract_invalid: " + str(exc)) from exc
    print("candidate_access_contract=unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
