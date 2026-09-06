#!/usr/bin/env python3
"""Generate Monitor display labels from an explicitly selected News producer.

The source files are read as data; no producer modules or cloud clients are
imported. Re-run this command when the producer taxonomy/catalog changes.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path


def render_catalog(producer_root: Path) -> str:
    source_root = producer_root / "backend/src/lcs_core/features/news"
    service_bytes = (source_root / "service.py").read_bytes()
    catalog_bytes = (source_root / "society_source_catalog.json").read_bytes()
    module = ast.parse(service_bytes.decode("utf-8"))
    event_types = next(
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "EVENT_TYPE_LABELS_JA"
    )
    source = json.loads(catalog_bytes)
    payload = {
        "schemaVersion": "news_usage_catalog_v1",
        "societyCatalogVersion": source["schema_version"],
        "sourceSha256": {
            "service.py": hashlib.sha256(service_bytes).hexdigest(),
            "society_source_catalog.json": hashlib.sha256(catalog_bytes).hexdigest(),
        },
        "newsEventTypes": [{"key": key, "label": label} for key, label in event_types],
        "societyCategories": [
            {"key": item["category_key"], "label": item["category_key"]}
            for item in source["categories"]
        ],
        "societySources": [
            {"key": item["source_id"], "label": item["official_name_ja"]}
            for item in source["sources"] if item["active_scope"]
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer-root", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = Path(__file__).resolve().parents[1] / "app/contracts/news_usage_catalog.json"
    rendered = render_catalog(args.producer_root)
    if args.check:
        return 0 if target.read_text(encoding="utf-8") == rendered else 1
    target.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
