from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def canonical_timestamp(value: Any, *, field: str = "updated_at") -> str:
    """Return the one UTC-microsecond representation used by snapshot hashes."""

    if isinstance(value, datetime):
        resolved = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field} is required")
        try:
            resolved = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} is invalid") from exc
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=timezone.utc)
    return (
        resolved.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def roster_fingerprint(
    roster: list[dict[str, Any]],
    *,
    diagnostic_fingerprint: str = "",
) -> str:
    """Hash every roster value that can change analytics or its presentation."""

    canonical_roster = [
        {
            "roster_id": str(item.get("roster_id") or ""),
            "name": str(item.get("name") or ""),
            "email": str(item.get("email") or ""),
            "area": str(item.get("area") or ""),
            "role": str(item.get("role") or ""),
            "department": str(item.get("department") or ""),
            "area_key": str(item.get("area_key") or ""),
            "workplace": str(item.get("workplace") or ""),
            "mr_experience": str(item.get("mr_experience") or ""),
            "label_ids": sorted(
                str(value) for value in list(item.get("label_ids") or [])
            ),
            "is_active": bool(item.get("is_active")),
            "updated_at": canonical_timestamp(
                item.get("updated_at"), field="roster.updated_at"
            ),
        }
        for item in sorted(
            roster,
            key=lambda row: str(row.get("roster_id") or ""),
        )
    ]
    payload = json.dumps(
        {
            "roster": canonical_roster,
            "isolatedRosterFingerprint": diagnostic_fingerprint,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def content_fingerprint(
    *,
    roster_fingerprint_value: str,
    roster: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    label_catalog_status: str,
    label_catalog_issues: list[str],
) -> str:
    """Hash the referenced label presentation on top of a roster receipt."""

    referenced_label_ids = sorted(
        {
            str(label_id)
            for item in roster
            for label_id in list(item.get("label_ids") or [])
            if str(label_id)
        }
    )
    labels_by_id = {
        str(item.get("label_id") or ""): item
        for item in labels
        if str(item.get("label_id") or "")
    }
    canonical_labels = []
    for label_id in referenced_label_ids:
        item = labels_by_id.get(label_id)
        canonical_labels.append(
            {
                "label_id": label_id,
                "name": str((item or {}).get("name") or ""),
                "color": str((item or {}).get("color") or ""),
                "is_active": (
                    bool(item.get("is_active")) if item is not None else None
                ),
                "updated_at": (
                    canonical_timestamp(
                        item.get("updated_at"), field="label.updated_at"
                    )
                    if item is not None
                    else ""
                ),
                "missing": item is None,
            }
        )
    payload = json.dumps(
        {
            "roster_fingerprint": roster_fingerprint_value,
            "label_catalog_status": label_catalog_status,
            "label_catalog_issues": sorted(label_catalog_issues),
            "labels": canonical_labels,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
