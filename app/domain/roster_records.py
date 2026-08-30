from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from app.domain.analysis_scopes import ScopeEvaluation, evaluate_membership
from app.domain.roster_values import (
    CANONICAL_AREAS,
    HEADQUARTERS_AREA,
    HEADQUARTERS_AREA_KEY,
    HEADQUARTERS_WORKPLACE,
)


DOCUMENT_ID_FIELD = "_document_id"
ROSTER_ISSUES_FIELD = "_roster_issues"
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_DISPLAY_FIELDS = ("name", "email", "area", "area_key", "workplace")


def normalize_roster_email(value: object) -> str:
    email = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    if not _EMAIL_RE.fullmatch(email):
        raise ValueError("invalid email")
    return email


@dataclass(frozen=True)
class CanonicalRosterRecord:
    """One non-throwing read of a roster document for every downstream consumer."""

    value: dict[str, Any]
    document_id: str
    issues: tuple[str, ...]
    evaluation: ScopeEvaluation
    identity_eligible: bool
    projection_eligible: bool
    analytics_eligible: bool


@dataclass(frozen=True)
class CanonicalRosterDiagnostics:
    """Collection-level isolation receipt safe to expose with analytics."""

    isolated_count: int
    issue_counts: tuple[tuple[str, int], ...]
    fingerprint: str

    @property
    def issues(self) -> tuple[str, ...]:
        return tuple(issue for issue, _count in self.issue_counts)


class CanonicalRosterCollection(list[CanonicalRosterRecord]):
    """List-compatible canonical read plus its collection diagnostics."""

    def __init__(
        self,
        records: Iterable[CanonicalRosterRecord],
        *,
        diagnostics: CanonicalRosterDiagnostics,
    ) -> None:
        super().__init__(records)
        self.diagnostics = diagnostics

    @property
    def analytics_records(self) -> tuple[CanonicalRosterRecord, ...]:
        return tuple(record for record in self if record.analytics_eligible)

    @property
    def isolated_records(self) -> tuple[CanonicalRosterRecord, ...]:
        return tuple(record for record in self if not record.analytics_eligible)


def read_canonical_roster(value: object | None) -> CanonicalRosterRecord:
    """Normalize one roster row and classify unsafe consumers without raising.

    The Firestore document id is the repair address.  A missing or conflicting
    stored ``roster_id`` is surfaced to management through that address, while
    projections remain fail-closed until an administrator repairs the row.
    """

    issues: list[str] = []
    if isinstance(value, Mapping):
        source = dict(value)
    else:
        source = {}
        if value is not None:
            issues.append("invalid_roster_record")
    document_id = str(source.get(DOCUMENT_ID_FIELD) or "").strip()
    stored_roster_id = str(source.get("roster_id") or "").strip()
    roster_id = stored_roster_id
    if document_id:
        roster_id = document_id
        if not stored_roster_id:
            issues.append("missing_roster_id")
        elif stored_roster_id != document_id:
            issues.append("roster_id_document_mismatch")
    elif not stored_roster_id:
        issues.append("missing_roster_id")
    source["roster_id"] = roster_id

    for field in _DISPLAY_FIELDS:
        normalized = str(source.get(field) or "").strip()
        source[field] = normalized
        if not normalized:
            issues.append(f"missing_{field}")

    # A non-empty location is not necessarily a valid reporting location.
    # Keep the original values as the management repair surface, but never let
    # an unsupported or internally inconsistent location enter analytics or
    # USER_MAP projection.
    if source["area"] and source["area"] not in CANONICAL_AREAS:
        issues.append("invalid_area")
    elif source["area"]:
        expected_area_key = source["area"]
        if source["area"] == HEADQUARTERS_AREA:
            expected_area_key = HEADQUARTERS_AREA_KEY
            if source["workplace"] != HEADQUARTERS_WORKPLACE:
                issues.append("invalid_headquarters_workplace")
        if source["area_key"] and source["area_key"] != expected_area_key:
            issues.append("invalid_area_key")

    if source["email"]:
        try:
            source["email"] = normalize_roster_email(source["email"])
        except ValueError:
            issues.append("invalid_email")

    raw_label_ids = source.get("label_ids", [])
    if raw_label_ids is None:
        raw_label_ids = []
    if isinstance(raw_label_ids, (list, tuple)):
        normalized_label_ids = [
            str(label_id or "").strip()
            for label_id in raw_label_ids
            if str(label_id or "").strip()
        ]
        source["label_ids"] = list(dict.fromkeys(normalized_label_ids))
        if len(source["label_ids"]) != len(normalized_label_ids):
            issues.append("duplicate_label_reference")
    else:
        source["label_ids"] = []
        issues.append("invalid_label_ids")

    raw_is_active = source.get("is_active")
    if isinstance(raw_is_active, bool):
        source["is_active"] = raw_is_active
    else:
        # Unknown values must not become truthy through bool("false").
        source["is_active"] = False
        issues.append("invalid_is_active")

    evaluation = evaluate_membership(
        role=source.get("role"),
        department=str(source.get("department") or ""),
        is_active=source["is_active"],
    )
    source["role"] = evaluation.normalized_role
    source["department"] = str(source.get("department") or "").strip()
    issues.extend(evaluation.issues)
    unique_issues = tuple(dict.fromkeys(issues))
    source[ROSTER_ISSUES_FIELD] = list(unique_issues)

    identity_blockers = {
        "invalid_roster_record",
        "missing_roster_id",
        "roster_id_document_mismatch",
        "missing_email",
        "invalid_email",
        "invalid_is_active",
    }
    projection_blockers = {
        "invalid_roster_record",
        "missing_roster_id",
        "roster_id_document_mismatch",
        "missing_name",
        "missing_email",
        "invalid_email",
        "missing_area",
        "missing_area_key",
        "missing_workplace",
        "invalid_area",
        "invalid_area_key",
        "invalid_headquarters_workplace",
        "invalid_department",
        "invalid_is_active",
        "invalid_label_ids",
    }
    analytics_blockers = set(projection_blockers)
    issue_set = set(unique_issues)
    return CanonicalRosterRecord(
        value=source,
        document_id=document_id,
        issues=unique_issues,
        evaluation=evaluation,
        identity_eligible=not bool(issue_set & identity_blockers),
        projection_eligible=not bool(issue_set & projection_blockers),
        analytics_eligible=not bool(issue_set & analytics_blockers),
    )


def read_canonical_roster_collection(
    values: Iterable[object],
) -> CanonicalRosterCollection:
    """Read a roster collection and fail closed on ambiguous identities.

    Conflicting rows remain addressable in management, while every identity,
    projection, and analytics consumer excludes the whole ambiguous group.
    """

    records = [read_canonical_roster(value) for value in values]
    issues_by_index: dict[int, set[str]] = defaultdict(set)
    identity_conflicts: set[int] = set()
    projection_conflicts: set[int] = set()
    analytics_conflicts: set[int] = set()

    def mark_duplicates(issue: str, keys: Iterable[tuple[int, str]]) -> None:
        key_by_index = {index: key for index, key in keys if key}
        indexes_by_key: dict[str, list[int]] = defaultdict(list)
        for index, key in key_by_index.items():
            indexes_by_key[key].append(index)
        for indexes in indexes_by_key.values():
            if len(indexes) <= 1:
                continue
            # A row that is already invalid for one consumer must not make the
            # same identity appear unique to another consumer. Every row in an
            # ambiguous key group is repair-only across all downstream paths.
            identity_conflicts.update(indexes)
            projection_conflicts.update(indexes)
            analytics_conflicts.update(indexes)
            for index in indexes:
                issues_by_index[index].add(issue)

    mark_duplicates(
        "duplicate_roster_id",
        (
            (index, str(record.value.get("roster_id") or "").strip())
            for index, record in enumerate(records)
            if not {"missing_roster_id", "roster_id_document_mismatch"}
            & set(record.issues)
        ),
    )
    mark_duplicates(
        "duplicate_email",
        (
            (index, normalize_roster_email(record.value.get("email")))
            for index, record in enumerate(records)
            if not {"missing_email", "invalid_email"} & set(record.issues)
        ),
    )
    for field in ("chat_user_id", "user_id"):
        mark_duplicates(
            "duplicate_identity",
            (
                (index, str(record.value.get(field) or "").strip())
                for index, record in enumerate(records)
            ),
        )

    result: list[CanonicalRosterRecord] = []
    for index, record in enumerate(records):
        collection_issues = issues_by_index.get(index, set())
        if not collection_issues:
            result.append(record)
            continue
        issues = tuple(dict.fromkeys([*record.issues, *sorted(collection_issues)]))
        value = {**record.value, ROSTER_ISSUES_FIELD: list(issues)}
        result.append(
            replace(
                record,
                value=value,
                issues=issues,
                identity_eligible=(
                    record.identity_eligible and index not in identity_conflicts
                ),
                projection_eligible=(
                    record.projection_eligible and index not in projection_conflicts
                ),
                analytics_eligible=(
                    record.analytics_eligible and index not in analytics_conflicts
                ),
            )
        )
    isolated = [record for record in result if not record.analytics_eligible]
    issue_counts = Counter(
        issue for record in isolated for issue in record.issues
    )
    diagnostic_rows = [
        {
            "document_id": record.document_id,
            "roster_id": str(record.value.get("roster_id") or ""),
            "name": str(record.value.get("name") or ""),
            "email": str(record.value.get("email") or ""),
            "area": str(record.value.get("area") or ""),
            "area_key": str(record.value.get("area_key") or ""),
            "workplace": str(record.value.get("workplace") or ""),
            "role": str(record.value.get("role") or ""),
            "department": str(record.value.get("department") or ""),
            "mr_experience": str(record.value.get("mr_experience") or ""),
            "label_ids": sorted(
                str(label_id)
                for label_id in list(record.value.get("label_ids") or [])
            ),
            "chat_user_id": str(record.value.get("chat_user_id") or ""),
            "user_id": str(record.value.get("user_id") or ""),
            "is_active": record.value.get("is_active") is True,
            "issues": sorted(record.issues),
        }
        for record in isolated
    ]
    diagnostic_rows.sort(
        key=lambda row: json.dumps(
            row,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    diagnostic_payload = json.dumps(
        diagnostic_rows,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    diagnostics = CanonicalRosterDiagnostics(
        isolated_count=len(isolated),
        issue_counts=tuple(sorted(issue_counts.items())),
        fingerprint=hashlib.sha256(diagnostic_payload).hexdigest(),
    )
    return CanonicalRosterCollection(result, diagnostics=diagnostics)
