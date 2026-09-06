#!/usr/bin/env python3
"""Execute the canonical News publisher against anonymous BigQuery TEMP fixtures.

--render-only is entirely local. --execute submits one bounded BigQuery script
after credential preflight, and never references a persistent dataset or table.
This is SQL correctness evidence, not a deployment or live-data acceptance test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
import sys
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql"
sys.path.insert(0, str(ROOT))

from scripts.credential_preflight import approved_credential_path


SOURCE_SERVICE = "lcs-rag-app"
TABLE_SCHEMAS = {
    "pipeline_state": "create_aggregates.sql",
    "pipeline_runs": "create_aggregates.sql",
    "pipeline_run_event_manifest": "create_fact_tables.sql",
    "user_scope": "create_fact_tables.sql",
    "news_usage_events": "create_news_usage_tables.sql",
    "news_usage_event_issues": "create_news_usage_tables.sql",
}
TABLE_NAMES = {*TABLE_SCHEMAS, "news_usage_event_source", "run_googleapis_com_stdout"}
PARAMETERS = {
    "run_id": ("STRING", "fixture-run"),
    "lease_id": ("STRING", "fixture-lease"),
    "expected_watermark": ("TIMESTAMP", None),
    "window_start": ("TIMESTAMP", "2026-09-03T00:00:00Z"),
    "window_end": ("TIMESTAMP", "2026-09-03T01:00:00Z"),
    "measurement_start": ("TIMESTAMP", "2026-09-01T00:00:00Z"),
    "event_future_tolerance_minutes": ("INT64", 5),
    "source_service": ("STRING", SOURCE_SERVICE),
    "roster_snapshot_run_id": ("STRING", "fixture-chat-roster"),
    "scope_policy_version": ("STRING", "summary_role_v1"),
    "global_roster_fingerprint": ("STRING", "fixture-global-roster"),
    "global_content_fingerprint": ("STRING", "fixture-global-content"),
    "user_map_roster_fingerprint": ("STRING", "fixture-user-map-roster"),
    "user_map_content_fingerprint": ("STRING", "fixture-user-map-content"),
}


def _literal(value: object) -> str:
    return "NULL" if value is None else json.dumps(value, ensure_ascii=False)


def _localize(sql: str) -> str:
    """Only exact known table owners can be redirected into this SQL session."""
    for name in TABLE_NAMES:
        sql = sql.replace(
            f"`${{PROJECT_ID}}.${{DATASET_ID}}.{name}`", f"`_SESSION.fixture_{name}`"
        )
    for key, value in {
        "NEWS_USAGE_SOURCE_SERVICE": SOURCE_SERVICE,
        "NEWS_USAGE_CONTRACT_VERSION": "news_usage_v1",
        "MONITOR_TIMEZONE": "Asia/Tokyo",
    }.items():
        sql = sql.replace("${" + key + "}", value)
    if "${" in sql:
        raise ValueError("unrecognized SQL placeholder or persistent table owner")

    def parameter(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in PARAMETERS:
            raise ValueError(f"unrecognized publisher parameter: {name}")
        return "fixture_" + name

    return re.sub(r"(?<!@)@([a-z_]+)", parameter, sql)


def _schema(name: str, filename: str) -> str:
    source = (SQL_DIR / filename).read_text(encoding="utf-8")
    pattern = (
        r"CREATE TABLE IF NOT EXISTS `\$\{PROJECT_ID\}\.\$\{DATASET_ID\}\."
        + re.escape(name) + r"` \(\n(.*?)\n\)"
    )
    matches = re.findall(pattern, source, flags=re.DOTALL)
    if len(matches) != 1:
        raise ValueError(f"canonical schema must have exactly one owner: {name}")
    return f"CREATE TEMP TABLE `_SESSION.fixture_{name}` (\n{matches[0]}\n);"


def _envelopes() -> list[dict]:
    envelopes = []
    actions = [
        ("detail_view", "primary"), ("detail_view", "primary"),
        ("outbound_click", "primary"), ("outbound_click", "primary"),
        ("outbound_click", "primary"), ("outbound_click", "evidence"),
        ("outbound_click", "registration"), ("tab_view", "primary"),
        ("detail_view", "primary"), ("detail_view", "primary"),
    ]
    for index, (name, link) in enumerate(actions):
        usage_id = str(UUID(int=index + 1))
        payload = {
            "schema_version": "news_usage_v1", "usage_event_id": usage_id,
            "page_view_id": str(UUID(int=100)), "event_name": name,
            "channel": "society" if index == 9 else "news",
            "occurred_at": "2026-09-02T15:05:00Z",
            "content_event_id": "fixture-article", "content_event_version": "v1",
            "content_event_type": "regulatory_safety",
            "content_geography_scope": "overseas" if index == 4 else "domestic",
            "content_domain_key": "diabetes", "content_source_id": "jds",
            "content_category_key": "糖尿病関連", "source_catalog_version": "fixture-v1",
            "surface": "detail", "link_kind": link,
        }
        if name == "tab_view":
            payload["trigger"] = "initial"
        if index == 8:
            payload.pop("content_event_type")
            payload.pop("content_geography_scope")
            payload["content_event_id"] = "fixture-unclassified"
        if index == 9:
            payload["content_event_id"] = "fixture-society"
            payload["content_source_id"] = "jadec"
        envelopes.append({
            "monitor_event": True, "event_family": "news_usage",
            "monitor_contract_version": "news_usage_v1",
            "event_id": "news_usage:" + hashlib.sha256(
                ("fixture-subject\n" + usage_id).encode()
            ).hexdigest(),
            "event_ts": payload["occurred_at"], "user_id": "fixture-subject",
            "service_name": SOURCE_SERVICE, "metadata_issues": [],
            "payload_json": json.dumps(payload, ensure_ascii=False),
        })
    envelopes.append(dict(envelopes[0]))  # Same action delivered twice.
    envelopes.append({**envelopes[0], "event_family": "future_chat_family"})
    return envelopes


def assert_temp_only(sql: str) -> None:
    # Ignore data literals and comments before checking SQL identifiers. In
    # particular, anonymous JSON fixture strings are data rather than SQL.
    code = re.sub(
        r"--[^\n]*|/\*.*?\*/|(?:r)?'(?:\\.|''|[^'\\])*'|\"(?:\\.|\"\"|[^\"\\])*\"",
        " ", sql, flags=re.DOTALL | re.I,
    )
    quoted_names = re.findall(r"`([^`]+)`", code)
    allowed = {f"_SESSION.fixture_{name}" for name in TABLE_NAMES}
    if any(name not in allowed for name in quoted_names) or "${" in sql:
        raise ValueError("persistent or unrecognized table reference remains")
    if re.search(r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW)\b", code, re.I):
        raise ValueError("persistent DDL is forbidden in fixture validation")
    if re.search(r"\b(?:EXPORT\s+DATA|EXECUTE\s+IMMEDIATE|CALL|EXTERNAL_QUERY)\b", code, re.I):
        raise ValueError("external or dynamic execution is forbidden")
    identifier = r"(`[^`]+`|[A-Za-z_][\w.]*)"
    temporary = {
        name.strip("`") for name in re.findall(
            r"CREATE\s+TEMP\s+TABLE\s+" + identifier, code, re.I,
        )
    }
    ctes = set(re.findall(r"(?:WITH|,)\s*([A-Za-z_]\w*)\s+AS\s*\(", code, re.I))
    references = re.findall(
        r"\b(?:FROM|JOIN|INTO|TABLE|MERGE|UPDATE(?!\s+SET\b))\s+" + identifier,
        code, re.I,
    )
    for name in references:
        name = name.strip("`")
        if name not in temporary | ctes and name.upper() != "UNNEST":
            raise ValueError(f"table is not a TEMP fixture or CTE: {name}")


def render_fixture_sql() -> str:
    declarations = [
        f"DECLARE fixture_{name} {kind} DEFAULT "
        + (f"TIMESTAMP({_literal(value)})" if kind == "TIMESTAMP" and value else _literal(value))
        + ";" for name, (kind, value) in PARAMETERS.items()
    ]
    schemas = [_schema(name, filename) for name, filename in TABLE_SCHEMAS.items()]
    news_schema = (SQL_DIR / "create_news_usage_tables.sql").read_text(encoding="utf-8")
    # Preserve the canonical additive pipeline schema instead of duplicating it.
    pipeline_alter = re.search(
        r"ALTER TABLE `\$\{PROJECT_ID\}\.\$\{DATASET_ID\}\.pipeline_state`\s+.*?;",
        news_schema, flags=re.DOTALL,
    )
    if not pipeline_alter:
        raise ValueError("News pipeline schema extension is missing")
    schemas.append(_localize(pipeline_alter.group(0)))
    schemas.append("""CREATE TEMP TABLE `_SESSION.fixture_run_googleapis_com_stdout` (
      timestamp TIMESTAMP, insertId STRING, textPayload STRING,
      resource STRUCT<labels STRUCT<service_name STRING, revision_name STRING>>
    );""")
    inserts = []
    for index, envelope in enumerate(_envelopes()):
        inserts.append(
            "INSERT INTO `_SESSION.fixture_run_googleapis_com_stdout` VALUES ("
            "TIMESTAMP('2026-09-03T00:01:00Z'), " + _literal(f"fixture-delivery-{index}")
            + ", " + _literal(json.dumps(envelope, ensure_ascii=False))
            + ", STRUCT(STRUCT(" + _literal(SOURCE_SERVICE)
            + " AS service_name, 'fixture-revision' AS revision_name) AS labels));"
        )
    setup = """
INSERT INTO `_SESSION.fixture_pipeline_state` (
  source, status, published_run_id, scope_policy_version,
  global_roster_fingerprint, global_content_fingerprint,
  user_map_roster_fingerprint, user_map_content_fingerprint, updated_at
) VALUES ('published', 'succeeded', fixture_roster_snapshot_run_id, fixture_scope_policy_version,
  fixture_global_roster_fingerprint, fixture_global_content_fingerprint,
  fixture_user_map_roster_fingerprint, fixture_user_map_content_fingerprint, CURRENT_TIMESTAMP());
INSERT INTO `_SESSION.fixture_pipeline_state` (
  source, status, lease_run_id, lease_expires_at, source_service, measurement_start_at, updated_at
) VALUES ('news_usage', 'running', fixture_lease_id, TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR),
  fixture_source_service, fixture_measurement_start, CURRENT_TIMESTAMP());
INSERT INTO `_SESSION.fixture_pipeline_runs` (
  run_id, source, status, started_at, window_start, window_end
) VALUES (fixture_run_id, 'news_usage', 'running', CURRENT_TIMESTAMP(), fixture_window_start, fixture_window_end);
INSERT INTO `_SESSION.fixture_user_scope` (
  snapshot_run_id, snapshot_created_at, roster_id, user_id, email,
  updated_at, is_active, global_scope_enabled, user_map_scope_enabled
) VALUES (fixture_roster_snapshot_run_id, CURRENT_TIMESTAMP(), 'fixture-roster', 'fixture-subject',
  'fixture@example.invalid', CURRENT_TIMESTAMP(), TRUE, TRUE, TRUE);
CREATE TEMP TABLE fixture_chat_pointer_before AS
SELECT TO_JSON_STRING(state) AS serialized FROM `_SESSION.fixture_pipeline_state` state WHERE source = 'published';
"""
    source = _localize((SQL_DIR / "create_news_usage_source.sql").read_text(encoding="utf-8"))
    source = source.replace("CREATE OR REPLACE VIEW", "CREATE TEMP TABLE", 1)
    publisher = _localize((SQL_DIR / "publish_news_usage.sql").read_text(encoding="utf-8"))
    assertions = """
ASSERT (SELECT COUNT(*) FROM `_SESSION.fixture_news_usage_events`) = 10 AS 'unique action count';
ASSERT (SELECT COUNT(*) FROM `_SESSION.fixture_news_usage_events`
  WHERE content_event_id = 'fixture-article' AND channel = 'news'
    AND (event_name = 'detail_view' OR (event_name = 'outbound_click' AND link_kind = 'primary'))
) = 5 AS 'two details plus three primary links preserve repeated article clicks';
ASSERT (SELECT COUNT(*) FROM `_SESSION.fixture_news_usage_events`
  WHERE content_event_id = 'fixture-article' AND event_name = 'outbound_click'
    AND link_kind IN ('evidence', 'registration')) = 2 AS 'secondary links retained but excluded from primary clicks';
ASSERT (SELECT COUNT(*) FROM `_SESSION.fixture_news_usage_events`
  WHERE content_event_type = 'regulatory_safety' AND channel = 'news' AND content_geography_scope = 'domestic'
    AND (event_name = 'detail_view' OR (event_name = 'outbound_click' AND link_kind = 'primary'))
) = 4 AS 'article category and domestic attribution';
ASSERT (SELECT COUNT(*) FROM `_SESSION.fixture_news_usage_events`
  WHERE content_event_type = 'regulatory_safety' AND channel = 'news' AND content_geography_scope = 'overseas'
    AND (event_name = 'detail_view' OR (event_name = 'outbound_click' AND link_kind = 'primary'))
) = 1 AS 'overseas attribution';
ASSERT (SELECT COUNT(*) FROM `_SESSION.fixture_news_usage_events`
  WHERE content_event_id = 'fixture-unclassified' AND content_event_type IS NULL AND content_geography_scope IS NULL
) = 1 AS 'missing optional classification preserves action';
ASSERT (SELECT COUNT(*) FROM `_SESSION.fixture_news_usage_events`
  WHERE channel = 'society' AND content_category_key = '糖尿病関連' AND content_source_id = 'jadec'
) = 1 AS 'society category and actual source preserved';
ASSERT (SELECT COUNT(*) FROM `_SESSION.fixture_news_usage_events`
  WHERE usage_date_jst = DATE '2026-09-03' AND content_event_version = 'v1'
) = 10 AS 'JST occurrence date and version retained';
ASSERT (SELECT COUNT(*) FROM `_SESSION.fixture_pipeline_run_event_manifest`
  WHERE disposition = 'deduplicated') = 1 AS 'same event ID delivery deduplicated';
ASSERT (SELECT COUNT(*) FROM `_SESSION.fixture_news_usage_event_issues`
  WHERE disposition = 'row_quarantined') = 0 AS 'valid fixtures do not quarantine';
ASSERT (SELECT TO_JSON_STRING(state) FROM `_SESSION.fixture_pipeline_state` state WHERE source = 'published')
  = (SELECT serialized FROM fixture_chat_pointer_before) AS 'Chat publication pointer unchanged';
ASSERT EXISTS (SELECT 1 FROM `_SESSION.fixture_pipeline_state`
  WHERE source = 'news_usage' AND status = 'succeeded' AND published_run_id = fixture_run_id
    AND data_through = fixture_window_end AND lease_run_id IS NULL) AS 'News cursor publishes independently';
SELECT 'passed' AS fixture_status, 10 AS unique_actions, 5 AS repeated_article_clicks;
"""
    sql = "\n\n".join([*declarations, *schemas, *inserts, setup, source, publisher, assertions])
    assert_temp_only(sql)
    return sql


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--render-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--credential-file")
    parser.add_argument("--project", choices=["lcs-developer-483404"], default="lcs-developer-483404")
    args = parser.parse_args(argv)
    sql = render_fixture_sql()
    if args.render_only:
        print(sql)
        return 0
    if not args.credential_file:
        parser.error("--execute requires --credential-file")
    credential_path = approved_credential_path(args.credential_file)
    from google.cloud import bigquery
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(str(credential_path))
    client = bigquery.Client(project=args.project, credentials=credentials)
    job = client.query(sql, location="US", job_config=bigquery.QueryJobConfig(
        maximum_bytes_billed=100 * 1024 * 1024, use_query_cache=False,
        labels={"purpose": "news-usage-temp-validation"},
    ))
    rows = list(job.result())
    if len(rows) != 1 or rows[0]["fixture_status"] != "passed":
        raise RuntimeError("fixture SQL did not return the expected assertion receipt")
    print(json.dumps({
        "status": "passed", "jobId": job.job_id, "project": args.project,
        "location": "US", "sqlSha256": hashlib.sha256(sql.encode()).hexdigest(),
        "persistentTablesReferenced": False,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
