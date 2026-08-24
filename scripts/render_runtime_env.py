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
) -> None:
    start = str(analytics_start_at or "").strip()
    if not _UTC_SECOND_RE.fullmatch(start):
        raise ValueError("exact UTC analytics start is required")
    text = source.read_text(encoding="utf-8").rstrip() + "\n"
    owner = "MONITOR_ANALYTICS_START_AT:"
    if text.count(owner) != 1:
        raise ValueError("MONITOR_ANALYTICS_START_AT must have one source owner")
    rendered = re.sub(
        r'^MONITOR_ANALYTICS_START_AT:.*$',
        f"MONITOR_ANALYTICS_START_AT: {json.dumps(start)}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    output.write_text(rendered, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the refresh job environment")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--analytics-start-at", required=True)
    args = parser.parse_args()
    render_refresh_env(
        source=args.source,
        output=args.output,
        analytics_start_at=args.analytics_start_at,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
