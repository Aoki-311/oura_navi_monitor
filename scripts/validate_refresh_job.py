#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Iterable


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def _first(payload: dict[str, Any], *keys: str) -> Any:
    for item in _walk(payload):
        for key in keys:
            if key in item and item[key] not in (None, ""):
                return item[key]
    return None


def _container(payload: dict[str, Any]) -> dict[str, Any]:
    for item in _walk(payload):
        containers = item.get("containers")
        if isinstance(containers, list) and containers:
            if len(containers) != 1 or not isinstance(containers[0], dict):
                raise ValueError("refresh Job must have exactly one container")
            return containers[0]
    raise ValueError("refresh Job has no container")


def _integer(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"refresh Job {label} is missing") from exc


def _timeout_seconds(value: Any) -> int:
    text = str(value or "").strip().lower()
    if text.endswith("s"):
        text = text[:-1]
    return _integer(text, "timeout")


def validate_refresh_job(
    payload: dict[str, Any],
    *,
    expected_image: str,
    expected_service_account: str,
    project_id: str,
    dataset_id: str,
    location: str,
    source_service: str,
    timeout_minutes: int,
) -> dict[str, Any]:
    container = _container(payload)
    if container.get("image") != expected_image:
        raise ValueError("refresh Job image does not match the immutable candidate digest")
    service_account = str(
        _first(payload, "serviceAccount", "serviceAccountName") or ""
    )
    if service_account != expected_service_account:
        raise ValueError("refresh Job service account does not match the approved identity")

    command = list(container.get("command") or [])
    args = list(container.get("args") or [])
    expected_args = [
        "-m",
        "app.jobs.refresh_analytics",
        "--apply",
        "--trigger-source",
        "scheduler_three_hour",
    ]
    if command != ["python"] or args != expected_args:
        raise ValueError("refresh Job command is not the governed scheduler owner")

    env_rows = container.get("env") or []
    env = {
        str(item.get("name") or ""): str(item.get("value") or "")
        for item in env_rows
        if isinstance(item, dict)
    }
    expected_env = {
        "MONITOR_PROJECT_ID": project_id,
        "MONITOR_BQ_DATASET": dataset_id,
        "MONITOR_BQ_LOCATION": location,
        "MONITOR_SOURCE_SERVICE": source_service,
    }
    for key, value in expected_env.items():
        if env.get(key) != value:
            raise ValueError(f"refresh Job {key} does not match the release inventory")
    analytics_start = str(env.get("MONITOR_ANALYTICS_START_AT") or "")
    if not analytics_start:
        raise ValueError("refresh Job analytics start is empty")

    task_count = _integer(_first(payload, "taskCount"), "task count")
    parallelism = _integer(_first(payload, "parallelism"), "parallelism")
    max_retries = _integer(_first(payload, "maxRetries"), "max retries")
    timeout_seconds = _timeout_seconds(
        _first(payload, "timeout", "timeoutSeconds")
    )
    if task_count != 1 or parallelism != 1:
        raise ValueError("refresh Job must be a single-task single-writer")
    if max_retries != 1:
        raise ValueError("refresh Job max retries must equal one")
    if timeout_seconds != int(timeout_minutes) * 60:
        raise ValueError("refresh Job timeout does not match the governed policy")

    return {
        "image": expected_image,
        "serviceAccount": service_account,
        "command": command,
        "args": args,
        "environment": expected_env | {"MONITOR_ANALYTICS_START_AT": analytics_start},
        "taskCount": task_count,
        "parallelism": parallelism,
        "maxRetries": max_retries,
        "timeoutSeconds": timeout_seconds,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the canonical refresh Job")
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--expected-service-account", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--source-service", required=True)
    parser.add_argument("--timeout-minutes", type=int, required=True)
    args = parser.parse_args()
    raw = os.environ.get("JOB_DESCRIPTION_JSON", "")
    payload = json.loads(raw)
    normalized = validate_refresh_job(
        payload,
        expected_image=args.expected_image,
        expected_service_account=args.expected_service_account,
        project_id=args.project,
        dataset_id=args.dataset,
        location=args.location,
        source_service=args.source_service,
        timeout_minutes=args.timeout_minutes,
    )
    print(json.dumps(normalized, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
