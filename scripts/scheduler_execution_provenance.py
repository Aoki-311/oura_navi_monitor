#!/usr/bin/env python3
"""Exact Scheduler -> RunJob audit -> Cloud Run execution provenance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from scripts.validate_refresh_job import validate_refresh_execution
except ModuleNotFoundError:
    from validate_refresh_job import validate_refresh_execution


def _load(path: str) -> list[dict[str, Any]]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"{path} must contain a JSON object list")
    return value


def _short_name(value: Any) -> str:
    return str(value or "").strip().rsplit("/", 1)[-1]


def _execution_creator(execution: dict[str, Any]) -> str:
    metadata = execution.get("metadata") or {}
    annotations = metadata.get("annotations") or {}
    labels = metadata.get("labels") or {}
    return str(
        execution.get("creator")
        or metadata.get("creator")
        or annotations.get("run.googleapis.com/creator")
        or labels.get("run.googleapis.com/creator")
        or ""
    ).strip()


def _audit_execution_name(entry: dict[str, Any]) -> str:
    proto = entry.get("protoPayload") or {}
    response = proto.get("response") or {}
    metadata = response.get("metadata") or {}
    completed = response.get("response") or {}
    candidates = {
        metadata.get("name"),
        metadata.get("target"),
        completed.get("name"),
    }
    names = {
        _short_name(value)
        for value in candidates
        if str(value or "").strip()
        and "/executions/" in str(value)
    }
    if len(names) != 1:
        raise ValueError(
            "RunJob audit must identify exactly one consistent Cloud Run execution"
        )
    return next(iter(names))


def _audit_succeeded(proto: dict[str, Any]) -> bool:
    status = proto.get("status")
    if status in (None, {}):
        return True
    if not isinstance(status, dict):
        return False
    try:
        return int(status.get("code") or 0) == 0
    except (TypeError, ValueError):
        return False


def validate_provenance(
    *,
    runs: list[dict[str, Any]],
    executions: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    audits: list[dict[str, Any]],
    project: str,
    region: str,
    job: str,
    scheduler: str,
    scheduler_service_account: str,
    expected_job_uri: str,
    expected_image: str,
    expected_job_service_account: str,
) -> int:
    execution_ids = {_short_name(row.get("execution_id")) for row in runs}
    if "" in execution_ids or len(execution_ids) < 3:
        raise ValueError("three exact Scheduler execution identities are required")
    execution_by_id: dict[str, dict[str, Any]] = {}
    for payload in executions:
        execution_id = _short_name(payload.get("name") or (payload.get("metadata") or {}).get("name"))
        if execution_id:
            if execution_id in execution_by_id:
                raise ValueError("Cloud Run execution inventory has a duplicate identity")
            execution_by_id[execution_id] = payload

    successful_attempt_ids: set[str] = set()
    for entry in attempts:
        payload = entry.get("jsonPayload") or {}
        resource = entry.get("resource") or {}
        labels = resource.get("labels") or {}
        job_name = str(payload.get("jobName") or "")
        target = str(payload.get("url") or payload.get("targetUrl") or "")
        if (
            str(resource.get("type") or "") != "cloud_scheduler_job"
            or str(labels.get("project_id") or "") != project
            or str(labels.get("location_id") or "") != region
            or str(labels.get("job_id") or "") != scheduler
            or job_name
            != f"projects/{project}/locations/{region}/jobs/{scheduler}"
            or target != expected_job_uri
        ):
            continue
        status = payload.get("status")
        if isinstance(status, dict):
            try:
                ok = "code" in status and int(status.get("code")) == 0
            except (TypeError, ValueError):
                ok = False
        else:
            ok = str(status or "").upper() in {"OK", "SUCCESS"}
        if ok and str(entry.get("severity") or "").upper() in {
            "DEFAULT",
            "INFO",
            "NOTICE",
        }:
            attempt_id = str(entry.get("insertId") or "").strip()
            timestamp = str(entry.get("timestamp") or "").strip()
            if not attempt_id or not timestamp:
                raise ValueError(
                    "successful Scheduler attempt has no unique log identity"
                )
            successful_attempt_ids.add(attempt_id)
    if len(successful_attempt_ids) < 3:
        raise ValueError("three unique successful exact Scheduler attempts are required")

    expected_job_resources = {
        f"projects/{project}/locations/{region}/jobs/{job}",
        f"namespaces/{project}/jobs/{job}",
    }
    expected_methods = {
        "/Jobs.RunJob",
        "google.cloud.run.v2.Jobs.RunJob",
    }
    audits_by_execution: dict[str, list[dict[str, Any]]] = {}
    for entry in audits:
        proto = entry.get("protoPayload") or {}
        method = str(proto.get("methodName") or "")
        principal = str(
            (proto.get("authenticationInfo") or {}).get("principalEmail") or ""
        )
        resource = str(proto.get("resourceName") or "")
        if (
            str(proto.get("serviceName") or "") != "run.googleapis.com"
            or method not in expected_methods
            or principal != scheduler_service_account
            or resource not in expected_job_resources
            or not _audit_succeeded(proto)
        ):
            continue
        execution_name = _audit_execution_name(entry)
        audits_by_execution.setdefault(execution_name, []).append(entry)

    matched = 0
    for execution_id in sorted(execution_ids):
        execution = execution_by_id.get(execution_id)
        if execution is None:
            raise ValueError("pipeline run has no matching Cloud Run execution: " + execution_id)
        creator = _execution_creator(execution)
        if creator != scheduler_service_account:
            raise ValueError(
                "Cloud Run execution creator is not the Scheduler OAuth identity: "
                + execution_id
            )
        validate_refresh_execution(
            execution,
            expected_image=expected_image,
            expected_service_account=expected_job_service_account,
            require_terminal_success=True,
        )
        exact_audits = audits_by_execution.get(execution_id, [])
        if len(exact_audits) != 1:
            raise ValueError(
                "Cloud Run execution has no unique exact Scheduler RunJob audit: "
                + execution_id
            )
        matched += 1
    return matched


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("runs", "executions", "attempts", "audits"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--scheduler", required=True)
    parser.add_argument("--scheduler-service-account", required=True)
    parser.add_argument("--expected-job-uri", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--expected-job-service-account", required=True)
    args = parser.parse_args()
    try:
        matched = validate_provenance(
            runs=_load(args.runs),
            executions=_load(args.executions),
            attempts=_load(args.attempts),
            audits=_load(args.audits),
            project=args.project,
            region=args.region,
            job=args.job,
            scheduler=args.scheduler,
            scheduler_service_account=args.scheduler_service_account,
            expected_job_uri=args.expected_job_uri,
            expected_image=args.expected_image,
            expected_job_service_account=args.expected_job_service_account,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise SystemExit("scheduler_execution_provenance_invalid: " + str(exc)) from exc
    print(json.dumps({"matched": matched}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
