from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SecurityAndUiGuardrailsTest(unittest.TestCase):
    def test_no_row_innerhtml_for_remote_data(self) -> None:
        js = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        # Guard against XSS-prone row rendering patterns.
        self.assertNotIn("tr.innerHTML =", js)

    def test_chart_wrap_exists_for_all_primary_charts(self) -> None:
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertGreaterEqual(html.count('class="chartWrap"'), 5)
        self.assertIn('id="kpiCardsPrimary"', html)
        self.assertIn('id="kpiCardsSecondary"', html)
        self.assertIn('data-preset="today"', html)
        self.assertIn('id="startAt"', html)
        self.assertIn('id="endAt"', html)
        self.assertIn('id="systemUsageChart"', html)
        self.assertIn("システムリクエスト推移", html)
        self.assertIn('id="metricGuide"', html)
        self.assertIn("指標の見方（リクエスト口径）", html)
        css = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".chartWrap {", css)
        self.assertIn("height: clamp(", css)
        self.assertIn(".metricGuideGrid {", css)

    def test_favicon_routes_and_assets_present(self) -> None:
        main_py = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/favicon.ico"', main_py)
        self.assertIn('@app.head("/favicon.ico"', main_py)
        self.assertTrue((ROOT / "frontend" / "favicon.svg").exists())
        self.assertTrue((ROOT / "frontend" / "vendor" / "chart.umd.js.map").exists())

    def test_metrics_and_export_routes_support_time_window_query(self) -> None:
        metrics_py = (ROOT / "app" / "routers" / "metrics.py").read_text(encoding="utf-8")
        export_py = (ROOT / "app" / "routers" / "export.py").read_text(encoding="utf-8")
        self.assertIn('preset: str = Query(default="")', metrics_py)
        self.assertIn('start: str = Query(default="")', metrics_py)
        self.assertIn('end: str = Query(default="")', metrics_py)
        self.assertIn('preset: str = Query(default="")', export_py)
        self.assertIn('start: str = Query(default="")', export_py)
        self.assertIn('end: str = Query(default="")', export_py)

    def test_export_jobs_are_the_frontend_export_path(self) -> None:
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        client_js = (ROOT / "frontend" / "api" / "client.js").read_text(encoding="utf-8")
        export_py = (ROOT / "app" / "routers" / "export.py").read_text(encoding="utf-8")
        fs_py = (ROOT / "app" / "services" / "firestore_history.py").read_text(encoding="utf-8")

        self.assertIn('@router.post("/jobs")', export_py)
        self.assertIn('@router.get("/jobs/{job_id}")', export_py)
        self.assertIn('@router.get("/jobs/{job_id}/download")', export_py)
        self.assertIn("ExportJobRequest", export_py)
        self.assertIn("export_audit_json", export_py)
        self.assertIn("deprecated; use POST /api/export/jobs", export_py)
        self.assertIn("_raise_legacy_export_gone()", export_py)
        self.assertIn("save_export_job", fs_py)
        self.assertIn("get_export_job", fs_py)
        self.assertIn("export_message_detail_rows", fs_py)
        self.assertIn('createExportJob(body = {}, options = {})', client_js)
        self.assertIn('postJson("/api/export/jobs"', client_js)
        self.assertIn('id="exportCustomRange"', html)
        self.assertIn('name="userExportData" value="summary"', html)
        self.assertIn('name="userExportData" value="messages"', html)
        self.assertIn("createExportJob(", app_js)
        self.assertNotIn("/api/export/messages.csv", app_js)
        self.assertNotIn("/api/export/user-monitoring.csv", app_js)
        self.assertNotIn("exportIncludeContent", html + app_js)
        self.assertNotIn("startCsvDownload", app_js)

    def test_export_fixed_columns_are_business_readable(self) -> None:
        export_py = (ROOT / "app" / "routers" / "export.py").read_text(encoding="utf-8")
        for label in (
            "ユーザー監視一覧",
            "ユーザーサマリー",
            "メッセージ明細",
            "message原文",
            "質問カテゴリ",
            "モード",
            "デバイス",
            "フィードバック",
        ):
            self.assertIn(label, export_py)
        self.assertNotIn("includedFields", export_py)
        self.assertNotIn("personalInfoMode", export_py)

    def test_next_monitor_api_routes_are_registered(self) -> None:
        metrics_py = (ROOT / "app" / "routers" / "metrics.py").read_text(encoding="utf-8")
        trace_py = (ROOT / "app" / "routers" / "trace.py").read_text(encoding="utf-8")
        main_py = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        time_window = (ROOT / "app" / "time_window.py").read_text(encoding="utf-8")
        self.assertIn('@router.get("/system-dashboard")', metrics_py)
        self.assertIn('@router.get("/answer-quality")', metrics_py)
        self.assertIn('@router.get("/followup")', metrics_py)
        self.assertIn('@router.get("/users")', metrics_py)
        self.assertIn('@router.get("/users/{user_id}")', metrics_py)
        self.assertIn('@router.get("/schema-health")', metrics_py)
        self.assertIn('prefix="/api/trace"', trace_py)
        self.assertIn('@router.get("/messages")', trace_py)
        self.assertIn("app.include_router(trace_router)", main_py)
        self.assertIn('"last_3d"', time_window)
        self.assertIn('"last_30d"', time_window)

    def test_bigquery_projection_views_extract_monitor_events(self) -> None:
        sql = (ROOT / "sql" / "create_views.sql").read_text(encoding="utf-8")
        for view_name in (
            "v_ask_audit_events",
            "v_followup_resolution_events",
            "v_followup_open_result_events",
            "v_coverage_gap_workitems",
            "v_request_user_metric_events",
            "v_answer_action_events",
            "v_monitor_event_message_join_keys",
            "v_monitor_excluded_identities",
        ):
            self.assertIn(view_name, sql)
        for field_name in (
            "conversation_turn_key",
            "conversation_message_key",
            "trace_request_key",
            "raw_payload_json",
            "coverage_score",
            "decision_normalized",
            "gap_kind",
            "device_class",
            "answer_action_json",
            "target_message_id",
            "intent_family",
            "question_category",
            "raw_query_intent",
            "raw_domain_pack",
        ):
            self.assertIn(field_name, sql)

    def test_system_dashboard_uses_physical_aggregate_with_view_fallback(self) -> None:
        metrics_py = (ROOT / "app" / "routers" / "metrics.py").read_text(encoding="utf-8")
        bq_py = (ROOT / "app" / "services" / "bigquery_metrics.py").read_text(encoding="utf-8")
        self.assertIn("bq.get_system_dashboard_metrics(window=window)", metrics_py)
        self.assertIn("def get_system_dashboard_metrics", bq_py)
        self.assertIn("def _get_system_dashboard_metrics_from_snapshot", bq_py)
        self.assertIn("def _get_system_dashboard_metrics_from_tables", bq_py)
        self.assertIn('self._table_exists("monitor_dashboard_snapshots")', bq_py)
        self.assertIn("list_rows(table, max_results=50)", bq_py)
        self.assertIn('payload.get("questionCategory")', bq_py)
        self.assertIn('payload.get("snapshotContract") != "monitor_exclusions_v2"', bq_py)
        self.assertIn("aggregate_contract_version = 'monitor_exclusions_v2'", bq_py)
        self.assertIn('"usability" not in answer_quality', bq_py)
        self.assertIn('self._table_exists("monitor_system_hourly")', bq_py)
        self.assertIn('self._table("monitor_system_hourly")', bq_py)
        self.assertIn('self._view("v_request_user_metric_events")', bq_py)
        self.assertIn('self._view("v_ask_audit_events")', bq_py)
        self.assertIn('self._view("v_followup_resolution_events")', bq_py)
        self.assertIn('self._view("v_followup_open_result_events")', bq_py)
        self.assertIn("system-dashboard", metrics_py)
        self.assertIn("cacheHit", metrics_py)
        self.assertIn('"replacementEndpoint": "/api/metrics/system-dashboard"', metrics_py)
        self.assertIn('"deprecated": True', metrics_py)
        self.assertIn('"legacyEndpoint": "/api/metrics/dashboard"', metrics_py)
        self.assertIn("fs.aggregate_monitor_metrics", metrics_py)

    def test_physical_aggregate_tables_are_defined_and_used(self) -> None:
        aggregate_sql = (ROOT / "sql" / "create_aggregate_tables.sql").read_text(encoding="utf-8")
        bootstrap = (ROOT / "scripts" / "bootstrap_gcp.sh").read_text(encoding="utf-8")
        bq_py = (ROOT / "app" / "services" / "bigquery_metrics.py").read_text(encoding="utf-8")
        self.assertIn("CREATE OR REPLACE TABLE `__PROJECT_ID__.__DATASET_ID__.monitor_answer_events`", aggregate_sql)
        self.assertIn("CREATE OR REPLACE TABLE `__PROJECT_ID__.__DATASET_ID__.monitor_user_daily`", aggregate_sql)
        self.assertIn("CREATE OR REPLACE TABLE `__PROJECT_ID__.__DATASET_ID__.monitor_system_hourly`", aggregate_sql)
        self.assertIn("CREATE OR REPLACE TABLE `__PROJECT_ID__.__DATASET_ID__.monitor_dashboard_snapshots`", aggregate_sql)
        self.assertIn("active_user_hll", aggregate_sql)
        self.assertIn("followup_success_count", aggregate_sql)
        self.assertIn("payload_json", aggregate_sql)
        self.assertIn("'last_30d'", aggregate_sql)
        self.assertIn("'last_60d'", aggregate_sql)
        self.assertIn("'all'", aggregate_sql)
        self.assertIn("__MONITOR_RETENTION_DAYS__", aggregate_sql)
        self.assertIn("answer_success_flag", aggregate_sql)
        self.assertIn("answer_success_metric_status", aggregate_sql)
        self.assertIn("answer_success_cutover", aggregate_sql)
        self.assertIn("TIMESTAMP('__ANSWER_SUCCESS_OFFICIAL_CUTOVER_TS__')", aggregate_sql)
        self.assertIn("answerability_level NOT IN ('not_answerable', 'clarification_blocked')", aggregate_sql)
        self.assertIn("proxy_answerability_failure", aggregate_sql)
        self.assertNotIn("first_answer_action_event_ts", aggregate_sql)
        self.assertIn("v_answer_action_events", aggregate_sql)
        self.assertIn("has_bad_feedback", aggregate_sql)
        self.assertIn("regenerate_requested", aggregate_sql)
        self.assertIn("enhance_requested", aggregate_sql)
        self.assertIn("correction_requested", aggregate_sql)
        self.assertIn("low_coverage_flag", aggregate_sql)
        self.assertIn("question_category_distribution", aggregate_sql)
        self.assertIn("questionCategory", aggregate_sql)
        self.assertIn("question_category_source", aggregate_sql)
        self.assertIn("topic_ideation", aggregate_sql)
        self.assertIn("'monitor_exclusions_v2' AS aggregate_contract_version", aggregate_sql)
        self.assertIn("'monitor_exclusions_v2' AS snapshotContract", aggregate_sql)
        self.assertIn("2401145@tc.terumo.co.jp", aggregate_sql)
        self.assertIn("109382080128482733156", aggregate_sql)
        self.assertIn("102048678887357191337", aggregate_sql)
        self.assertIn("lcs-agent@lcs-developer-483404.iam.gserviceaccount.com", aggregate_sql)
        self.assertIn("v_request_user_metric_events", aggregate_sql)
        self.assertIn("AGGREGATE_SQL_TEMPLATE", bootstrap)
        self.assertIn("ANSWER_SUCCESS_OFFICIAL_CUTOVER_TS", bootstrap)
        self.assertIn("RETENTION_DAYS", bootstrap)
        self.assertTrue((ROOT / "scripts" / "refresh_aggregate_tables.sh").exists())
        self.assertTrue((ROOT / "scripts" / "setup_aggregate_refresh.sh").exists())
        self.assertIn('self._table_exists("monitor_user_daily")', bq_py)
        self.assertIn('self._table_exists("monitor_answer_events")', bq_py)
        self.assertIn('self._table_exists("monitor_system_hourly")', bq_py)
        self.assertIn('self._table_exists("monitor_dashboard_snapshots")', bq_py)
        self.assertIn("def _get_answer_quality_metrics_from_table", bq_py)
        self.assertIn("def _get_user_detail_summary_from_tables", bq_py)

    def test_user_detail_is_lightweight_and_message_lazy_loaded(self) -> None:
        metrics_py = (ROOT / "app" / "routers" / "metrics.py").read_text(encoding="utf-8")
        bq_py = (ROOT / "app" / "services" / "bigquery_metrics.py").read_text(encoding="utf-8")
        fs_py = (ROOT / "app" / "services" / "firestore_history.py").read_text(encoding="utf-8")
        self.assertIn("conversation_limit: int = Query(default=50", metrics_py)
        self.assertIn('user_email: str = Query(default="")', metrics_py)
        self.assertIn('user_id_hash: str = Query(default="")', metrics_py)
        self.assertIn("include_messages: bool = Query(default=False)", metrics_py)
        self.assertIn("fs.resolve_user_profile", metrics_py)
        self.assertIn("user_keys=[canonical_user_id, user_id, user_email, user_id_hash, profile_email]", metrics_py)
        self.assertIn("executor.submit(\n                fs.list_user_conversation_summaries,", metrics_py)
        self.assertNotIn("fs.get_user_detail_metrics(user_id=user_id", metrics_py)
        self.assertIn('"endpoint": "/api/trace/messages"', metrics_py)
        self.assertIn('"questionCategoryDistribution"', metrics_py)
        self.assertIn('"deviceDistribution"', metrics_py)
        self.assertIn("def get_user_detail_summary", bq_py)
        self.assertIn("user_keys: List[str] | None = None", bq_py)
        self.assertIn('bigquery.ArrayQueryParameter("user_keys"', bq_py)
        self.assertIn("def get_user_profile", fs_py)
        self.assertIn("def resolve_user_profile", fs_py)
        self.assertIn("def list_user_conversation_summaries", fs_py)

    def test_trace_messages_is_paginated_and_preview_only_by_default(self) -> None:
        trace_py = (ROOT / "app" / "routers" / "trace.py").read_text(encoding="utf-8")
        fs_py = (ROOT / "app" / "services" / "firestore_history.py").read_text(encoding="utf-8")
        api_doc = (ROOT / "docs" / "MONITOR_API_CONTRACT.md").read_text(encoding="utf-8")
        self.assertIn('cursor: str = Query(default="")', trace_py)
        self.assertIn("include_content: bool = Query(default=False)", trace_py)
        self.assertIn("candidates=payload_events", trace_py)
        self.assertNotIn("resolved_user_id = user_id", trace_py)
        self.assertIn("cursor: str = \"\"", fs_py)
        self.assertIn("include_content: bool = False", fs_py)
        self.assertIn("candidates: List[Dict[str, Any]] | None = None", fs_py)
        self.assertIn('"contentPolicy"', fs_py)
        self.assertIn('if include_content:', fs_py)
        self.assertIn('message_row["content"]', fs_py)
        self.assertIn('"contentPreview": _content_preview', fs_py)
        self.assertIn('"nextCursor"', fs_py)
        self.assertIn("candidate_pairs", fs_py)
        self.assertIn("candidate_category_by_turn", fs_py)
        self.assertIn("candidate_category_by_conv_turn", fs_py)
        self.assertIn('"questionCategory"', fs_py)
        self.assertIn("def _business_question_category", fs_py)
        self.assertIn("topic_ideation", fs_py)
        self.assertIn("include_content", api_doc)
        self.assertIn("通常画面では `contentPreview` のみ", api_doc)

    def test_local_gcloud_cli_auth_fallback_is_opt_in(self) -> None:
        settings_py = (ROOT / "app" / "settings.py").read_text(encoding="utf-8")
        auth_py = (ROOT / "app" / "services" / "google_auth.py").read_text(encoding="utf-8")
        bq_py = (ROOT / "app" / "services" / "bigquery_metrics.py").read_text(encoding="utf-8")
        fs_py = (ROOT / "app" / "services" / "firestore_history.py").read_text(encoding="utf-8")
        self.assertIn("monitor_use_gcloud_cli_auth", settings_py)
        self.assertIn("gcloud", auth_py)
        self.assertIn("print-access-token", auth_py)
        self.assertIn("get_gcloud_cli_credentials_if_enabled", bq_py)
        self.assertIn("get_gcloud_cli_credentials_if_enabled", fs_py)

    def test_bigquery_time_bucket_sql_uses_generate_array(self) -> None:
        metrics = (ROOT / "app" / "services" / "bigquery_metrics.py").read_text(encoding="utf-8")
        self.assertNotIn("GENERATE_DATETIME_ARRAY", metrics)
        self.assertIn("GENERATE_ARRAY(", metrics)
        self.assertNotIn("),\nWITH grid AS", metrics)

    def test_firestore_history_uses_keyword_filter_api(self) -> None:
        history = (ROOT / "app" / "services" / "firestore_history.py").read_text(encoding="utf-8")
        self.assertIn("FieldFilter", history)
        self.assertIn(".where(filter=FieldFilter(", history)

    def test_dashboard_frontend_uses_partial_failure_tolerant_loading(self) -> None:
        js = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Promise.allSettled(", js)
        self.assertIn("一部データの取得に失敗しました", js)
        self.assertIn("DASHBOARD_FETCH_TIMEOUT_MS", js)
        self.assertIn("renderSystemUsageChart", js)
        self.assertIn("renderQuestionCategory", js)
        self.assertIn("core_request_count", js)
        self.assertIn("isExcludedUser", js)
        self.assertIn("109382080128482733156", js)
        self.assertIn("102048678887357191337", js)
        self.assertIn("currentUserDetailContext", js)
        self.assertIn("user_id_hash=", js)
        self.assertIn("preserveConversation: true", js)

    def test_dashboard_answer_quality_ui_is_reduced_to_two_business_metrics(self) -> None:
        dashboard_adapter = (ROOT / "frontend" / "adapters" / "dashboardAdapter.js").read_text(encoding="utf-8")
        index_html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertIn('title: "回答利用可能性"', dashboard_adapter)
        self.assertIn('title: "根拠十分性"', dashboard_adapter)
        self.assertNotIn('title: "回答可能性"', dashboard_adapter)
        self.assertNotIn('title: "業務利用可能性"', dashboard_adapter)
        self.assertIn("OurA Navi 運用モニター</a>", index_html)

    def test_question_category_uses_six_business_labels_without_uncategorized_ui(self) -> None:
        labels_js = (ROOT / "frontend" / "viewModels" / "labels.js").read_text(encoding="utf-8")
        sql = (ROOT / "sql" / "create_views.sql").read_text(encoding="utf-8")
        for label in (
            "製品説明",
            "営業手法",
            "トラブル対応",
            "製品価格関連",
            "病院・GPO関連",
            "ネタ探し",
        ):
            self.assertIn(label, labels_js)
        self.assertNotIn("未分類", labels_js)
        self.assertIn("ELSE 'topic_ideation'", sql)
        self.assertIn("'product_explanation'", sql)
        self.assertIn("'sales_approach'", sql)
        self.assertIn("'troubleshooting'", sql)
        self.assertIn("'product_price'", sql)
        self.assertIn("'hospital_gpo'", sql)

if __name__ == "__main__":
    unittest.main()
