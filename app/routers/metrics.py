from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_bigquery_metrics_service, get_firestore_history_service
from app.security.auth import AdminIdentity, require_admin
from app.services.bigquery_metrics import BigQueryMetricsService
from app.services.firestore_history import FirestoreHistoryService
from app.settings import Settings, get_settings

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


def _sanitize_days(days: int, settings: Settings) -> int:
    if days <= 0:
        return settings.monitor_default_days
    return min(days, settings.monitor_retention_days)


@router.get("/overview")
def metrics_overview(
    days: int = Query(default=7, ge=1, le=365),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> dict:
    window = _sanitize_days(days, settings)
    try:
        bq_overview = bq.get_overview(days=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"bigquery overview failed: {exc}") from exc

    try:
        fs_usage = fs.aggregate_usage(days=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"firestore usage failed: {exc}") from exc

    return {
        "days": window,
        "overview": bq_overview,
        "usage": fs_usage,
    }


@router.get("/usage")
def metrics_usage(
    days: int = Query(default=30, ge=1, le=365),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> dict:
    window = _sanitize_days(days, settings)
    try:
        timeseries = bq.get_usage_timeseries(days=window)
        firestore_usage = fs.aggregate_usage(days=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"usage query failed: {exc}") from exc

    return {
        "days": window,
        "timeseries": timeseries,
        "usage": firestore_usage,
    }


@router.get("/errors")
def metrics_errors(
    days: int = Query(default=7, ge=1, le=365),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> dict:
    window = _sanitize_days(days, settings)
    try:
        report = bq.get_error_report(days=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"error report query failed: {exc}") from exc

    return {
        "days": window,
        **report,
    }


@router.get("/devices")
def metrics_devices(
    days: int = Query(default=7, ge=1, le=365),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> dict:
    window = _sanitize_days(days, settings)
    try:
        rows = bq.get_device_report(days=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"device report query failed: {exc}") from exc

    return {
        "days": window,
        "devices": rows,
    }


@router.get("/query-suggest")
def metrics_query_suggest(
    days: int = Query(default=7, ge=1, le=365),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> dict:
    window = _sanitize_days(days, settings)
    try:
        log_report = bq.get_query_suggest_report(days=window)
        fact_report = fs.aggregate_query_suggest_facts(days=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"query-suggest report failed: {exc}") from exc

    return {
        "days": window,
        "logs": log_report,
        "facts": fact_report,
    }
