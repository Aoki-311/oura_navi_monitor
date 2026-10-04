#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from google.cloud import firestore
from google.oauth2 import service_account

from app.contracts.admin import UserCreate, UserPatch, normalize_email
from app.domain.analysis_scopes import AnalysisScope, Department, SCOPE_POLICY_VERSION, membership_for
from app.domain.management_errors import revision_text
from app.domain.roster_records import read_canonical_roster_collection
from app.domain.user_identity import roster_id_for_email
from app.repositories.user_directory import UserDirectoryRepository
from app.services.user_management import UserManagementService, area_key_for, normalize_roster_text
from app.settings import get_settings
try:
    from scripts.credential_preflight import approved_credential_path
except ModuleNotFoundError:
    from credential_preflight import approved_credential_path


REMARK_TO_DEPARTMENT = {
    "MR": Department.DM_FIELD,
    "MR(DM)": Department.DM_FIELD,
    "MR(HCS)": Department.HCS_FIELD,
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
    source_rows: int = 0
    resolved_duplicate_rows: list[int] = field(default_factory=list)
    normalized_headquarters_rows: list[int] = field(default_factory=list)


def _canonical_remark(value: Any) -> str:
    return normalize_roster_text(str(value or "")).replace("（", "(").replace("）", ")")


def load_roster_plan(
    path: Path, *, duplicate_departments: dict[str, str] | None = None,
) -> RosterPlan:
    """Read old/new rosters, resolving only explicitly selected duplicate identities."""
    resolutions = {
        normalize_email(email): Department(department)
        for email, department in (duplicate_departments or {}).items()
    }
    workbook = load_workbook(filename=path, read_only=True, data_only=True)
    try:
        candidates = [
            sheet for sheet in workbook
            if tuple(str(sheet.cell(1, index).value or "").strip() for index in range(1, 8))
            == EXPECTED_HEADERS
        ]
        if len(candidates) != 1:
            raise ValueError("expected exactly one roster sheet with the required A:G headers")
        sheet = candidates[0]
        has_team = sheet.max_column >= 9
        if has_team and str(sheet.cell(1, 9).value or "").strip() != "HCSチーム":
            raise ValueError("unexpected roster I column; expected HCSチーム")
        source = list(sheet.iter_rows(min_row=2, max_col=9, values_only=True))
    finally:
        workbook.close()

    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    normalized_headquarters_rows: list[int] = []
    for row_number, values in enumerate(source, 2):
        if not any(value not in (None, "") for value in values):
            continue
        row = dict(zip(EXPECTED_HEADERS, values[:7]))
        remark = _canonical_remark(row["備考"])
        try:
            department = REMARK_TO_DEPARTMENT[remark]
        except KeyError as exc:
            raise ValueError(f"unsupported roster remark: {remark}") from exc
        area = normalize_roster_text(row["エリア"])
        workplace = normalize_roster_text(row["勤務地"])
        # This workbook calls the Toranomon headquarters 東京. Preserve the
        # existing canonical headquarters identity, never infer field regions.
        if (area, workplace) == ("東京", "虎ノ門") and department in {
            Department.DM_HQ, Department.HEALTHCARE_HQ, Department.ADMIN,
        }:
            area = "本社"
            normalized_headquarters_rows.append(row_number)
        payload = UserCreate(
            name=normalize_roster_text(row["社員名"]),
            email=str(row["社員メールアドレス"] or ""),
            area=area,
            workplace=workplace,
            role=normalize_roster_text(row["役割"]),
            department=department,
            mr_experience=(
                normalize_roster_text(row["テルモMR経歴"])
                if department in {Department.DM_FIELD, Department.HCS_FIELD}
                else "-"
            ),
            team=normalize_roster_text(values[8]) if has_team and department is Department.HCS_FIELD else "",
            expected_scope_policy_version=SCOPE_POLICY_VERSION,
        )
        grouped.setdefault(payload.email, []).append(
            (row_number, {
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
                "team": payload.team,
                "label_ids": [],
                "chat_user_id": "",
                "is_active": True,
            })
        )

    users: list[dict[str, Any]] = []
    resolved_duplicate_rows: list[int] = []
    used_resolutions: set[str] = set()
    for email, entries in grouped.items():
        if len(entries) == 1:
            users.append(entries[0][1])
            continue
        preferred = resolutions.get(email)
        selected = [entry for entry in entries if entry[1]["department"] == preferred]
        if preferred is None or len(selected) != 1:
            rows = ", ".join(str(entry[0]) for entry in entries)
            raise ValueError(f"duplicate email in roster rows {rows}; select one exact department explicitly")
        used_resolutions.add(email)
        users.append(selected[0][1])
        resolved_duplicate_rows.extend(entry[0] for entry in entries if entry is not selected[0])
    if set(resolutions) != used_resolutions:
        raise ValueError("a duplicate resolution did not match a duplicated roster identity")

    memberships = [
        membership_for(
            role=item["role"],
            department=item["department"],
            is_active=item["is_active"],
        )
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
        source_rows=sum(len(entries) for entries in grouped.values()),
        resolved_duplicate_rows=sorted(resolved_duplicate_rows),
        normalized_headquarters_rows=normalized_headquarters_rows,
    )


def _directory_for(credential_file: str) -> UserDirectoryRepository:
    settings = get_settings()
    credentials = service_account.Credentials.from_service_account_file(
        str(approved_credential_path(credential_file))
    )
    database = str(settings.monitor_firestore_database or "(default)").strip()
    client = firestore.Client(
        project=settings.monitor_project_id,
        database=database,
        credentials=credentials,
    )
    return UserDirectoryRepository(settings, client=client)


def _apply_plan(plan: RosterPlan, *, actor: str, credential_file: str) -> dict[str, Any]:
    directory = _directory_for(credential_file)
    existing = directory.list_users(include_inactive=True)
    planned_by_id = {str(item["roster_id"]): item for item in plan.users}
    existing_by_id = {
        str(item.get("roster_id") or ""): item
        for item in existing
    }
    unexpected_ids = sorted(set(existing_by_id) - set(planned_by_id))
    if unexpected_ids:
        raise RuntimeError(
            "roster import is bootstrap-only; unrelated production roster rows exist"
        )
    bootstrap_fields = (
        "roster_id", "user_id", "chat_user_id", "name", "email", "area",
        "workplace", "area_key", "role", "department", "mr_experience",
        "label_ids", "is_active", "team",
    )
    for roster_id, current in existing_by_id.items():
        planned = planned_by_id[roster_id]
        mismatched = [
            field
            for field in bootstrap_fields
            if current.get(field, "" if field == "team" else None) != planned.get(field)
        ]
        if mismatched:
            raise RuntimeError(
                "roster import is bootstrap-only; existing rows differ from the plan"
            )
    manager = UserManagementService(directory=directory)
    created = 0
    for item in plan.users:
        if item["roster_id"] in existing_by_id:
            continue
        manager.create_user(
            UserCreate(
                name=item["name"],
                email=item["email"],
                area=item["area"],
                workplace=item["workplace"],
                role=item["role"],
                department=item["department"],
                mr_experience=item["mr_experience"],
                team=item["team"],
                expected_scope_policy_version=SCOPE_POLICY_VERSION,
            ),
            actor=actor,
        )
        created += 1
    applied = directory.list_users(include_inactive=True)
    expected_ids = {str(item["roster_id"]) for item in plan.users}
    applied_ids = {str(item.get("roster_id") or "") for item in applied}
    if applied_ids != expected_ids:
        raise RuntimeError("bootstrap readback does not match the planned roster")
    for current in applied:
        planned = planned_by_id[str(current.get("roster_id") or "")]
        if any(current.get(field, "" if field == "team" else None) != planned.get(field) for field in bootstrap_fields):
            raise RuntimeError("bootstrap field readback does not match the planned roster")
    return {
        "appliedUsers": len(applied),
        "createdUsers": created,
        "resumedFromUsers": len(existing),
        "readbackMatched": True,
    }


_SOURCE_FIELDS = ("name", "email", "area", "workplace", "role", "department", "mr_experience", "team")
_PRESERVED_FIELDS = ("label_ids", "is_active", "created_at")
_IDENTITY_FIELDS = ("user_id", "chat_user_id", "identity_bound_at")


def _sync_diff(plan: RosterPlan, existing: list[dict[str, Any]]) -> dict[str, Any]:
    """Build an additive/update-only plan before any write; email owns matching."""
    by_email: dict[str, dict[str, Any]] = {}
    ids: set[str] = set()
    for item in existing:
        email = normalize_email(str(item.get("email") or ""))
        roster_id = str(item.get("_document_id") or item.get("roster_id") or "").strip()
        if not roster_id or roster_id in ids or email in by_email:
            raise RuntimeError("existing roster has an ambiguous identity; repair it before sync")
        if item.get("roster_id") != roster_id:
            raise RuntimeError("existing roster document identity needs repair before sync")
        ids.add(roster_id)
        by_email[email] = item
    creates: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    unchanged = 0
    for planned in plan.users:
        current = by_email.get(planned["email"])
        if current is None:
            if planned["roster_id"] in ids:
                raise RuntimeError("new email conflicts with an existing stable roster id")
            creates.append(planned)
            continue
        changes = {
            key: planned[key] for key in _SOURCE_FIELDS
            if current.get(key, "" if key == "team" else None) != planned[key]
        }
        if current.get("area_key") != planned["area_key"]:
            # area_key is derived by the existing service, never directly written.
            changes.update(area=planned["area"], workplace=planned["workplace"])
        if changes:
            updates.append({"roster_id": current["roster_id"], "current": current, "changes": changes})
        else:
            unchanged += 1
    updates_by_id = {item["roster_id"]: item["changes"] for item in updates}
    candidates = []
    for current in existing:
        candidate = {**current, **updates_by_id.get(current["roster_id"], {})}
        if current["roster_id"] in updates_by_id:
            candidate["area_key"] = area_key_for(area=candidate["area"], workplace=candidate["workplace"])
        candidates.append(candidate)
    candidates.extend(creates)
    records = read_canonical_roster_collection(candidates)
    planned_emails = {item["email"] for item in plan.users}
    invalid = [
        record for record in records
        if "duplicate_identity" in record.issues
        or (record.value.get("email") in planned_emails and not record.analytics_eligible)
    ]
    if invalid:
        issues = sorted({issue for record in invalid for issue in record.issues})
        raise RuntimeError("sync candidate roster is invalid: " + ", ".join(issues))
    digest_source = {
        "policy": SCOPE_POLICY_VERSION,
        "planned": sorted(plan.users, key=lambda item: item["email"]),
        "existing": sorted(existing, key=lambda item: str(item.get("roster_id") or "")),
    }
    digest = hashlib.sha256(json.dumps(digest_source, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    return {
        "creates": creates,
        "updates": updates,
        "summary": {
            "createUsers": len(creates),
            "updateUsers": len(updates),
            "unchangedUsers": unchanged,
            "outsideWorkbookUsers": len(existing) - len(updates) - unchanged,
            "changedFieldCounts": dict(Counter(key for item in updates for key in item["changes"])),
            "syncDigest": digest,
        },
    }


def _apply_sync(
    plan: RosterPlan, *, directory: UserDirectoryRepository, actor: str,
    expected_sync_digest: str,
) -> dict[str, Any]:
    before = directory.list_users(include_inactive=True)
    diff = _sync_diff(plan, before)
    if not expected_sync_digest or expected_sync_digest != diff["summary"]["syncDigest"]:
        raise RuntimeError("sync plan changed; compare the current roster and use its exact syncDigest")
    referenced_labels = {
        label for item in diff["updates"]
        for label in item["current"].get("label_ids", [])
    }
    if referenced_labels:
        known_labels = {str(item.get("label_id") or "") for item in directory.list_labels(include_inactive=True)}
        if referenced_labels - known_labels:
            raise RuntimeError("sync references missing labels; repair the label catalog before writing")
    manager = UserManagementService(directory=directory)
    for item in diff["updates"]:
        manager.update_user(
            item["roster_id"],
            UserPatch(
                **item["changes"],
                expected_updated_at=revision_text(item["current"].get("updated_at")),
                expected_scope_policy_version=SCOPE_POLICY_VERSION,
            ),
            actor=actor,
        )
    for item in diff["creates"]:
        manager.create_user(
            UserCreate(**{key: item[key] for key in _SOURCE_FIELDS}, expected_scope_policy_version=SCOPE_POLICY_VERSION),
            actor=actor,
        )
    after = directory.list_users(include_inactive=True)
    remaining = _sync_diff(plan, after)
    if remaining["creates"] or remaining["updates"]:
        raise RuntimeError("sync readback differs from the workbook")
    by_email = {normalize_email(str(item["email"])): item for item in after}
    for old in before:
        current = by_email.get(normalize_email(str(old["email"])))
        if current is None or current.get("roster_id") != old.get("roster_id"):
            raise RuntimeError("sync did not preserve stable roster identity")
        if any(current.get(key) != old.get(key) for key in _PRESERVED_FIELDS):
            raise RuntimeError("sync readback did not preserve existing labels/status/creation time")
        if any(old.get(key) and current.get(key) != old.get(key) for key in _IDENTITY_FIELDS):
            raise RuntimeError("sync readback did not preserve a bound identity")
    return {**diff["summary"], "appliedUsers": len(plan.users), "readbackMatched": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or apply the canonical Monitor roster import")
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--actor", default="")
    parser.add_argument("--credential-file")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--sync", action="store_true", help="update matched users and add new users; preserve users outside workbook")
    parser.add_argument("--compare-current", action="store_true", help="read current Firestore roster with explicit credentials, without writes")
    parser.add_argument("--expected-sync-digest", default="", help="exact digest from --sync --compare-current, required for sync apply")
    parser.add_argument("--resolve-duplicate", action="append", default=[], metavar="EMAIL=DEPARTMENT", help="explicitly retain one department for a duplicated email")
    args = parser.parse_args()
    resolutions: dict[str, str] = {}
    for value in args.resolve_duplicate:
        email, separator, department = value.partition("=")
        if not separator or normalize_email(email) in resolutions:
            parser.error("each --resolve-duplicate must uniquely specify EMAIL=DEPARTMENT")
        resolutions[normalize_email(email)] = department
    if args.compare_current and (not args.sync or args.apply):
        parser.error("--compare-current requires --sync and cannot be combined with --apply")
    plan = load_roster_plan(args.workbook, duplicate_departments=resolutions)
    summary = {
        "mode": "apply" if args.apply else "plan",
        "users": len(plan.users),
        "scopeCounts": plan.scope_counts,
        "departmentCounts": plan.department_counts,
        "sourceRows": plan.source_rows,
        "resolvedDuplicateRows": plan.resolved_duplicate_rows,
        "normalizedHeadquartersRows": plan.normalized_headquarters_rows,
        "scopePolicyVersion": SCOPE_POLICY_VERSION,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if args.compare_current:
        directory = _directory_for(args.credential_file or "")
        comparison = _sync_diff(plan, directory.list_users(include_inactive=True))
        print(json.dumps({"syncPlan": comparison["summary"]}, ensure_ascii=False, sort_keys=True))
        return 0
    if not args.apply:
        return 0
    if not args.actor:
        raise SystemExit("--actor is required with --apply")
    if args.sync:
        if not args.expected_sync_digest:
            raise SystemExit("--expected-sync-digest is required with --sync --apply")
        receipt = _apply_sync(
            plan, directory=_directory_for(args.credential_file or ""), actor=args.actor,
            expected_sync_digest=args.expected_sync_digest,
        )
    else:
        receipt = _apply_plan(plan, actor=args.actor, credential_file=args.credential_file or "")
    print(json.dumps({"applyReceipt": receipt}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
