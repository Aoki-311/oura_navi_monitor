from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.analytics_snapshot import (
    canonical_timestamp,
    content_fingerprint,
    roster_fingerprint,
)


def _roster(updated_at):
    return [{
        "roster_id": "roster-1",
        "name": "利用者",
        "email": "user@example.com",
        "area": "関西",
        "area_key": "関西",
        "workplace": "大阪",
        "role": "本社MR",
        "department": "DM専任",
        "mr_experience": "10年",
        "label_ids": ["label-1"],
        "is_active": True,
        "updated_at": updated_at,
    }]


def _labels(updated_at):
    return [{
        "label_id": "label-1",
        "name": "重点",
        "color": "#23d28f",
        "is_active": True,
        "updated_at": updated_at,
    }]


def test_firestore_nanoseconds_and_bigquery_datetime_have_one_snapshot_receipt() -> None:
    firestore_value = "2026-08-30T01:02:03.123456789Z"
    bigquery_value = datetime(
        2026, 8, 30, 1, 2, 3, 123456, tzinfo=timezone.utc
    )
    assert canonical_timestamp(firestore_value) == "2026-08-30T01:02:03.123456Z"
    assert canonical_timestamp(bigquery_value) == "2026-08-30T01:02:03.123456Z"

    firestore_roster = _roster(firestore_value)
    bigquery_roster = _roster(bigquery_value)
    firestore_roster_fp = roster_fingerprint(firestore_roster)
    bigquery_roster_fp = roster_fingerprint(bigquery_roster)
    assert firestore_roster_fp == bigquery_roster_fp
    assert content_fingerprint(
        roster_fingerprint_value=firestore_roster_fp,
        roster=firestore_roster,
        labels=_labels(firestore_value),
        label_catalog_status="available",
        label_catalog_issues=[],
    ) == content_fingerprint(
        roster_fingerprint_value=bigquery_roster_fp,
        roster=bigquery_roster,
        labels=_labels("2026-08-30T01:02:03.123456+00:00"),
        label_catalog_status="available",
        label_catalog_issues=[],
    )


def test_unparseable_snapshot_timestamp_fails_closed() -> None:
    with pytest.raises(ValueError, match="roster.updated_at is invalid"):
        roster_fingerprint(_roster("not-a-timestamp"))
