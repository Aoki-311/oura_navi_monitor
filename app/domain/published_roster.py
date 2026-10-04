"""Validate frozen publications before interpreting them with today's policy."""

from typing import Any

from app.domain.analysis_scopes import (
    AnalysisScope,
    LEGACY_SCOPE_POLICY_VERSION,
    READABLE_SCOPE_POLICY_VERSIONS,
    membership_for,
    normalize_role,
)
from app.domain.roster_records import read_canonical_roster_collection


def validated_published_roster(
    rows: list[dict[str, Any]], *, policy_version: str,
) -> list[dict[str, Any]]:
    """Check stored flags under the writer's policy; return canonical API rows.

    Callers must verify receipts against the original ``rows`` before using
    the return value. In particular, converting DM専任 to MR(DM) before hashing
    would invalidate every publication produced before the HCS release.
    """

    if policy_version not in READABLE_SCOPE_POLICY_VERSIONS:
        raise ValueError("unsupported published scope policy")
    records = read_canonical_roster_collection(rows)
    if len(records.analytics_records) != len(rows):
        raise ValueError("published roster contains invalid rows")
    for raw in rows:
        if policy_version == LEGACY_SCOPE_POLICY_VERSION:
            # These exact departments and roles belonged to the v1 writer.
            # Future departments must never be accepted under an old receipt.
            department = raw.get("department")
            if department not in {"DM専任", "ヘルスケア本社", "DM本社", "管理者"}:
                raise ValueError("published roster contains invalid legacy department")
            user_map_enabled = department != "管理者"
            global_enabled = user_map_enabled and normalize_role(raw.get("role")) in {
                "本社MR", "コントラクトMR",
            }
        else:
            structural = membership_for(
                role=raw.get("role"), department=raw.get("department", ""),
                is_active=True,
            )
            global_enabled = structural.global_enabled
            user_map_enabled = structural.user_map_enabled
        if (
            raw.get("global_scope_enabled") is not global_enabled
            or raw.get("user_map_scope_enabled") is not user_map_enabled
        ):
            raise ValueError("published roster contains invalid scope flags")
    return [record.value for record in records.analytics_records]


def published_scope_rows(
    rows: list[dict[str, Any]], scope: AnalysisScope,
) -> list[dict[str, Any]]:
    """The writer's original scope, used only for immutable receipt checks."""

    return [row for row in rows if row.get("is_active") is True
            and row.get(f"{scope.value}_scope_enabled") is True]


def current_scope_rows(
    rows: list[dict[str, Any]], scope: AnalysisScope,
) -> list[dict[str, Any]]:
    """Apply current membership only after the whole source was verified."""

    return [row for row in rows if membership_for(
        role=row.get("role"), department=row.get("department", ""),
        is_active=row.get("is_active") is True,
    ).includes(scope)]
