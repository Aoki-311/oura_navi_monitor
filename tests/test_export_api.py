import csv
import io
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.dependencies import get_analytics_service, get_export_job_repository
from app.main import app
from app.settings import get_settings


_WINDOW_RESPONSE = {
    "windowStart": "2026-08-17T00:00:00Z",
    "windowEnd": "2026-08-24T00:00:00Z",
    "windowTimezone": "Asia/Tokyo",
}
_EXPORT_SNAPSHOT = {
    "expectedContentFingerprint": "content-fingerprint-1",
    "expectedWindowStart": _WINDOW_RESPONSE["windowStart"],
    "expectedWindowEnd": _WINDOW_RESPONSE["windowEnd"],
    "expectedWindowTimezone": _WINDOW_RESPONSE["windowTimezone"],
}


class _Analytics:
    def overview_users(self, **kwargs):
        assert kwargs["window"].start_utc.isoformat() == "2026-08-17T00:00:00+00:00"
        assert kwargs["window"].end_utc.isoformat() == "2026-08-24T00:00:00+00:00"
        return {
            "scope": "global",
            "scopePolicyVersion": "summary_role_v1",
            "rosterFingerprint": "roster-fingerprint-1",
            "contentFingerprint": "content-fingerprint-1",
            "publishedRunId": "run-1",
            **_WINDOW_RESPONSE,
            "contentDiagnostics": {
                "state": "complete",
                "labelCatalogStatus": "available",
                "rosterStatus": "available",
                "rosterIsolatedCount": 0,
                "rosterIssueCounts": {},
                "issues": [],
            },
            "scopeUserCount": 1,
            "freshness": {
                "state": "fresh",
                "dataThrough": "2026-08-24T00:00:00Z",
            },
            "users": [
                {
                    "rosterId": "roster_1",
                    "name": "利用者",
                    "email": "user@example.com",
                    "role": "本社MR",
                    "department": "DM専任",
                    "area": "関西",
                    "areaKey": "関西",
                    "workplace": "=危険な式",
                    "labels": [],
                    "lastActiveAt": "2026-08-24T00:00:00Z",
                    "activeDays7": 2,
                    "userMessageCount7": 4,
                    "completeDelivery": {
                        "value": 0.75,
                        "measuredCount": 3,
                        "totalCount": 4,
                        "measurementState": "partial",
                        "measurementReason": "historical_unavailable",
                    },
                    "activity": "middle",
                    "activityLabel": "中アクティブ",
                }
            ]
        }

    def user_detail(self, roster_id, **kwargs):
        assert kwargs["window"].start_utc.isoformat() == "2026-08-17T00:00:00+00:00"
        assert kwargs["window"].end_utc.isoformat() == "2026-08-24T00:00:00+00:00"
        no_usage = {
            "measuredCount": 0,
            "totalCount": 0,
            "measurementState": "no_usage",
            "measurementReason": "no_usage",
        }
        measured = {
            "measuredCount": 2,
            "totalCount": 2,
            "measurementState": "measured",
            "measurementReason": "complete",
        }
        axis = {**measured, "isolatedCount": 0}
        return {
            "scope": "user_map",
            "scopePolicyVersion": "summary_role_v1",
            "rosterFingerprint": "roster-fingerprint-1",
            "contentFingerprint": "content-fingerprint-1",
            "publishedRunId": "run-1",
            **_WINDOW_RESPONSE,
            "contentDiagnostics": {
                "state": "complete",
                "labelCatalogStatus": "available",
                "rosterStatus": "available",
                "rosterIsolatedCount": 0,
                "rosterIssueCounts": {},
                "issues": [],
            },
            "freshness": {"state": "fresh", "dataThrough": "2026-08-24T00:00:00Z"},
            "analyticsQuality": {
                "contractVersion": "dashboard_events_v2",
                "isolatedEventCount": 0,
                "totalEventCount": 2,
                "classification": dict(axis),
                "task": dict(axis),
                "product": dict(axis),
                "sourcePipeline": {
                    "publishedRunId": "run-1",
                    "latestRunId": "run-1",
                    "latestRunStatus": "succeeded",
                    "latestRunErrorCode": "",
                    "latestRunFinishedAt": "2026-08-24T00:00:00Z",
                    "state": "clean",
                    "quarantinedEventCount": 0,
                    "deduplicatedDeliveryCount": 0,
                    "repairedDuplicateFactCount": 0,
                    "axisUnmeasuredFindingCount": 0,
                    "batchBlockingFailureCount": 0,
                },
            },
            "profile": {
                "rosterId": roster_id,
                "name": "利用者",
                "email": "user@example.com",
                "area": "関西",
                "workplace": "大阪",
                "role": "本社MR",
                "department": "DM専任",
                "mrExperience": "8年",
                "labels": [],
            },
            "summary": {
                "lastActiveAt": "2026-08-24T00:00:00Z",
                "activeDays": 1,
                "questions": 2,
                "questionsPerActiveDay": 2.0,
                "completeDelivery": {"value": 1.0, **measured},
                "p95Latency": {"valueMs": 2500, **measured},
            },
            "comparisons": {
                "area": {"label": "関西", "peerCount": 0, "averageQuestions": None, "averageActiveDays": None, "averageCompleteDelivery": {"value": None, **no_usage}},
                "role": {"label": "本社MR", "peerCount": 0, "averageQuestions": None, "averageActiveDays": None, "averageCompleteDelivery": {"value": None, **no_usage}},
            },
            "trend": [{"date": "2026-08-24", "questions": 2, "completeDelivery": {"value": 1.0, **measured}, "isPartial": False}],
            "products": [{"label": "テルフュージョン", "count": 2}],
            "productResolution": {"candidateCount": 2, "resolvedCount": 2, "unresolvedQuestions": 0, "resolutionRate": 1.0, **measured},
            "tasks": [{"key": "fact_lookup", "label": "情報確認", "count": 2, "rate": 1.0}],
            "taskMeasurement": measured,
            "questionCategories": [{"key": "product_information", "label": "製品情報・仕様", "count": 2, "rate": 1.0}],
            "questionCategoryMeasurement": measured,
            "modes": [{"key": "internal", "label": "社内モード", "count": 2, "rate": 1.0}],
            "modeMeasurement": measured,
            "devices": [{"key": "desktop", "label": "PC", "count": 2, "rate": 1.0}],
            "deviceMeasurement": measured,
        }


class _DegradedAnalytics(_Analytics):
    @staticmethod
    def _degraded(payload: dict) -> dict:
        return {
            **payload,
            "contentDiagnostics": {
                "state": "degraded",
                "labelCatalogStatus": "unavailable",
                "rosterStatus": "available",
                "rosterIsolatedCount": 0,
                "rosterIssueCounts": {},
                "issues": ["label_catalog_unavailable"],
            },
        }

    def overview_users(self, **kwargs):
        return self._degraded(super().overview_users(**kwargs))

    def user_detail(self, roster_id, **kwargs):
        return self._degraded(super().user_detail(roster_id, **kwargs))


class _MissingDiagnosticsAnalytics(_Analytics):
    def overview_users(self, **kwargs):
        payload = super().overview_users(**kwargs)
        payload.pop("contentDiagnostics")
        return payload


class _InvalidDiagnosticsAnalytics(_Analytics):
    def user_detail(self, roster_id, **kwargs):
        payload = super().user_detail(roster_id, **kwargs)
        payload["contentDiagnostics"] = {
            "state": "complete",
            "labelCatalogStatus": "available",
            "issues": "not-an-array",
        }
        return payload


class _ContradictoryRosterDiagnosticsAnalytics(_Analytics):
    def overview_users(self, **kwargs):
        payload = super().overview_users(**kwargs)
        payload["contentDiagnostics"] = {
            "state": "complete",
            "labelCatalogStatus": "available",
            "rosterStatus": "partial",
            "rosterIsolatedCount": 1,
            "rosterIssueCounts": {"duplicate_email": 2},
            "issues": [],
        }
        return payload


class _Exports:
    def __init__(self) -> None:
        self.jobs = {}

    def put(self, job):
        self.jobs[job["job_id"]] = dict(job)
        return dict(job)

    def put_idempotent(self, job):
        existing = self.jobs.get(job["job_id"])
        if existing is not None:
            return dict(existing)
        return self.put(job)

    def cleanup_expired(self, *, limit=200):
        return 0

    def get(self, job_id):
        return self.jobs.get(job_id)

    def delete(self, job_id):
        self.jobs.pop(job_id, None)

    @staticmethod
    def is_expired(_job):
        return False


def _settings():
    return get_settings().model_copy(
        update={
            "monitor_admin_allowlist": "admin@example.com",
            "monitor_allow_unverified_local": True,
        }
    )


def test_export_is_job_only_owner_scoped_and_not_legacy_get_csv() -> None:
    exports = _Exports()
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_analytics_service] = _Analytics
    app.dependency_overrides[get_export_job_repository] = lambda: exports
    client = TestClient(app)
    headers = {"x-monitor-admin-email": "admin@example.com"}
    try:
        response = client.post(
            "/api/export/jobs",
            json={
                "kind": "overview_users",
                "preset": "last_7d",
                "expectedPublishedRunId": "run-1",
                "expectedRosterFingerprint": "roster-fingerprint-1",
                "expectedScopePolicyVersion": "summary_role_v1",
                **_EXPORT_SNAPSHOT,
                "idempotencyKey": "csv-test-key-1",
            },
            headers=headers,
        )
        assert response.status_code == 201
        job_id = response.json()["jobId"]
        download = client.get(f"/api/export/jobs/{job_id}/download", headers=headers)
        assert download.status_code == 200
        assert "利用者" in download.text
        assert "'=危険な式" in download.text
        assert "過去データに項目なし" in download.text
        assert "no-store" in download.headers["cache-control"]
        csv_rows = list(csv.DictReader(io.StringIO(download.text.lstrip("\ufeff"))))
        assert csv_rows[0]["分析開始時刻"] == _WINDOW_RESPONSE["windowStart"]
        assert csv_rows[0]["分析終了時刻"] == _WINDOW_RESPONSE["windowEnd"]
        assert csv_rows[0]["分析タイムゾーン"] == _WINDOW_RESPONSE["windowTimezone"]
        assert client.get("/api/export/users.csv", headers=headers).status_code == 404

        changed_snapshot = client.post(
            "/api/export/jobs",
            json={
                "kind": "overview_users",
                "preset": "last_7d",
                "expectedPublishedRunId": "older-run",
                "expectedRosterFingerprint": "roster-fingerprint-1",
                "expectedScopePolicyVersion": "summary_role_v1",
                **_EXPORT_SNAPSHOT,
                "idempotencyKey": "csv-test-key-snapshot",
            },
            headers=headers,
        )
        assert changed_snapshot.status_code == 409
        assert changed_snapshot.json()["detail"]["code"] == "snapshot_changed"

        changed_roster = client.post(
            "/api/export/jobs",
            json={
                "kind": "overview_users",
                "preset": "last_7d",
                "expectedPublishedRunId": "run-1",
                "expectedRosterFingerprint": "older-roster",
                "expectedScopePolicyVersion": "summary_role_v1",
                **_EXPORT_SNAPSHOT,
                "idempotencyKey": "csv-test-key-roster",
            },
            headers=headers,
        )
        assert changed_roster.status_code == 409
        assert changed_roster.json()["detail"]["code"] == "snapshot_changed"

        changed_content = client.post(
            "/api/export/jobs",
            json={
                "kind": "overview_users",
                "preset": "last_7d",
                "expectedPublishedRunId": "run-1",
                "expectedRosterFingerprint": "roster-fingerprint-1",
                "expectedScopePolicyVersion": "summary_role_v1",
                **{
                    **_EXPORT_SNAPSHOT,
                    "expectedContentFingerprint": "older-content",
                },
                "idempotencyKey": "csv-test-key-content",
            },
            headers=headers,
        )
        assert changed_content.status_code == 409
        assert changed_content.json()["detail"]["code"] == "snapshot_changed"

        changed_window = client.post(
            "/api/export/jobs",
            json={
                "kind": "overview_users",
                "preset": "last_7d",
                "expectedPublishedRunId": "run-1",
                "expectedRosterFingerprint": "roster-fingerprint-1",
                "expectedScopePolicyVersion": "summary_role_v1",
                **{**_EXPORT_SNAPSHOT, "expectedWindowTimezone": "UTC"},
                "idempotencyKey": "csv-test-key-window",
            },
            headers=headers,
        )
        assert changed_window.status_code == 409
        assert changed_window.json()["detail"]["code"] == "snapshot_changed"

        retried = client.post(
            "/api/export/jobs",
            json={
                "kind": "overview_users",
                "preset": "last_7d",
                "expectedPublishedRunId": "run-1",
                "expectedRosterFingerprint": "roster-fingerprint-1",
                "expectedScopePolicyVersion": "summary_role_v1",
                **_EXPORT_SNAPSHOT,
                "idempotencyKey": "csv-test-key-1",
            },
            headers=headers,
        )
        assert retried.status_code == 201
        assert retried.json()["jobId"] == job_id

        conflict = client.post(
            "/api/export/jobs",
            json={
                "kind": "overview_users",
                "preset": "last_7d",
                "q": "different",
                "expectedPublishedRunId": "run-1",
                "expectedRosterFingerprint": "roster-fingerprint-1",
                "expectedScopePolicyVersion": "summary_role_v1",
                **_EXPORT_SNAPSHOT,
                "idempotencyKey": "csv-test-key-1",
            },
            headers=headers,
        )
        assert conflict.status_code == 409

        exports.jobs[job_id]["created_by"] = "other@example.com"
        assert client.get(f"/api/export/jobs/{job_id}", headers=headers).status_code == 404
        exports.jobs[job_id]["created_by"] = "admin@example.com"
        assert client.delete(f"/api/export/jobs/{job_id}", headers=headers).status_code == 204
        assert client.get(f"/api/export/jobs/{job_id}", headers=headers).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_user_detail_csv_contains_all_analytics_axes_without_conversation_text() -> None:
    exports = _Exports()
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_analytics_service] = _Analytics
    app.dependency_overrides[get_export_job_repository] = lambda: exports
    client = TestClient(app)
    headers = {"x-monitor-admin-email": "admin@example.com"}
    try:
        response = client.post(
            "/api/export/jobs",
            json={
                "kind": "user_detail",
                "rosterId": "roster_1",
                "preset": "last_7d",
                "expectedPublishedRunId": "run-1",
                "expectedRosterFingerprint": "roster-fingerprint-1",
                "expectedScopePolicyVersion": "summary_role_v1",
                **_EXPORT_SNAPSHOT,
                "idempotencyKey": "csv-user-detail-1",
            },
            headers=headers,
        )
        assert response.status_code == 201
        download = client.get(response.json()["downloadUrl"], headers=headers)
        assert download.status_code == 200
        for expected in (
            "P95応答時間",
            "回答成功率",
            "テルフュージョン",
            "質問種類",
            "質問テーマ",
            "利用モード",
            "デバイス",
        ):
            assert expected in download.text
        assert "製品の仕様を教えてください" not in download.text
        assert "通常の分析CSVには含めません" in download.text
    finally:
        app.dependency_overrides.clear()


def test_export_refuses_to_claim_a_degraded_label_snapshot_is_complete() -> None:
    exports = _Exports()
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_analytics_service] = _DegradedAnalytics
    app.dependency_overrides[get_export_job_repository] = lambda: exports
    client = TestClient(app)
    headers = {"x-monitor-admin-email": "admin@example.com"}
    try:
        for kind, roster_id in (
            ("overview_users", ""),
            ("user_detail", "roster_1"),
        ):
            response = client.post(
                "/api/export/jobs",
                json={
                    "kind": kind,
                    "rosterId": roster_id,
                    "preset": "last_7d",
                    "expectedPublishedRunId": "run-1",
                    "expectedRosterFingerprint": "roster-fingerprint-1",
                    "expectedScopePolicyVersion": "summary_role_v1",
                    **_EXPORT_SNAPSHOT,
                    "idempotencyKey": f"degraded-{kind}",
                },
                headers=headers,
            )
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "content_snapshot_incomplete"
            assert response.json()["detail"]["issues"] == [
                "label_catalog_unavailable"
            ]
        assert exports.jobs == {}
    finally:
        app.dependency_overrides.clear()


def test_export_fails_closed_when_content_diagnostics_are_missing_or_invalid() -> None:
    exports = _Exports()
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_export_job_repository] = lambda: exports
    client = TestClient(app)
    headers = {"x-monitor-admin-email": "admin@example.com"}
    try:
        cases = (
            (_MissingDiagnosticsAnalytics, "overview_users", ""),
            (_InvalidDiagnosticsAnalytics, "user_detail", "roster_1"),
            (_ContradictoryRosterDiagnosticsAnalytics, "overview_users", ""),
        )
        for analytics, kind, roster_id in cases:
            app.dependency_overrides[get_analytics_service] = analytics
            response = client.post(
                "/api/export/jobs",
                json={
                    "kind": kind,
                    "rosterId": roster_id,
                    "preset": "last_7d",
                    "expectedPublishedRunId": "run-1",
                    "expectedRosterFingerprint": "roster-fingerprint-1",
                    "expectedScopePolicyVersion": "summary_role_v1",
                    **_EXPORT_SNAPSHOT,
                    "idempotencyKey": f"invalid-diagnostics-{kind}",
                },
                headers=headers,
            )
            assert response.status_code == 503
            assert response.json()["detail"] == {
                "code": "content_snapshot_incomplete",
                "message": "分析ラベルを確認できないため、完全なCSVは作成できません。復旧後に再実行してください。",
                "issues": ["invalid_content_diagnostics"],
            }
        assert exports.jobs == {}
    finally:
        app.dependency_overrides.clear()
