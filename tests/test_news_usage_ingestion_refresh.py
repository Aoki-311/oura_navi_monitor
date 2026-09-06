from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.jobs import refresh_analytics
from app.settings import Settings


class _ChatJob:
    def __init__(self, calls: list[str]):
        self.calls = calls

    def run(self, *, now):
        self.calls.append("chat_committed")
        return {"status": "up_to_date", "dataThrough": "2026-09-06T00:00:00Z"}

    def run_until_current(self, *, now):
        self.calls.append("chat_committed")
        return {"status": "succeeded", "lastRun": {"status": "succeeded"}}


def test_disabled_configuration_preserves_the_exact_chat_result(monkeypatch) -> None:
    settings = Settings(monitor_analytics_start_at="2026-08-01T00:00:00Z")
    calls: list[str] = []

    def must_not_run(**_):
        raise AssertionError("disabled news ingestion must not run")

    monkeypatch.setattr(refresh_analytics, "run_configured_news_usage", must_not_run)
    result = refresh_analytics._run_refresh_jobs(
        settings,
        chat_job=_ChatJob(calls),
        target_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
        until_current=False,
        trigger_source="manual",
    )

    assert calls == ["chat_committed"]
    assert result == {
        "status": "up_to_date",
        "dataThrough": "2026-09-06T00:00:00Z",
    }


def test_news_failure_happens_after_chat_commit_and_cannot_rollback_it(monkeypatch) -> None:
    settings = Settings(
        monitor_analytics_start_at="2026-08-01T00:00:00Z",
        monitor_news_usage_source_service="oura-navi-test",
        monitor_news_usage_start_at="2026-09-06T00:00:00Z",
    )
    calls: list[str] = []

    def fail_news(*_, **__):
        calls.append("news_failed")
        raise RuntimeError("news transport failed")

    monkeypatch.setattr(refresh_analytics, "run_configured_news_usage", fail_news)

    with pytest.raises(RuntimeError, match="news transport failed"):
        refresh_analytics._run_refresh_jobs(
            settings,
            chat_job=_ChatJob(calls),
            target_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
            until_current=True,
            trigger_source="scheduler_hourly",
        )

    assert calls == ["chat_committed", "news_failed"]


def test_enabled_news_result_is_additive_to_the_legacy_chat_envelope(monkeypatch) -> None:
    settings = Settings(
        monitor_analytics_start_at="2026-08-01T00:00:00Z",
        monitor_news_usage_source_service="oura-navi-test",
        monitor_news_usage_start_at="2026-09-06T00:00:00Z",
    )
    calls: list[str] = []

    def run_news(*_, **kwargs):
        calls.append("news_committed")
        assert kwargs["until_current"] is False
        return {"status": "succeeded", "publishedRunId": "news-run-1"}

    monkeypatch.setattr(refresh_analytics, "run_configured_news_usage", run_news)
    result = refresh_analytics._run_refresh_jobs(
        settings,
        chat_job=_ChatJob(calls),
        target_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
        until_current=False,
        trigger_source="manual",
    )

    assert calls == ["chat_committed", "news_committed"]
    assert result["status"] == "up_to_date"
    assert result["newsUsage"] == {
        "status": "succeeded",
        "publishedRunId": "news-run-1",
    }
