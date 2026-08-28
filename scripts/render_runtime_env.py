#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


_UTC_SECOND_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def render_refresh_env(
    *,
    source: Path,
    output: Path,
    analytics_start_at: str,
    project_id: str,
    dataset_id: str,
    location: str,
    source_service: str,
) -> None:
    start = str(analytics_start_at or "").strip()
    if not _UTC_SECOND_RE.fullmatch(start):
        raise ValueError("exact UTC analytics start is required")
    text = source.read_text(encoding="utf-8").rstrip() + "\n"
    replacements = {
        "MONITOR_PROJECT_ID": str(project_id or "").strip(),
        "MONITOR_BQ_DATASET": str(dataset_id or "").strip(),
        "MONITOR_BQ_LOCATION": str(location or "").strip(),
        "MONITOR_SOURCE_SERVICE": str(source_service or "").strip(),
        "MONITOR_ANALYTICS_START_AT": start,
    }
    if any(not value for value in replacements.values()):
        raise ValueError("refresh runtime identity values cannot be empty")
    rendered = text
    for key, value in replacements.items():
        owner = f"{key}:"
        if rendered.count(owner) != 1:
            raise ValueError(f"{key} must have one source owner")
        rendered = re.sub(
            rf"^{re.escape(key)}:.*$",
            f"{key}: {json.dumps(value)}",
            rendered,
            count=1,
            flags=re.MULTILINE,
        )
    output.write_text(rendered, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the refresh job environment")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--analytics-start-at", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--source-service", required=True)
    args = parser.parse_args()
    render_refresh_env(
        source=args.source,
        output=args.output,
        analytics_start_at=args.analytics_start_at,
        project_id=args.project,
        dataset_id=args.dataset,
        location=args.location,
        source_service=args.source_service,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
