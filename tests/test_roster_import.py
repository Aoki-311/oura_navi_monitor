from pathlib import Path

import pytest
from openpyxl import Workbook

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


def test_roster_import_plan_derives_scope_counts_from_departments(roster_workbook: Path) -> None:
    plan = load_roster_plan(roster_workbook)
    assert len(plan.users) == 4
    assert plan.scope_counts == {"global": 2, "user_map": 3, "management": 4}
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
