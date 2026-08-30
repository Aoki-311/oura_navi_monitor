#!/usr/bin/env python3
"""Validate the three-window gate and exact Scheduler -> Run Job provenance."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.scheduler_execution_provenance import validate_provenance
except ModuleNotFoundError:
    from scheduler_execution_provenance import validate_provenance


def _load(path: str, label: str) -> list[dict[str, Any]]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"{label} inventory is not a list")
    return value


def _parse(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def validate(args: argparse.Namespace) -> str:
    runs = _load(args.runs, "canonical run")
    executions = _load(args.executions, "Cloud Run execution")
    attempts = _load(args.attempts, "Scheduler attempt")
    audits = _load(args.audits, "RunJob audit")
    if len(runs) < 3:
        raise ValueError("three successful canonical runs are required")
    execution_ids = {
        str(row.get("execution_id") or "").rsplit("/", 1)[-1] for row in runs
    }
    windows = {str(row.get("window_end") or "") for row in runs}
    if len(execution_ids) < 3 or len(windows) < 3:
        raise ValueError(
            "three successful canonical executions with distinct windows are required"
        )
    started = [_parse(row["started_at"]) for row in runs]
    span = int((max(started) - min(started)).total_seconds() // 60)
    if span < args.minimum_span_minutes:
        raise ValueError("canonical executions have not covered two governed cadence intervals")
    freshness_values = [
        int(row["freshness_minutes"])
        for row in runs
        if row.get("freshness_minutes") is not None
    ]
    if not freshness_values:
        raise ValueError("canonical published watermark has no freshness evidence")
    freshness = min(freshness_values)
    if freshness < 0 or freshness > args.stale_after_minutes:
        raise ValueError("canonical published watermark is not currently fresh")
    matched = validate_provenance(
        runs=runs,
        executions=executions,
        attempts=attempts,
        audits=audits,
        project=args.project,
        region=args.region,
        job=args.job,
        scheduler=args.scheduler,
        scheduler_service_account=args.scheduler_service_account,
        expected_job_uri=args.expected_job_uri,
        expected_image=args.expected_image,
        expected_job_service_account=args.expected_job_service_account,
    )
    latest = max(_parse(row["window_end"]) for row in runs).isoformat().replace(
        "+00:00", "Z"
    )
    return (
        f"runs={len(runs)} executions={len(execution_ids)} windows={len(windows)} "
        f"span_minutes={span} freshness_minutes={freshness} scheduler_proven={matched} "
        f"latest_window_end={latest}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("runs", "executions", "attempts", "audits"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--minimum-span-minutes", required=True, type=int)
    parser.add_argument("--stale-after-minutes", required=True, type=int)
    parser.add_argument("--expected-job-uri", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--expected-job-service-account", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--scheduler", required=True)
    parser.add_argument("--scheduler-service-account", required=True)
    args = parser.parse_args()
    try:
        result = validate(args)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit("Scheduler execution provenance failed: " + str(exc)) from exc
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
