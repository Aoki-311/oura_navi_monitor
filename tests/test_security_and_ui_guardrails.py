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
