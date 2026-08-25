from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class Department(StrEnum):
    """The roster department is the sole source for analysis membership."""

    DM_FIELD = "DM専任"
    HEALTHCARE_HQ = "ヘルスケア本社"
    DM_HQ = "DM本社"
    ADMIN = "管理者"


class AnalysisScope(StrEnum):
    GLOBAL = "global"
    USER_MAP = "user_map"


def department_in_scope(
    department: Department | str,
    scope: AnalysisScope,
) -> bool:
    """Return historical eligibility without applying the current active flag."""

    value = Department(department)
    if scope is AnalysisScope.GLOBAL:
        return value in {Department.DM_FIELD, Department.HEALTHCARE_HQ}
    return value in {
        Department.DM_FIELD,
        Department.HEALTHCARE_HQ,
        Department.DM_HQ,
    }


@dataclass(frozen=True)
class ScopeMembership:
    global_enabled: bool
    user_map_enabled: bool

    def includes(self, scope: AnalysisScope) -> bool:
        if scope is AnalysisScope.GLOBAL:
            return self.global_enabled
        return self.user_map_enabled


def membership_for(
    department: Department | str,
    *,
    is_active: bool,
    label_ids: Iterable[str] = (),
) -> ScopeMembership:
    """Derive all scopes once; labels deliberately have no effect."""

    del label_ids
    if not is_active:
        return ScopeMembership(False, False)
    return ScopeMembership(
        global_enabled=department_in_scope(department, AnalysisScope.GLOBAL),
        user_map_enabled=department_in_scope(department, AnalysisScope.USER_MAP),
    )


def display_area(area_key: str) -> str:
    """Display the one canonical area identity stored on the roster row."""

    value = str(area_key or "").strip()
    if not value:
        raise ValueError("canonical area key is required")
    return value
