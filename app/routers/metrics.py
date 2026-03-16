from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_bigquery_metrics_service, get_firestore_history_service
from app.security.auth import AdminIdentity, require_admin
from app.services.bigquery_metrics import BigQueryMetricsService
from app.services.firestore_history import FirestoreHistoryService
from app.settings import Settings, get_settings
from app.time_window import MetricsTimeWindow, resolve_time_window

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


def _build_window(
    *,
    settings: Settings,
    days: int,
    preset: str,
    start: str,
    end: str,
) -> MetricsTimeWindow:
    return resolve_time_window(
        settings=settings,
        days=days,
        preset=preset,
        start=start,
        end=end,
    )


@router.get("/overview")
def metrics_overview(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        bq_overview = bq.get_overview(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"bigquery overview failed: {exc}") from exc

    try:
        fs_usage = fs.aggregate_usage(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"firestore usage failed: {exc}") from exc

    return {
        "days": window.requested_days,
        "window": {
            "source": window.source,
            "preset": window.preset,
            "start": window.start_utc.isoformat(),
            "end": window.end_utc.isoformat(),
            "timezone": window.timezone,
            "bucketMinutes": window.bucket_minutes,
        },
        "overview": bq_overview,
        "usage": fs_usage,
    }


@router.get("/usage")
def metrics_usage(
    days: int = Query(default=30, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        timeseries = bq.get_usage_timeseries(window=window)
        firestore_usage = fs.aggregate_usage(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"usage query failed: {exc}") from exc

    return {
        "days": window.requested_days,
        "window": {
            "source": window.source,
            "preset": window.preset,
            "start": window.start_utc.isoformat(),
            "end": window.end_utc.isoformat(),
            "timezone": window.timezone,
            "bucketMinutes": window.bucket_minutes,
        },
        "timeseries": timeseries,
        "usage": firestore_usage,
    }


@router.get("/errors")
def metrics_errors(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        report = bq.get_error_report(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"error report query failed: {exc}") from exc

    return {
        "days": window.requested_days,
        "window": {
            "source": window.source,
            "preset": window.preset,
            "start": window.start_utc.isoformat(),
            "end": window.end_utc.isoformat(),
            "timezone": window.timezone,
            "bucketMinutes": window.bucket_minutes,
        },
        **report,
    }


@router.get("/devices")
def metrics_devices(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        rows = bq.get_device_report(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"device report query failed: {exc}") from exc

    return {
        "days": window.requested_days,
        "window": {
            "source": window.source,
            "preset": window.preset,
            "start": window.start_utc.isoformat(),
            "end": window.end_utc.isoformat(),
            "timezone": window.timezone,
            "bucketMinutes": window.bucket_minutes,
        },
        "devices": rows,
    }


@router.get("/query-suggest")
def metrics_query_suggest(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        log_report = bq.get_query_suggest_report(window=window)
        fact_report = fs.aggregate_query_suggest_facts(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"query-suggest report failed: {exc}") from exc

    return {
        "days": window.requested_days,
        "window": {
            "source": window.source,
            "preset": window.preset,
            "start": window.start_utc.isoformat(),
            "end": window.end_utc.isoformat(),
            "timezone": window.timezone,
            "bucketMinutes": window.bucket_minutes,
        },
        "logs": log_report,
        "facts": fact_report,
    }
