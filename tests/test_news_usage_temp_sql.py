from __future__ import annotations

import pytest

from scripts import validate_news_usage_temp_sql as fixture


def test_render_uses_canonical_publisher_and_schemas_only_in_temp_owners():
    sql = fixture.render_fixture_sql()
    canonical = (fixture.SQL_DIR / "publish_news_usage.sql").read_text(encoding="utf-8")
    assert fixture._localize(canonical) in sql
    for name, filename in fixture.TABLE_SCHEMAS.items():
        assert fixture._schema(name, filename) in sql
    assert "content_event_type STRING" in sql
    assert "CREATE TEMP TABLE `_SESSION.fixture_news_usage_event_source` AS" in sql
    assert "two details plus three primary links" in sql
    assert "Chat publication pointer unchanged" in sql
    assert "persistentTablesReferenced" not in sql
    fixture.assert_temp_only(sql)


@pytest.mark.parametrize("unsafe", [
    "SELECT * FROM `real-project.real_dataset.table`",
    "SELECT * FROM real_project.real_dataset.table",
    "SELECT * FROM unowned_table",
    "CREATE TABLE unowned_table (value INT64)",
    "EXECUTE IMMEDIATE 'SELECT 1'",
    "SELECT * FROM EXTERNAL_QUERY('connection', 'SELECT 1')",
])
def test_temp_guard_rejects_persistent_unknown_and_dynamic_sql(unsafe):
    with pytest.raises(ValueError):
        fixture.assert_temp_only(fixture.render_fixture_sql() + "\n" + unsafe)


def test_new_canonical_table_owner_requires_explicit_fixture_support():
    with pytest.raises(ValueError, match="persistent table owner"):
        fixture._localize("SELECT * FROM `${PROJECT_ID}.${DATASET_ID}.new_table`")


def test_render_only_never_checks_credentials_or_constructs_cloud_clients(monkeypatch, capsys):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("render-only must remain local")

    monkeypatch.setattr(fixture, "approved_credential_path", forbidden)
    assert fixture.main(["--render-only"]) == 0
    assert capsys.readouterr().out.startswith("DECLARE fixture_run_id")


def test_execute_requires_explicit_mode_and_credential_preflight(monkeypatch):
    with pytest.raises(SystemExit) as no_mode:
        fixture.main([])
    assert no_mode.value.code == 2
    with pytest.raises(SystemExit) as no_credential:
        fixture.main(["--execute"])
    assert no_credential.value.code == 2

    def rejected(_path):
        raise ValueError("credential metadata rejected before SDK initialization")

    monkeypatch.setattr(fixture, "approved_credential_path", rejected)
    with pytest.raises(ValueError, match="before SDK initialization"):
        fixture.main(["--execute", "--credential-file", "/not-an-approved-key.json"])


def test_fixture_has_distinct_actions_one_duplicate_and_one_other_family():
    rows = fixture._envelopes()
    news = [row for row in rows if row["event_family"] == "news_usage"]
    assert len(rows) == 12
    assert len(news) == 11
    assert len({row["event_id"] for row in news}) == 10
    assert news[-1] == news[0]
