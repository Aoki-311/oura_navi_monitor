from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

import scripts.import_monitor_users as importer
from scripts.import_monitor_users import load_roster_plan


@pytest.fixture
def roster_workbook(tmp_path: Path) -> Path:
    path = tmp_path / "OurA-Navi_userlist.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "OurA-Naviユーザー管理"
    sheet.append([
        "エリア",
        "勤務地",
        "社員名",
        "社員メールアドレス",
        "役割",
        "テルモMR経歴",
        "備考",
    ])
    sheet.append(["北海道東北", "札幌", "MR利用者", "mr@example.com", "本社MR", "10年", "MR"])
    sheet.append(["本社", "虎ノ門", "HC利用者", "hc@example.com", "本部メンバー", "", "本社（ヘルスケア）"])
    sheet.append(["首都圏A", "東京", "DM本社利用者", "dm@example.com", "本部メンバー", "", "本社（DM）"])
    sheet.append(["本社", "虎ノ門", "管理者", "admin@example.com", "本部メンバー", "", "システム管理者"])
    workbook.save(path)
    return path


def test_roster_import_plan_derives_summary_from_exact_roles_and_user_map_from_departments(roster_workbook: Path) -> None:
    plan = load_roster_plan(roster_workbook)
    assert len(plan.users) == 4
    assert plan.scope_counts == {"global": 1, "user_map": 3, "management": 4}
    assert plan.department_counts == {
        "DM専任": 1,
        "ヘルスケア本社": 1,
        "DM本社": 1,
        "管理者": 1,
    }
    assert all(item["user_id"] == "" for item in plan.users)
    assert all("user_key" not in item for item in plan.users)


def test_toranomon_and_tokyo_business_area_remain_distinct(roster_workbook: Path) -> None:
    plan = load_roster_plan(roster_workbook)
    headquarters = [item for item in plan.users if item["area_key"] == "本社・虎ノ門"]
    tokyo_business = [item for item in plan.users if item["area_key"] == "首都圏A"]
    assert len(headquarters) == 2
    assert len(tokyo_business) == 1


def test_bootstrap_apply_resumes_only_an_exact_partial_plan(
    roster_workbook: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = load_roster_plan(roster_workbook)
    stored = [dict(plan.users[0])]
    planned_by_email = {item["email"]: item for item in plan.users}

    class Directory:
        @staticmethod
        def list_users(*, include_inactive: bool = True):
            assert include_inactive is True
            return [dict(item) for item in stored]

    class Manager:
        def __init__(self, *, directory):
            assert directory is not None

        @staticmethod
        def create_user(payload, *, actor: str):
            assert actor == "admin@example.com"
            stored.append(dict(planned_by_email[payload.email]))

    credential = roster_workbook.parent / "approved-credential.json"
    credential.write_text("{}", encoding="utf-8")
    credential.chmod(0o600)
    monkeypatch.setattr(
        importer,
        "get_settings",
        lambda: SimpleNamespace(
            monitor_firestore_database="lcs-user-data",
            monitor_project_id="test-project",
        ),
    )
    monkeypatch.setattr(
        importer.service_account.Credentials,
        "from_service_account_file",
        lambda _path: object(),
    )
    monkeypatch.setattr(importer.firestore, "Client", lambda **_kwargs: object())
    monkeypatch.setattr(
        importer,
        "UserDirectoryRepository",
        lambda _settings, *, client: Directory(),
    )
    monkeypatch.setattr(importer, "UserManagementService", Manager)

    receipt = importer._apply_plan(
        plan,
        actor="admin@example.com",
        credential_file=str(credential),
    )

    assert receipt == {
        "appliedUsers": 4,
        "createdUsers": 3,
        "resumedFromUsers": 1,
        "readbackMatched": True,
    }


def test_bootstrap_apply_refuses_to_overwrite_a_changed_existing_row(
    roster_workbook: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = load_roster_plan(roster_workbook)
    changed = {**plan.users[0], "role": "別の役割"}

    class Directory:
        @staticmethod
        def list_users(*, include_inactive: bool = True):
            return [dict(changed)]

    credential = roster_workbook.parent / "approved-credential.json"
    credential.write_text("{}", encoding="utf-8")
    credential.chmod(0o600)
    monkeypatch.setattr(
        importer,
        "get_settings",
        lambda: SimpleNamespace(
            monitor_firestore_database="lcs-user-data",
            monitor_project_id="test-project",
        ),
    )
    monkeypatch.setattr(
        importer.service_account.Credentials,
        "from_service_account_file",
        lambda _path: object(),
    )
    monkeypatch.setattr(importer.firestore, "Client", lambda **_kwargs: object())
    monkeypatch.setattr(
        importer,
        "UserDirectoryRepository",
        lambda _settings, *, client: Directory(),
    )
    monkeypatch.setattr(
        importer,
        "UserManagementService",
        lambda **_kwargs: pytest.fail("changed bootstrap rows must be rejected before writes"),
    )

    with pytest.raises(RuntimeError, match="existing rows differ"):
        importer._apply_plan(
            plan,
            actor="admin@example.com",
            credential_file=str(credential),
        )
