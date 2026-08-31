from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Protocol

from app.domain.analytics_snapshot import content_fingerprint, roster_fingerprint
from app.domain.analysis_scopes import AnalysisScope, membership_for
from app.domain.label_records import read_canonical_label_collection
from app.domain.roster_records import (
    ROSTER_ISSUES_FIELD,
    read_canonical_roster_collection,
)


LOGGER = logging.getLogger(__name__)


class UserScopeDirectory(Protocol):
    def list_users(self, *, include_inactive: bool = True) -> list[dict[str, Any]]: ...

    def list_labels(self, *, include_inactive: bool = True) -> list[dict[str, Any]]: ...


class UserScopeProjection(list[dict[str, Any]]):
    """Canonical roster rows plus the receipts derived from those same rows."""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        fingerprints: dict[str, str],
    ) -> None:
        super().__init__(rows)
        self.fingerprints = dict(fingerprints)


def project_user_scope(
    directory: UserScopeDirectory,
    *,
    projected_at: datetime | None = None,
) -> UserScopeProjection:
    """Project the one canonical roster contract used by refresh and legacy reads."""

    records = read_canonical_roster_collection(
        directory.list_users(include_inactive=True)
    )
    projection_time = projected_at or datetime.now(timezone.utc)
    for record in records.analytics_records:
        if not record.value.get("updated_at"):
            record.value["updated_at"] = projection_time

    issue_counts: Counter[str] = Counter(dict(records.diagnostics.issue_counts))
    if issue_counts:
        LOGGER.warning(
            "scope projection roster rows isolated: %s",
            dict(sorted(issue_counts.items())),
        )

    label_catalog_unavailable = False
    base_label_catalog_issues: list[str] = []
    try:
        raw_labels = directory.list_labels(include_inactive=True)
    except Exception:
        LOGGER.exception("scope projection label catalog unavailable")
        label_rows: list[dict[str, Any]] = []
        label_catalog_unavailable = True
        base_label_catalog_issues = ["label_catalog_unavailable"]
    else:
        label_rows = []
        for label_record in read_canonical_label_collection(raw_labels):
            if not label_record.catalog_eligible:
                base_label_catalog_issues.extend(label_record.issues)
                continue
            if not label_record.value.get("updated_at"):
                label_record.value["updated_at"] = projection_time
            label_rows.append(label_record.value)
        base_label_catalog_issues = list(dict.fromkeys(base_label_catalog_issues))

    labels_by_id = {
        str(item.get("label_id") or ""): item for item in label_rows
    }

    def label_diagnostics(
        scope_roster: list[dict[str, Any]],
    ) -> tuple[str, list[str]]:
        if label_catalog_unavailable:
            return "unavailable", list(base_label_catalog_issues)
        issues = list(base_label_catalog_issues)
        referenced_label_ids = {
            str(label_id)
            for user in scope_roster
            for label_id in list(user.get("label_ids") or [])
            if str(label_id)
        }
        if referenced_label_ids - set(labels_by_id):
            issues.append("unknown_label_reference")
        if any(
            "duplicate_label_reference"
            in list(user.get(ROSTER_ISSUES_FIELD) or [])
            for user in scope_roster
        ):
            issues.append("duplicate_label_reference")
        issues = list(dict.fromkeys(issues))
        return ("partial" if issues else "available"), issues

    fingerprints: dict[str, str] = {}
    scope_label_metadata: dict[str, tuple[str, list[str]]] = {}
    for scope in (AnalysisScope.GLOBAL, AnalysisScope.USER_MAP):
        scope_roster = [
            record.value
            for record in records.analytics_records
            if record.evaluation.membership.includes(scope)
        ]
        roster_receipt = roster_fingerprint(
            scope_roster,
            diagnostic_fingerprint=records.diagnostics.fingerprint,
        )
        fingerprints[f"{scope.value}_roster_fingerprint"] = roster_receipt
        catalog_status, catalog_issues = label_diagnostics(scope_roster)
        scope_label_metadata[scope.value] = (catalog_status, catalog_issues)
        fingerprints[f"{scope.value}_content_fingerprint"] = content_fingerprint(
            roster_fingerprint_value=roster_receipt,
            roster=scope_roster,
            labels=label_rows,
            label_catalog_status=catalog_status,
            label_catalog_issues=catalog_issues,
        )

    global_status, global_issues = scope_label_metadata[AnalysisScope.GLOBAL.value]
    user_map_status, user_map_issues = scope_label_metadata[
        AnalysisScope.USER_MAP.value
    ]
    rows: list[dict[str, Any]] = []
    for record in records.analytics_records:
        user = record.value
        membership = membership_for(
            role=user.get("role"),
            department=user.get("department", ""),
            is_active=True,
        )
        referenced_labels = [
            labels_by_id[label_id]
            for label_id in list(user.get("label_ids") or [])
            if label_id in labels_by_id
        ]
        rows.append(
            {
                "snapshot_run_id": "",
                "snapshot_created_at": None,
                "roster_id": user["roster_id"],
                "user_id": str(user.get("user_id") or "").strip() or None,
                "name": user.get("name"),
                "email": user.get("email"),
                "area": user.get("area"),
                "area_key": user.get("area_key"),
                "workplace": user.get("workplace"),
                "role": user.get("role"),
                "department": user.get("department"),
                "mr_experience": user.get("mr_experience"),
                "label_ids_json": json.dumps(
                    list(user.get("label_ids") or []), ensure_ascii=False
                ),
                "labels_json": json.dumps(
                    referenced_labels,
                    ensure_ascii=False,
                    default=str,
                    sort_keys=True,
                ),
                "is_active": bool(user.get("is_active")),
                "global_scope_enabled": membership.global_enabled,
                "user_map_scope_enabled": membership.user_map_enabled,
                "is_admin": str(user.get("department")) == "管理者",
                "updated_at": user.get("updated_at"),
                "roster_isolated_count": records.diagnostics.isolated_count,
                "roster_issue_counts_json": json.dumps(
                    dict(records.diagnostics.issue_counts),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "roster_diagnostic_fingerprint": records.diagnostics.fingerprint,
                "global_label_catalog_status": global_status,
                "global_label_catalog_issues_json": json.dumps(
                    global_issues, ensure_ascii=False
                ),
                "user_map_label_catalog_status": user_map_status,
                "user_map_label_catalog_issues_json": json.dumps(
                    user_map_issues, ensure_ascii=False
                ),
            }
        )
    return UserScopeProjection(rows, fingerprints=fingerprints)
