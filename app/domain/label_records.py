from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from app.domain.roster_records import DOCUMENT_ID_FIELD


LABEL_COLORS = frozenset(
    {
        "#23d28f",
        "#386dff",
        "#ffb340",
        "#ff5b74",
        "#7c5cff",
        "#27d9d2",
        "#5f6285",
    }
)


@dataclass(frozen=True)
class CanonicalLabelRecord:
    value: dict[str, Any]
    document_id: str
    issues: tuple[str, ...]
    catalog_eligible: bool


def normalize_label_name_claim(value: object) -> str:
    """Return the canonical label-name claim used by the write owner."""

    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def read_canonical_label(value: object | None) -> CanonicalLabelRecord:
    issues: list[str] = []
    if isinstance(value, Mapping):
        source = dict(value)
    else:
        source = {}
        if value is not None:
            issues.append("invalid_label_record")
    document_id = str(source.get(DOCUMENT_ID_FIELD) or "").strip()
    stored_label_id = str(source.get("label_id") or "").strip()
    label_id = stored_label_id
    if document_id:
        label_id = document_id
        if not stored_label_id:
            issues.append("missing_label_id")
        elif stored_label_id != document_id:
            issues.append("label_id_document_mismatch")
    elif not stored_label_id:
        issues.append("missing_label_id")
    source["label_id"] = label_id

    name = str(source.get("name") or "").strip()
    source["name"] = name
    if not name:
        issues.append("missing_label_name")

    color = str(source.get("color") or "").strip().lower()
    source["color"] = color
    if color not in LABEL_COLORS:
        issues.append("invalid_label_color")

    raw_is_active = source.get("is_active")
    if isinstance(raw_is_active, bool):
        source["is_active"] = raw_is_active
    else:
        source["is_active"] = False
        issues.append("invalid_label_is_active")

    usage_count = source.get("usage_count", 0)
    if isinstance(usage_count, bool) or not isinstance(usage_count, int) or usage_count < 0:
        source["usage_count"] = 0
        issues.append("invalid_label_usage_count")
    else:
        source["usage_count"] = usage_count

    unique_issues = tuple(dict.fromkeys(issues))
    catalog_blockers = {
        "invalid_label_record",
        "missing_label_id",
        "label_id_document_mismatch",
        "missing_label_name",
        "invalid_label_color",
        "invalid_label_is_active",
    }
    return CanonicalLabelRecord(
        value=source,
        document_id=document_id,
        issues=unique_issues,
        catalog_eligible=not bool(set(unique_issues) & catalog_blockers),
    )


def read_canonical_label_collection(
    values: Iterable[object],
) -> list[CanonicalLabelRecord]:
    """Read a label collection and isolate every ambiguous catalog row."""

    records = [read_canonical_label(value) for value in values]
    issues_by_index: dict[int, set[str]] = defaultdict(set)

    def mark_duplicate_groups(
        issue: str,
        keys: Iterable[tuple[int, str]],
        *,
        require_distinct_label_ids: bool = False,
    ) -> None:
        indexes_by_key: dict[str, list[int]] = defaultdict(list)
        for index, key in keys:
            if key:
                indexes_by_key[key].append(index)
        for indexes in indexes_by_key.values():
            if len(indexes) <= 1:
                continue
            if require_distinct_label_ids:
                label_ids = {
                    str(records[index].value.get("label_id") or "").strip()
                    for index in indexes
                }
                if len(label_ids) <= 1:
                    continue
            for index in indexes:
                issues_by_index[index].add(issue)

    mark_duplicate_groups(
        "duplicate_label_id",
        (
            (index, str(record.value.get("label_id") or "").strip())
            for index, record in enumerate(records)
        ),
    )
    mark_duplicate_groups(
        "duplicate_label_name",
        (
            (index, normalize_label_name_claim(record.value.get("name")))
            for index, record in enumerate(records)
        ),
        require_distinct_label_ids=True,
    )

    result: list[CanonicalLabelRecord] = []
    for index, record in enumerate(records):
        collection_issues = issues_by_index.get(index)
        if not collection_issues:
            result.append(record)
            continue
        issues = tuple(dict.fromkeys([*record.issues, *sorted(collection_issues)]))
        result.append(
            replace(
                record,
                issues=issues,
                catalog_eligible=False,
            )
        )
    return result
