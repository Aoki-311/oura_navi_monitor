#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from app.contracts.admin import UserCreate, UserPatch
from app.domain.analysis_scopes import AnalysisScope, Department, membership_for
from app.domain.user_identity import roster_id_for_email
from app.repositories.user_directory import UserDirectoryRepository
from app.services.user_management import UserManagementService, area_key_for, normalize_roster_text
from app.settings import get_settings


REMARK_TO_DEPARTMENT = {
    "MR": Department.DM_FIELD,
    "本社(DM)": Department.DM_HQ,
    "本社(ヘルスケア)": Department.HEALTHCARE_HQ,
    "システム管理者": Department.ADMIN,
}
EXPECTED_HEADERS = (
    "エリア",
    "勤務地",
    "社員名",
    "社員メールアドレス",
    "役割",
    "テルモMR経歴",
    "備考",
)


@dataclass(frozen=True)
class RosterPlan:
    users: list[dict[str, Any]]
    scope_counts: dict[str, int]
    department_counts: dict[str, int]


def _canonical_remark(value: Any) -> str:
    return normalize_roster_text(str(value or "")).replace("（", "(").replace("）", ")")


def load_roster_plan(path: Path) -> RosterPlan:
    workbook = load_workbook(filename=path, read_only=True, data_only=True)
    if "OurA-Naviユーザー管理" not in workbook.sheetnames:
        raise ValueError("required roster sheet is missing")
    sheet = workbook["OurA-Naviユーザー管理"]
    headers = [str(sheet.cell(1, index).value or "").strip() for index in range(1, 8)]
    if tuple(headers) != EXPECTED_HEADERS:
        raise ValueError(f"unexpected roster headers: {headers}")

    users: list[dict[str, Any]] = []
    emails: set[str] = set()
    for values in sheet.iter_rows(min_row=2, max_col=7, values_only=True):
        if not any(value not in (None, "") for value in values):
            continue
        row = dict(zip(headers, values))
        remark = _canonical_remark(row["備考"])
        try:
            department = REMARK_TO_DEPARTMENT[remark]
        except KeyError as exc:
            raise ValueError(f"unsupported roster remark: {remark}") from exc
        payload = UserCreate(
            name=normalize_roster_text(row["社員名"]),
            email=str(row["社員メールアドレス"] or ""),
            area=normalize_roster_text(row["エリア"]),
            workplace=normalize_roster_text(row["勤務地"]),
            role=normalize_roster_text(row["役割"]),
            department=department,
            mr_experience=(
                normalize_roster_text(row["テルモMR経歴"])
                if department is Department.DM_FIELD
                else "-"
            ),
        )
        if payload.email in emails:
            raise ValueError("duplicate email in roster")
        emails.add(payload.email)
        users.append(
            {
                "roster_id": roster_id_for_email(payload.email),
                "user_id": "",
                "name": payload.name,
                "email": payload.email,
                "area": payload.area,
                "workplace": payload.workplace,
                "area_key": area_key_for(area=payload.area, workplace=payload.workplace),
                "role": payload.role,
                "department": department.value,
                "mr_experience": payload.mr_experience or "-",
                "label_ids": [],
                "chat_user_id": "",
                "is_active": True,
            }
        )

    memberships = [
        membership_for(Department(item["department"]), is_active=item["is_active"])
        for item in users
    ]
    return RosterPlan(
        users=users,
        scope_counts={
            AnalysisScope.GLOBAL.value: sum(item.includes(AnalysisScope.GLOBAL) for item in memberships),
            AnalysisScope.USER_MAP.value: sum(item.includes(AnalysisScope.USER_MAP) for item in memberships),
            "management": len(users),
        },
        department_counts=dict(Counter(item["department"] for item in users)),
    )


def _apply_plan(plan: RosterPlan, *, actor: str) -> None:
    settings = get_settings()
    directory = UserDirectoryRepository(settings)
    manager = UserManagementService(directory=directory)
    for item in plan.users:
        existing = directory.get_user(item["roster_id"])
        if existing is None:
            manager.create_user(
                UserCreate(
                    name=item["name"],
                    email=item["email"],
                    area=item["area"],
                    workplace=item["workplace"],
                    role=item["role"],
                    department=item["department"],
                    mr_experience=item["mr_experience"],
                ),
                actor=actor,
            )
            continue
        manager.update_user(
            item["roster_id"],
            UserPatch(
                name=item["name"],
                email=item["email"],
                area=item["area"],
                workplace=item["workplace"],
                role=item["role"],
                department=item["department"],
                mr_experience=item["mr_experience"],
                is_active=True,
            ),
            actor=actor,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or apply the canonical Monitor roster import")
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--actor", default="")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    plan = load_roster_plan(args.workbook)
    summary = {
        "mode": "apply" if args.apply else "plan",
        "users": len(plan.users),
        "scopeCounts": plan.scope_counts,
        "departmentCounts": plan.department_counts,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if not args.apply:
        return 0
    if not args.actor:
        raise SystemExit("--actor is required with --apply")
    _apply_plan(plan, actor=args.actor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
