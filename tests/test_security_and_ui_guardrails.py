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
            "v_monitor_event_message_join_keys",
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
        self.assertIn('self._table_exists("monitor_system_hourly")', bq_py)
        self.assertIn('self._table("monitor_system_hourly")', bq_py)
        self.assertIn('self._view("v_requests")', bq_py)
        self.assertIn('self._view("v_request_user_metric_events")', bq_py)
        self.assertIn('self._view("v_ask_audit_events")', bq_py)
        self.assertIn('self._view("v_followup_resolution_events")', bq_py)
        self.assertIn('self._view("v_followup_open_result_events")', bq_py)
        self.assertIn("system-dashboard", metrics_py)
        self.assertIn("cacheHit", metrics_py)

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
        self.assertIn("answer_success_flag", aggregate_sql)
        self.assertIn("answer_success_metric_status", aggregate_sql)
        self.assertIn("low_coverage_flag", aggregate_sql)
        self.assertIn("AGGREGATE_SQL_TEMPLATE", bootstrap)
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
        self.assertIn("include_messages: bool = Query(default=False)", metrics_py)
        self.assertIn("executor.submit(bq.get_user_detail_summary, window=window, user_key=user_id)", metrics_py)
        self.assertIn("executor.submit(fs.get_user_profile, user_id=user_id)", metrics_py)
        self.assertIn("executor.submit(\n                fs.list_user_conversation_summaries,", metrics_py)
        self.assertNotIn("fs.get_user_detail_metrics(user_id=user_id", metrics_py)
        self.assertIn('"endpoint": "/api/trace/messages"', metrics_py)
        self.assertIn("def get_user_detail_summary", bq_py)
        self.assertIn("def get_user_profile", fs_py)
        self.assertIn("def list_user_conversation_summaries", fs_py)

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
        self.assertIn("core_request_count", js)

if __name__ == "__main__":
    unittest.main()
