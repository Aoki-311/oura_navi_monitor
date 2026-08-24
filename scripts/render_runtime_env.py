#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


_IAP_AUDIENCE_RE = re.compile(
    r"^/projects/[1-9][0-9]*/global/backendServices/[1-9][0-9]*$"
)


def validate_iap_audience(value: str) -> str:
    audience = str(value or "").strip()
    if not _IAP_AUDIENCE_RE.fullmatch(audience):
        raise ValueError(
            "exact IAP signed-header audience must match "
            "/projects/{PROJECT_NUMBER}/global/backendServices/{BACKEND_SERVICE_ID}"
        )
    return audience


def render_runtime_env(*, source: Path, output: Path, iap_audience: str) -> None:
    audience = validate_iap_audience(iap_audience)
    text = source.read_text(encoding="utf-8").rstrip() + "\n"
    if "MONITOR_IAP_AUDIENCE:" in text:
        raise ValueError("MONITOR_IAP_AUDIENCE must have one build-time owner")
    output.write_text(
        text + f"MONITOR_IAP_AUDIENCE: {json.dumps(audience)}\n",
        encoding="utf-8",
    )


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
    parser = argparse.ArgumentParser(description="Render one candidate runtime environment")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    owner = parser.add_mutually_exclusive_group(required=True)
    owner.add_argument("--iap-audience")
    owner.add_argument("--analytics-start-at")
    args = parser.parse_args()
    if args.iap_audience is not None:
        render_runtime_env(
            source=args.source,
            output=args.output,
            iap_audience=args.iap_audience,
        )
    else:
        render_refresh_env(
            source=args.source,
            output=args.output,
            analytics_start_at=args.analytics_start_at,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
