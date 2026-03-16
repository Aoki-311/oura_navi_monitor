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
        self.assertGreaterEqual(html.count('class="chartWrap"'), 4)
        self.assertIn('id="kpiCardsPrimary"', html)
        self.assertIn('id="kpiCardsSecondary"', html)
        self.assertIn('data-preset="today"', html)
        self.assertIn('id="startAt"', html)
        self.assertIn('id="endAt"', html)
        css = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".chartWrap {", css)
        self.assertIn("height: clamp(", css)

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

if __name__ == "__main__":
    unittest.main()
