from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Iterable, Mapping


class Department(StrEnum):
    """Closed roster departments used for administration and USER_MAP access."""

    DM_FIELD = "MR(DM)"
    HCS_FIELD = "MR(HCS)"
    HEALTHCARE_HQ = "ヘルスケア本社"
    DM_HQ = "DM本社"
    ADMIN = "管理者"

    @classmethod
    def _missing_(cls, value: object) -> Department | None:
        normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
        if normalized == "DM専任":
            return cls.DM_FIELD
        return next((member for member in cls if member.value == normalized), None)


class AnalysisScope(StrEnum):
    GLOBAL = "global"
    USER_MAP = "user_map"


class SummaryCohort(StrEnum):
    ALL = "all"
    DM = "dm"
    HCS = "hcs"


SUMMARY_ROLES: Final[tuple[str, ...]] = ("社員MR", "本社MR", "コントラクトMR")
SCOPE_POLICY_VERSION: Final[str] = "summary_department_v2"
LEGACY_SCOPE_POLICY_VERSION: Final[str] = "summary_role_v1"
READABLE_SCOPE_POLICY_VERSIONS: Final[frozenset[str]] = frozenset(
    {SCOPE_POLICY_VERSION, LEGACY_SCOPE_POLICY_VERSION}
)
_SUMMARY_DEPARTMENTS: Final[frozenset[Department]] = frozenset(
    {Department.DM_FIELD, Department.HCS_FIELD}
)
_USER_MAP_DEPARTMENTS: Final[frozenset[Department]] = frozenset(
    {*_SUMMARY_DEPARTMENTS, Department.HEALTHCARE_HQ, Department.DM_HQ}
)


def normalize_role(value: object) -> str:
    """Normalize roster text without introducing fuzzy role matching."""

    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


@dataclass(frozen=True)
class ScopeMembership:
    global_enabled: bool
    user_map_enabled: bool

    def includes(self, scope: AnalysisScope) -> bool:
        if scope is AnalysisScope.GLOBAL:
            return self.global_enabled
        return self.user_map_enabled


@dataclass(frozen=True)
class ScopeEvaluation:
    membership: ScopeMembership
    normalized_role: str
    department: Department | None
    issues: tuple[str, ...]


def evaluate_membership(
    *,
    role: object,
    department: Department | str,
    is_active: bool,
    label_ids: Iterable[str] = (),
) -> ScopeEvaluation:
    """Safely derive scopes for both valid and legacy roster rows.

    Summary membership requires a field department and an exact MR role.
    Headquarters remain eligible for USER_MAP. Monitor labels are annotations
    only and never grant either scope.
    """

    del label_ids
    normalized_role = normalize_role(role)
    issues: list[str] = []
    if not normalized_role:
        issues.append("missing_role")
    try:
        resolved_department = Department(department)
    except (TypeError, ValueError):
        resolved_department = None
        issues.append("invalid_department")

    user_map_enabled = bool(
        resolved_department is not None
        and is_active
        and resolved_department in _USER_MAP_DEPARTMENTS
    )
    global_enabled = bool(
        user_map_enabled
        and resolved_department in _SUMMARY_DEPARTMENTS
        and normalized_role in SUMMARY_ROLES
    )
    return ScopeEvaluation(
        membership=ScopeMembership(
            global_enabled=global_enabled,
            user_map_enabled=user_map_enabled,
        ),
        normalized_role=normalized_role,
        department=resolved_department,
        issues=tuple(issues),
    )


def membership_for(
    *,
    role: object,
    department: Department | str,
    is_active: bool,
    label_ids: Iterable[str] = (),
) -> ScopeMembership:
    """Return the single governed membership projection."""

    return evaluate_membership(
        role=role,
        department=department,
        is_active=is_active,
        label_ids=label_ids,
    ).membership


def summary_cohort_matches(
    row: Mapping[str, object],
    cohort: SummaryCohort | str = SummaryCohort.ALL,
) -> bool:
    """Select a summary cohort from the same governed roster membership."""

    selected = SummaryCohort(cohort)
    evaluation = evaluate_membership(
        role=row.get("role"),
        department=str(row.get("department") or ""),
        is_active=row.get("is_active") is True,
    )
    if not evaluation.membership.global_enabled:
        return False
    return selected is SummaryCohort.ALL or evaluation.department is {
        SummaryCohort.DM: Department.DM_FIELD,
        SummaryCohort.HCS: Department.HCS_FIELD,
    }[selected]


def display_area(area_key: str) -> str:
    """Display the one canonical area identity stored on the roster row."""

    value = str(area_key or "").strip()
    if not value:
        raise ValueError("canonical area key is required")
    return value
