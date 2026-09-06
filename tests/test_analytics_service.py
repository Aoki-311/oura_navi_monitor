from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.domain.analysis_scopes import AnalysisScope, SCOPE_POLICY_VERSION
from app.jobs.project_firestore import FirestoreProjector
from app.services.analytics_service import (
    AnalyticsService as _ProductionAnalyticsService,
    AnalyticsSnapshotConflictError,
    _activity_level,
    _published_hour_axis,
)
from app.settings import Settings
from app.time_window import MetricsTimeWindow


class _Pipeline:
    @staticmethod
    def data_through():
        return datetime.now(timezone.utc)


class _StalePipeline:
    @staticmethod
    def data_through():
        return datetime.now(timezone.utc) - timedelta(days=2)


class _QualityPipeline:
    @staticmethod
    def publication_snapshot():
        return {
            "data_through": datetime.now(timezone.utc),
            "published_run_id": "run-1",
            "latest_run_id": "run-1",
            "latest_run_status": "succeeded",
            "latest_run_error_code": "",
            "latest_run_finished_at": datetime.now(timezone.utc),
            "quarantined_event_count": 2,
            "deduplicated_delivery_count": 3,
            "repaired_duplicate_fact_count": 1,
            "axis_unmeasured_finding_count": 4,
            "batch_blocking_failure_count": 0,
        }


class _UnavailableDiagnosticsPipeline:
    @staticmethod
    def publication_snapshot():
        return {
            "publication_state_available": True,
            "data_through": datetime.now(timezone.utc),
            "published_run_id": "last-known-good-run",
            "quality_diagnostics_available": False,
            "quality_diagnostics_error_code": "schema_unavailable",
        }


class _UnavailablePublicationStatePipeline:
    @staticmethod
    def publication_snapshot():
        return {
            "publication_state_available": False,
            "publication_state_error_code": "provider_unavailable",
            "quality_diagnostics_available": False,
            "quality_diagnostics_error_code": "publication_state_unavailable",
        }


class _Directory:
    def __init__(self) -> None:
        self.labels = []
        self.users = [
            {
                "roster_id": "field_1",
                "name": "利用者",
                "email": "field@example.com",
                "area": "関西",
                "area_key": "関西",
                "workplace": "大阪",
                "role": "本社MR",
                "department": "DM専任",
                "mr_experience": "10年",
                "label_ids": [],
                "is_active": True,
            },
            {
                "roster_id": "hq_1",
                "name": "本社利用者",
                "email": "hq@example.com",
                "area": "本社",
                "area_key": "本社・虎ノ門",
                "workplace": "虎ノ門",
                "role": "本部メンバー",
                "department": "DM本社",
                "mr_experience": "-",
                "label_ids": [],
                "is_active": True,
            },
        ]

    def list_users(self, *, include_inactive: bool = True):
        return list(self.users)

    def get_user(self, roster_id: str):
        return next((item for item in self.users if item["roster_id"] == roster_id), None)

    def list_labels(self, *, include_inactive: bool = True):
        return list(self.labels)


class _Analytics:
    def __init__(self, rows, metrics=None):
        self.rows = rows
        self.metrics = list(metrics or [])
        self.metric_windows = []
        self.published_roster_rows = []

    def published_roster_snapshot(self, *, published_run_id):
        return [
            dict(item)
            for item in self.published_roster_rows
            if str(item.get("snapshot_run_id") or "") == published_run_id
        ]

    def overview_events(self, **_kwargs):
        return list(self.rows)

    def activity_events(self, **_kwargs):
        return list(self.rows)

    def user_metrics(self, *, window, **_kwargs):
        self.metric_windows.append(window)
        return list(self.metrics)

    def user_detail_events(self, **_kwargs):
        return list(self.rows)


class _PublishedScopePipeline:
    """Test adapter for one atomically published BQ roster projection."""

    def __init__(self, source, *, run_id: str, fingerprints: dict[str, str]):
        self._source = source
        self._run_id = run_id
        self._fingerprints = dict(fingerprints)

    def publication_snapshot(self):
        reader = getattr(self._source, "publication_snapshot", None)
        if callable(reader):
            snapshot = dict(reader() or {})
        else:
            snapshot = {"data_through": self._source.data_through()}
        if snapshot.get("publication_state_available") is False:
            return snapshot
        snapshot.update(
            {
                "publication_state_available": True,
                "published_run_id": str(snapshot.get("published_run_id") or self._run_id),
                "scope_policy_version": SCOPE_POLICY_VERSION,
                **self._fingerprints,
            }
        )
        return snapshot


class _MissingScopeReceiptPipeline:
    @staticmethod
    def publication_snapshot():
        return {
            "publication_state_available": True,
            "data_through": datetime.now(timezone.utc),
            "published_run_id": "legacy-run",
        }


class _ContractRecordingAnalytics(_Analytics):
    def __init__(self):
        super().__init__([])
        self.query_run_ids: list[str | None] = []

    def overview_events(self, **kwargs):
        self.query_run_ids.append(kwargs.get("published_run_id"))
        return []

    def activity_events(self, **kwargs):
        self.query_run_ids.append(kwargs.get("published_run_id"))
        return []


def AnalyticsService(*, analytics, pipeline, directory, settings):
    """Build production service tests around a frozen published projection.

    This deliberately snapshots the directory once. Mutating the Firestore
    double after construction must not alter an analytics response.
    """

    projector = object.__new__(FirestoreProjector)
    projector._directory = directory
    projection = projector.user_scope_rows()
    base_snapshot_reader = getattr(pipeline, "publication_snapshot", None)
    base_snapshot = (
        dict(base_snapshot_reader() or {})
        if callable(base_snapshot_reader)
        else {}
    )
    run_id = str(base_snapshot.get("published_run_id") or "run-1")
    frozen_at = datetime(2026, 8, 30, tzinfo=timezone.utc)
    analytics.published_roster_rows = [
        {
            **dict(row),
            "snapshot_run_id": run_id,
            "snapshot_created_at": frozen_at,
        }
        for row in projection
    ]
    return _ProductionAnalyticsService(
        analytics=analytics,
        pipeline=_PublishedScopePipeline(
            pipeline,
            run_id=run_id,
            fingerprints=projection.fingerprints,
        ),
        directory=directory,
        settings=settings,
    )


def _window(now: datetime) -> MetricsTimeWindow:
    return MetricsTimeWindow(
        start_utc=now - timedelta(days=7),
        end_utc=now,
        timezone="Asia/Tokyo",
        source="days",
        preset="",
        requested_days=7,
        bucket_minutes=1440,
    )


def test_independent_usage_panels_share_overview_formulas_without_activity_query():
    now = datetime.now(timezone.utc)
    rows = [
        {"roster_id": "field_1", "valid_question": True,
         "question_ts": now - timedelta(hours=2), "question_date": now.date().isoformat(),
         "device_class": "desktop", "mode": "internal"},
        {"roster_id": "field_1", "valid_question": True,
         "question_ts": now - timedelta(hours=1), "question_date": now.date().isoformat(),
         "device_class": "mobile", "mode": "websearch"},
    ]
    analytics = _Analytics(rows)
    service = AnalyticsService(analytics=analytics, pipeline=_Pipeline(), directory=_Directory(), settings=Settings())
    expected = service.overview(window=_window(now))

    def unexpected_activity(**kwargs):
        raise AssertionError("independent usage panel must not reload activity")

    analytics.activity_events = unexpected_activity
    environment = service.environment(window=_window(now))
    trend = service.trend(window=_window(now))
    for key in ("hourlyQuestions", "deviceDistribution", "modeDistribution"):
        assert environment[key] == expected[key]
    assert "kpis" not in environment and "activityDistribution" not in environment
    assert trend["usageTrend"] == expected["usageTrend"]
    assert "deviceDistribution" not in trend


def test_user_period_counts_come_from_selected_events_not_seven_day_metrics():
    now = datetime.now(timezone.utc)
    rows = [{"roster_id": "field_1", "valid_question": True,
             "question_ts": now - timedelta(hours=1), "question_date": now.date().isoformat()} for _ in range(2)]
    analytics = _Analytics(rows, metrics=[{"roster_id": "field_1", "active_days_7": 6, "user_message_count_7": 99}])
    service = AnalyticsService(analytics=analytics, pipeline=_Pipeline(), directory=_Directory(), settings=Settings())
    user = service.overview_users(window=_window(now))["users"][0]
    assert user["activeDaysInPeriod"] == 1
    assert user["userMessageCountInPeriod"] == 2
    assert user["userMessageCount7"] == 99


def test_missing_scope_receipt_never_selects_a_second_roster_or_reader() -> None:
    now = datetime.now(timezone.utc)
    directory = _Directory()
    for user in directory.users:
        user["updated_at"] = datetime(2026, 8, 30, tzinfo=timezone.utc)
    analytics = _ContractRecordingAnalytics()
    service = _ProductionAnalyticsService(
        analytics=analytics,
        pipeline=_MissingScopeReceiptPipeline(),
        directory=directory,
        settings=Settings(),
    )

    with pytest.raises(
        AnalyticsSnapshotConflictError,
        match="scope receipt is unavailable",
    ):
        service.overview(window=_window(now))

    assert analytics.query_run_ids == []


def test_partial_run_versioned_receipt_never_mixes_contracts() -> None:
    with pytest.raises(
        AnalyticsSnapshotConflictError,
        match="scope receipt is incomplete",
    ):
        _ProductionAnalyticsService._run_versioned_snapshot_id(
            {
                "published_run_id": "run-partial",
                "scope_policy_version": SCOPE_POLICY_VERSION,
            }
        )


def _single_day_window() -> MetricsTimeWindow:
    return MetricsTimeWindow(
        start_utc=datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc),
        timezone="Asia/Tokyo",
        source="preset",
        preset="today",
        requested_days=1,
        bucket_minutes=1440,
    )


def test_hour_axis_never_fills_hours_beyond_the_published_watermark() -> None:
    window = MetricsTimeWindow(
        start_utc=datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 8, 25, 15, 0, tzinfo=timezone.utc),
        timezone="Asia/Tokyo",
        source="custom",
        preset="",
        requested_days=2,
        bucket_minutes=30,
    )

    assert _published_hour_axis(
        window,
        data_through=datetime(2026, 8, 23, 18, 0, tzinfo=timezone.utc),
    ) == ["00:00", "01:00", "02:00"]


def test_overview_publishes_representative_partial_measurement_with_coverage() -> None:
    now = datetime.now(timezone.utc)
    rows = [
        {
            "question_ts": now - timedelta(hours=2),
            "question_date": (now - timedelta(hours=2)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "measurement_available": True,
            "complete_delivery": True,
            "answer_measurement_profile": "runtime_truth_full",
            "total_latency_ms": 1000,
            "mode": "internal",
            "device_class": "desktop",
            "primary_question_category": "product_information",
        },
        {
            "question_ts": now - timedelta(hours=1),
            "question_date": (now - timedelta(hours=1)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "measurement_available": False,
            "complete_delivery": False,
            "total_latency_ms": 2000,
            "mode": "websearch",
            "device_class": "mobile",
            "primary_question_category": "institution_gpo_market",
        },
    ]
    service = AnalyticsService(
        analytics=_Analytics(rows),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.overview(window=_window(now))

    assert payload["kpis"]["completeDelivery"] == {
        "value": 1.0,
        "measuredCount": 1,
        "totalCount": 2,
        "measurementState": "partial",
        "measurementReason": "current_data_gap",
    }
    assert payload["kpis"]["p95Latency"] == {
        "valueMs": 2000,
        "measuredCount": 2,
        "totalCount": 2,
        "measurementState": "measured",
        "measurementReason": "complete",
    }


def test_failure_only_historical_outcomes_do_not_publish_a_biased_success_rate() -> None:
    now = datetime.now(timezone.utc)
    service = AnalyticsService(
        analytics=_Analytics([{
            "question_ts": now - timedelta(hours=1),
            "question_date": (now - timedelta(hours=1)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "measurement_available": True,
            "complete_delivery": False,
            "answer_measurement_profile": "terminal_outcome",
            "total_latency_ms": 1000,
            "mode": "internal",
            "device_class": "desktop",
            "classification_status": "not_measured",
            "record_origin": "legacy_audit_history",
        }]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    measurement = service.overview(window=_window(now))["kpis"]["completeDelivery"]
    assert measurement == {
        "value": None,
        "measuredCount": 0,
        "totalCount": 1,
        "measurementState": "not_measured",
        "measurementReason": "historical_unavailable",
    }


def test_current_terminal_failure_remains_in_the_answer_success_denominator() -> None:
    now = datetime.now(timezone.utc)
    service = AnalyticsService(
        analytics=_Analytics([{
            "question_ts": now - timedelta(hours=1),
            "question_date": (now - timedelta(hours=1)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "measurement_available": True,
            "complete_delivery": False,
            "answer_measurement_profile": "complete_delivery_full",
            "primary_failure_reason": "stream_failed",
            "total_latency_ms": 900,
            "mode": "internal",
            "device_class": "desktop",
            "primary_question_category": "product_information",
        }]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    assert service.overview(window=_window(now))["kpis"]["completeDelivery"] == {
        "value": 0.0,
        "measuredCount": 1,
        "totalCount": 1,
        "measurementState": "measured",
        "measurementReason": "complete",
    }


def test_measurement_state_distinguishes_no_usage_from_unmeasured_history() -> None:
    now = datetime.now(timezone.utc)
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    no_usage = service.overview(window=_window(now))
    assert no_usage["kpis"]["completeDelivery"]["measurementState"] == "no_usage"

    service = AnalyticsService(
        analytics=_Analytics([{
            "question_ts": now - timedelta(hours=1),
            "question_date": (now - timedelta(hours=1)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "measurement_available": False,
            "complete_delivery": None,
            "total_latency_ms": None,
            "mode": "internal",
            "device_class": "desktop",
            "primary_question_category": "unclassified",
            "classification_status": "not_measured",
            "record_origin": "firestore_history",
        }]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )
    historical = service.overview(window=_window(now))
    assert historical["kpis"]["completeDelivery"]["measurementState"] == "not_measured"
    assert historical["kpis"]["p95Latency"]["measurementState"] == "not_measured"
    assert historical["kpis"]["completeDelivery"]["measurementReason"] == "historical_unavailable"


def test_stale_pipeline_is_metadata_and_does_not_disable_available_analytics() -> None:
    now = datetime.now(timezone.utc)
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_StalePipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.overview(window=_window(now))

    assert "status" not in payload
    assert payload["freshness"]["state"] == "stale"
    assert payload["kpis"]["activeUsers"] == 0


def test_published_run_quality_is_exposed_separately_from_visible_axis_coverage() -> None:
    now = datetime.now(timezone.utc)
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_QualityPipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    quality = service.overview(window=_window(now))["analyticsQuality"]

    assert quality["totalEventCount"] == 0
    assert quality["isolatedEventCount"] == 0
    assert quality["sourcePipeline"] == {
        "publishedRunId": "run-1",
        "latestRunId": "run-1",
        "latestRunStatus": "succeeded",
        "latestRunErrorCode": "",
        "latestRunFinishedAt": quality["sourcePipeline"]["latestRunFinishedAt"],
        "diagnosticsStatus": "available",
        "diagnosticsErrorCode": "",
        "state": "degraded",
        "quarantinedEventCount": 2,
        "deduplicatedDeliveryCount": 3,
        "repairedDuplicateFactCount": 1,
        "axisUnmeasuredFindingCount": 4,
        "batchBlockingFailureCount": 0,
    }


def test_latest_failed_run_is_visible_without_replacing_last_published_data() -> None:
    quality = _ProductionAnalyticsService._source_pipeline_quality(
        {
            "published_run_id": "run-success",
            "latest_run_id": "run-failed",
            "latest_run_status": "failed",
            "latest_run_error_code": "DataQualityGateError",
            "latest_run_finished_at": datetime(2026, 8, 29, 4, tzinfo=timezone.utc),
            "batch_blocking_failure_count": 2,
        }
    )

    assert quality["publishedRunId"] == "run-success"
    assert quality["latestRunId"] == "run-failed"
    assert quality["state"] == "blocked"
    assert quality["batchBlockingFailureCount"] == 2


def test_missing_quality_diagnostics_never_erases_available_overview_facts() -> None:
    now = datetime.now(timezone.utc)
    question_at = now - timedelta(hours=1)
    service = AnalyticsService(
        analytics=_Analytics(
            [
                {
                    "question_ts": question_at,
                    "question_date": question_at.astimezone(
                        timezone.utc
                    ).date().isoformat(),
                    "roster_id": "field_1",
                    "area_key": "関西",
                    "valid_question": True,
                    "measurement_available": False,
                    "complete_delivery": None,
                    "total_latency_ms": None,
                    "classification_measurement_state": "not_measured",
                    "task_measurement_state": "not_measured",
                    "product_measurement_state": "not_measured",
                }
            ]
        ),
        pipeline=_UnavailableDiagnosticsPipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.overview(window=_window(now))

    assert payload["kpis"]["activeUsers"] == 1
    assert sum(row["questions"] for row in payload["usageTrend"]) == 1
    assert sum(row["count"] for row in payload["hourlyQuestions"]) == 1
    assert payload["analyticsQuality"]["sourcePipeline"]["state"] == "unavailable"
    assert payload["analyticsQuality"]["sourcePipeline"]["diagnosticsStatus"] == "unavailable"
    assert payload["analyticsQuality"]["sourcePipeline"]["diagnosticsErrorCode"] == "schema_unavailable"


def test_missing_published_scope_receipt_fails_closed_instead_of_mixing_live_roster() -> None:
    now = datetime.now(timezone.utc)
    question_at = now - timedelta(hours=1)
    question_day = question_at.astimezone(ZoneInfo("Asia/Tokyo")).date().isoformat()
    service = AnalyticsService(
        analytics=_Analytics(
            [
                {
                    "question_ts": question_at,
                    "question_date": question_day,
                    "roster_id": "field_1",
                    "area_key": "関西",
                    "valid_question": True,
                    "measurement_available": False,
                    "complete_delivery": None,
                    "total_latency_ms": None,
                    "classification_measurement_state": "not_measured",
                    "task_measurement_state": "not_measured",
                    "product_measurement_state": "not_measured",
                }
            ]
        ),
        pipeline=_UnavailablePublicationStatePipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    with pytest.raises(AnalyticsSnapshotConflictError):
        service.overview(window=_window(now))
    with pytest.raises(AnalyticsSnapshotConflictError):
        service.user_detail("field_1", window=_window(now))


def test_missing_request_tasks_are_explicitly_unmeasured_without_hiding_other_metrics() -> None:
    now = datetime.now(timezone.utc)
    rows = [
        {
            "question_ts": now - timedelta(hours=1),
            "question_date": (now - timedelta(hours=1)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "measurement_available": False,
            "complete_delivery": None,
            "total_latency_ms": None,
            "mode": "internal",
            "device_class": "desktop",
            "primary_question_category": "legacy_unknown",
            "classification_status": "unclassified",
        }
    ]
    service = AnalyticsService(
        analytics=_Analytics(rows),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.overview(window=_window(now))

    assert payload["kpis"]["activeUsers"] == 1
    assert payload["requestTasks"] == []
    assert payload["taskMeasurement"] == {
        "measuredCount": 0,
        "totalCount": 1,
        "measurementState": "not_measured",
        "measurementReason": "current_data_gap",
    }


def test_historical_question_category_never_claims_request_task_measurement() -> None:
    now = datetime.now(timezone.utc)
    service = AnalyticsService(
        analytics=_Analytics([{
            "question_ts": now - timedelta(hours=1),
            "question_date": (now - timedelta(hours=1)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "primary_question_category": "product_information",
            "classification_status": "classified",
            "analytics_tasks": ["unclassified"],
            "record_origin": "legacy_audit_history",
        }]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.overview(window=_window(now))

    assert payload["requestTasks"] == []
    assert payload["taskMeasurement"] == {
        "measuredCount": 0,
        "totalCount": 1,
        "measurementState": "not_measured",
        "measurementReason": "historical_unavailable",
    }


def test_current_multi_task_event_drives_distribution_and_product_matrix() -> None:
    now = datetime.now(timezone.utc)
    service = AnalyticsService(
        analytics=_Analytics([{
            "question_ts": now - timedelta(hours=1),
            "question_date": (now - timedelta(hours=1)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "analytics_tasks": ["fact_lookup", "comparison_selection"],
            "task_measurement_state": "measured",
            "record_origin": "canonical_event",
            "primary_product_name": "テルフュージョン",
            "product_candidate_count": 1,
            "product_resolved_count": 1,
            "product_measurement_state": "measured",
        }]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.overview(window=_window(now))

    assert payload["taskMeasurement"] == {
        "measuredCount": 1,
        "totalCount": 1,
        "measurementState": "measured",
        "measurementReason": "complete",
    }
    assert {row["key"] for row in payload["requestTasks"]} == {
        "fact_lookup",
        "comparison_selection",
    }
    assert {
        (row["product"], row["task"], row["count"])
        for row in payload["productTaskMatrix"]
    } == {
        ("テルフュージョン", "fact_lookup", 1),
        ("テルフュージョン", "comparison_selection", 1),
    }


def test_environment_dimensions_distinguish_partial_measurement_from_unknown_history() -> None:
    now = datetime.now(timezone.utc)
    rows = [
        {
            "question_ts": now - timedelta(hours=2),
            "question_date": (now - timedelta(hours=2)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "mode": "internal",
            "device_class": "desktop",
        },
        {
            "question_ts": now - timedelta(hours=1),
            "question_date": (now - timedelta(hours=1)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "mode": "unknown",
            "device_class": "unknown",
            "record_origin": "firestore_history",
        },
    ]
    service = AnalyticsService(
        analytics=_Analytics(rows),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.overview(window=_window(now))

    assert payload["modeDistribution"] == [
        {"key": "internal", "label": "社内モード", "count": 1, "rate": 1.0}
    ]
    assert payload["deviceDistribution"] == [
        {"key": "desktop", "label": "PC", "count": 1, "rate": 1.0}
    ]
    assert payload["modeMeasurement"] == {
        "measuredCount": 1,
        "totalCount": 2,
        "measurementState": "partial",
        "measurementReason": "historical_unavailable",
    }
    assert payload["deviceMeasurement"] == {
        "measuredCount": 1,
        "totalCount": 2,
        "measurementState": "partial",
        "measurementReason": "historical_unavailable",
    }


def test_single_day_overview_does_not_define_return_rate() -> None:
    window = _single_day_window()
    question_ts = datetime(2026, 8, 24, 1, 0, tzinfo=timezone.utc)
    service = AnalyticsService(
        analytics=_Analytics([{
            "question_ts": question_ts,
            "question_date": "2026-08-24",
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
        }]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    assert service.overview(window=window)["kpis"]["returnRate"] is None


def test_regions_exclude_non_summary_roles_even_when_their_location_is_valid() -> None:
    now = datetime.now(timezone.utc)
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.regions(window=_window(now))

    assert {row["areaKey"] for row in payload["regions"]} == {"関西"}


def test_summary_snapshot_identity_is_stable_across_area_filters() -> None:
    now = datetime.now(timezone.utc)
    directory = _Directory()
    directory.users.append(
        {
            "roster_id": "field_2",
            "name": "別地域利用者",
            "email": "field2@example.com",
            "area": "九州",
            "area_key": "九州",
            "workplace": "福岡",
            "role": "コントラクトMR",
            "department": "DM専任",
            "mr_experience": "5年",
            "label_ids": [],
            "is_active": True,
        }
    )
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_QualityPipeline(),
        directory=directory,
        settings=Settings(),
    )

    unfiltered = service.overview(window=_window(now))
    filtered = service.overview(window=_window(now), area_key="関西")
    filtered_users = service.overview_users(
        window=_window(now),
        area_key="関西",
    )
    regions = service.regions(window=_window(now))

    assert filtered["scopeUserCount"] == 1
    assert filtered_users["scopeUserCount"] == 1
    assert regions["scopeUserCount"] == 2
    assert {
        unfiltered["rosterFingerprint"],
        filtered["rosterFingerprint"],
        filtered_users["rosterFingerprint"],
        regions["rosterFingerprint"],
    } == {unfiltered["rosterFingerprint"]}


def test_user_metrics_reuses_the_exact_page_window_for_list_and_detail() -> None:
    window = _single_day_window()
    analytics = _Analytics([])
    service = AnalyticsService(
        analytics=analytics,
        pipeline=_QualityPipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    service.overview_users(window=window)
    service.user_detail("field_1", window=window)

    assert analytics.metric_windows == [window, window]


def test_label_catalog_failure_cannot_erase_summary_or_region_bodies() -> None:
    now = datetime.now(timezone.utc)
    directory = _Directory()

    def fail_labels(*, include_inactive: bool = True):
        raise RuntimeError("label catalog unavailable")

    directory.list_labels = fail_labels
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_QualityPipeline(),
        directory=directory,
        settings=Settings(),
    )

    assert service.overview(window=_window(now))["scopeUserCount"] == 1
    assert service.regions(window=_window(now))["scopeUserCount"] == 1


def test_label_catalog_failure_preserves_user_bodies_with_explicit_diagnostics() -> None:
    now = datetime.now(timezone.utc)
    directory = _Directory()
    directory.users[0]["label_ids"] = ["label_unavailable"]

    def fail_labels(*, include_inactive: bool = True):
        raise RuntimeError("label catalog unavailable")

    directory.list_labels = fail_labels
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_QualityPipeline(),
        directory=directory,
        settings=Settings(),
    )

    users = service.overview_users(window=_window(now))
    detail = service.user_detail("field_1", window=_window(now))

    assert users["users"][0]["name"] == "利用者"
    assert users["users"][0]["labels"] == []
    assert users["contentDiagnostics"] == {
        "state": "degraded",
        "labelCatalogStatus": "unavailable",
        "rosterStatus": "available",
        "rosterIsolatedCount": 0,
        "rosterIssueCounts": {},
        "issues": ["label_catalog_unavailable"],
    }
    assert detail["profile"]["name"] == "利用者"
    assert detail["profile"]["labels"] == []
    assert detail["summary"]["questions"] == 0
    assert detail["contentDiagnostics"] == users["contentDiagnostics"]


def test_unknown_and_duplicate_label_references_degrade_without_hiding_inactive_labels() -> None:
    now = datetime.now(timezone.utc)
    directory = _Directory()
    directory.users[0]["label_ids"] = [
        "label_inactive",
        "label_unknown",
        "label_inactive",
    ]
    directory.labels = [
        {
            "label_id": "label_inactive",
            "name": "過去ラベル",
            "color": "#23d28f",
            "is_active": False,
        }
    ]
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_QualityPipeline(),
        directory=directory,
        settings=Settings(),
    )

    payload = service.overview_users(window=_window(now))

    assert payload["users"][0]["labels"] == [
        {"labelId": "label_inactive", "name": "過去ラベル", "color": "#23d28f"}
    ]
    assert payload["contentDiagnostics"] == {
        "state": "degraded",
        "labelCatalogStatus": "partial",
        "rosterStatus": "available",
        "rosterIsolatedCount": 0,
        "rosterIssueCounts": {},
        "issues": ["unknown_label_reference", "duplicate_label_reference"],
    }
    directory.users[0]["label_ids"] = ["label_inactive", "label_unknown"]
    repaired_duplicate = service.overview_users(window=_window(now))
    assert repaired_duplicate["users"] == payload["users"]
    assert repaired_duplicate["contentDiagnostics"] == payload["contentDiagnostics"]
    assert repaired_duplicate["contentFingerprint"] == payload["contentFingerprint"]

    next_service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_QualityPipeline(),
        directory=directory,
        settings=Settings(),
    )
    next_published = next_service.overview_users(window=_window(now))
    assert next_published["contentDiagnostics"]["issues"] == [
        "unknown_label_reference"
    ]
    assert next_published["contentFingerprint"] != payload["contentFingerprint"]


def test_invalid_label_row_is_isolated_without_coercing_string_false_to_active() -> None:
    now = datetime.now(timezone.utc)
    directory = _Directory()
    directory.users[0]["label_ids"] = ["label_bad"]
    directory.labels = [
        {
            "label_id": "label_bad",
            "name": "不正ラベル",
            "color": "#23d28f",
            "is_active": "false",
        }
    ]
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_QualityPipeline(),
        directory=directory,
        settings=Settings(),
    )

    payload = service.overview_users(window=_window(now))

    assert payload["users"][0]["name"] == "利用者"
    assert payload["users"][0]["labels"] == []
    assert payload["contentDiagnostics"] == {
        "state": "degraded",
        "labelCatalogStatus": "partial",
        "rosterStatus": "available",
        "rosterIsolatedCount": 0,
        "rosterIssueCounts": {},
        "issues": ["invalid_label_is_active", "unknown_label_reference"],
    }


def test_one_structurally_invalid_roster_row_cannot_erase_valid_analytics() -> None:
    now = datetime.now(timezone.utc)
    directory = _Directory()
    directory.users.insert(
        0,
        {
            **directory.users[0],
            "roster_id": "broken_1",
            "name": "壊れた行",
            "email": "broken@example.com",
            "area_key": "",
        },
    )
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_QualityPipeline(),
        directory=directory,
        settings=Settings(),
    )

    overview = service.overview(window=_window(now))
    users = service.overview_users(window=_window(now))
    regions = service.regions(window=_window(now))
    detail = service.user_detail("field_1", window=_window(now))

    assert overview["scopeUserCount"] == 1
    assert [row["rosterId"] for row in users["users"]] == ["field_1"]
    assert [row["areaKey"] for row in regions["regions"]] == ["関西"]
    assert detail["profile"]["name"] == "利用者"
    for payload in (overview, users, regions, detail):
        diagnostics = payload["contentDiagnostics"]
        assert diagnostics["state"] == "degraded"
        assert diagnostics["rosterStatus"] == "partial"
        assert diagnostics["rosterIsolatedCount"] == 1
        assert diagnostics["rosterIssueCounts"] == {"missing_area_key": 1}
        assert diagnostics["issues"] == ["roster_missing_area_key"]


def test_normalized_duplicate_label_names_isolate_only_the_conflicting_labels() -> None:
    now = datetime.now(timezone.utc)
    directory = _Directory()
    directory.users[0]["label_ids"] = ["label_safe"]
    directory.labels = [
        {
            "label_id": "label_a",
            "name": " ＴＥＳＴ ",
            "color": "#23d28f",
            "is_active": True,
        },
        {
            "label_id": "label_b",
            "name": "test",
            "color": "#386dff",
            "is_active": True,
        },
        {
            "label_id": "label_safe",
            "name": "安全",
            "color": "#ffb340",
            "is_active": True,
        },
    ]
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_QualityPipeline(),
        directory=directory,
        settings=Settings(),
    )

    users = service.overview_users(window=_window(now))
    detail = service.user_detail("field_1", window=_window(now))

    assert users["users"][0]["labels"] == [
        {"labelId": "label_safe", "name": "安全", "color": "#ffb340"}
    ]
    assert detail["profile"]["labels"] == users["users"][0]["labels"]
    assert users["contentDiagnostics"]["state"] == "degraded"
    assert users["contentDiagnostics"]["labelCatalogStatus"] == "partial"
    assert users["contentDiagnostics"]["issues"] == [
        "duplicate_label_name"
    ]
    assert detail["contentDiagnostics"] == users["contentDiagnostics"]


def test_duplicate_roster_identities_are_excluded_from_global_and_user_map_scopes() -> None:
    directory = _Directory()
    directory.users.extend(
        [
            {
                **directory.users[0],
                "roster_id": "field_duplicate",
                "name": "重複利用者",
                "email": " FIELD@EXAMPLE.COM ",
            },
            {
                **directory.users[0],
                "roster_id": "safe_summary",
                "name": "安全な利用者",
                "email": "safe-summary@example.com",
            },
        ]
    )
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_QualityPipeline(),
        directory=directory,
        settings=Settings(),
    )

    publication = service._publication_snapshot()
    global_roster = service._roster_snapshot(
        AnalysisScope.GLOBAL,
        publication=publication,
    ).rows
    user_map_roster = service._roster_snapshot(
        AnalysisScope.USER_MAP,
        publication=publication,
    ).rows

    assert [row["roster_id"] for row in global_roster] == ["safe_summary"]
    assert [row["roster_id"] for row in user_map_roster] == ["hq_1", "safe_summary"]


def test_visible_label_definition_is_part_of_analytics_snapshot_identity() -> None:
    now = datetime.now(timezone.utc)
    directory = _Directory()
    directory.users[0]["updated_at"] = "2026-08-29T00:00:00Z"
    directory.users[0]["label_ids"] = ["label_1"]
    directory.labels = [
        {
            "label_id": "label_1",
            "name": "重点",
            "color": "#23d28f",
            "is_active": True,
            "updated_at": "2026-08-29T00:00:00Z",
        }
    ]
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_QualityPipeline(),
        directory=directory,
        settings=Settings(),
    )

    before = service.overview_users(window=_window(now))
    directory.labels[0] = {
        **directory.labels[0],
        "name": "最重点",
        "updated_at": "2026-08-29T01:00:00Z",
    }
    after = service.overview_users(window=_window(now))

    assert before["users"][0]["labels"][0]["name"] == "重点"
    assert after["users"] == before["users"]
    assert after["contentFingerprint"] == before["contentFingerprint"]

    next_service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_QualityPipeline(),
        directory=directory,
        settings=Settings(),
    )
    next_published = next_service.overview_users(window=_window(now))
    assert next_published["users"][0]["labels"][0]["name"] == "最重点"
    assert before["rosterFingerprint"] == next_published["rosterFingerprint"]
    assert before["contentFingerprint"] != next_published["contentFingerprint"]


def test_product_ranking_discloses_unresolved_governed_product_candidates() -> None:
    now = datetime.now(timezone.utc)
    rows = [
        {
            "question_ts": now - timedelta(hours=1),
            "question_date": (now - timedelta(hours=1)).date().isoformat(),
            "roster_id": "field_1",
            "area_key": "関西",
            "valid_question": True,
            "measurement_available": True,
            "complete_delivery": True,
            "total_latency_ms": 1000,
            "mode": "internal",
            "device_class": "desktop",
            "primary_question_category": "product_information",
            "primary_product_name": "テルフュージョン",
            "product_candidate_count": 2,
            "product_resolved_count": 1,
            "product_measurement_state": "measured",
        }
    ]
    service = AnalyticsService(
        analytics=_Analytics(rows),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.overview(window=_window(now))

    assert payload["topProducts"] == [
        {"label": "テルフュージョン", "count": 1}
    ]
    assert payload["productResolution"] == {
        "candidateCount": 2,
        "resolvedCount": 1,
        "unresolvedQuestions": 1,
        "resolutionRate": 0.5,
        "measuredCount": 1,
        "totalCount": 1,
        "measurementState": "measured",
        "measurementReason": "complete",
    }


def test_user_detail_uses_actual_last_seen_even_when_outside_selected_window() -> None:
    now = datetime.now(timezone.utc)
    last_seen = now - timedelta(days=30, hours=3)
    service = AnalyticsService(
        analytics=_Analytics([], metrics=[{"roster_id": "field_1", "last_active_at": last_seen}]),
        pipeline=_Pipeline(),
        directory=_Directory(),
        settings=Settings(),
    )

    payload = service.user_detail("field_1", window=_window(now))

    assert payload["summary"]["lastActiveAt"] == last_seen.isoformat()
    assert payload["summary"]["questions"] == 0


def test_inactive_user_cannot_be_opened_through_direct_detail_route() -> None:
    now = datetime.now(timezone.utc)
    directory = _Directory()
    directory.users[0]["is_active"] = False
    service = AnalyticsService(
        analytics=_Analytics([]),
        pipeline=_Pipeline(),
        directory=directory,
        settings=Settings(),
    )

    with pytest.raises(KeyError, match="user not found"):
        service.user_detail("field_1", window=_window(now))


def test_peer_comparison_excludes_the_selected_user_and_discloses_coverage() -> None:
    roster = [
        {"roster_id": "one", "area": "関西"},
        {"roster_id": "two", "area": "関西"},
    ]
    comparison = _ProductionAnalyticsService._peer_comparison(
        user=roster[0],
        roster=roster,
        field="area",
        events={
            "one": [{"question_date": "2026-08-23", "measurement_available": True, "complete_delivery": True}],
            "two": [{"question_date": "2026-08-23", "measurement_available": False, "complete_delivery": False}],
        },
    )

    assert comparison["peerCount"] == 1
    assert comparison["averageQuestions"] == 1.0
    assert comparison["averageCompleteDelivery"] == {
        "value": None,
        "measuredCount": 0,
        "totalCount": 1,
        "measurementState": "not_measured",
        "measurementReason": "current_data_gap",
    }


def test_activity_level_uses_distinct_japan_active_days_not_question_volume() -> None:
    period_end = datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc)
    same_day_questions = [
        datetime(2026, 8, 24, 1, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 24, 2, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 24, 3, 0, tzinfo=timezone.utc),
    ]

    assert _activity_level(
        same_day_questions,
        end=period_end,
        timezone_name="Asia/Tokyo",
    ) == "low"

    six_active_days = [
        period_end - timedelta(days=offset, hours=1)
        for offset in range(6)
    ]
    assert _activity_level(
        six_active_days,
        end=period_end,
        timezone_name="Asia/Tokyo",
    ) == "high"


@pytest.mark.parametrize(
    ("active_day_offsets", "expected"),
    [
        ([], "dormant"),
        ([0], "low"),
        ([0, 1], "low"),
        ([0, 1, 2], "middle"),
        ([0, 1, 2, 3, 4], "middle"),
        ([0, 1, 2, 3, 4, 5], "high"),
        ([13], "low"),
        ([14], "dormant"),
    ],
)
def test_activity_level_covers_all_four_segments_and_the_fourteen_day_boundary(
    active_day_offsets: list[int],
    expected: str,
) -> None:
    period_end = datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc)
    questions = [
        period_end - timedelta(days=offset, hours=1)
        for offset in active_day_offsets
    ]

    assert _activity_level(
        questions,
        end=period_end,
        timezone_name="Asia/Tokyo",
    ) == expected
