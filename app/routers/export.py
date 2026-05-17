from __future__ import annotations

import csv
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import Any, Dict, Iterable, List, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.responses import Response

from app.dependencies import get_bigquery_metrics_service, get_firestore_history_service
from app.security.auth import AdminIdentity, require_admin
from app.services.bigquery_metrics import BigQueryMetricsService
from app.services.firestore_history import FirestoreHistoryService
from app.settings import Settings, get_settings
from app.time_window import MetricsTimeWindow, TimeWindowValidationError, resolve_time_window

router = APIRouter(prefix="/api/export", tags=["export"])
logger = logging.getLogger(__name__)

ExportScope = Literal["all", "user"]
ExportOutputData = Literal["ユーザー監視一覧", "メッセージ明細", "ユーザーサマリー"]


class ExportFilters(BaseModel):
    activity: str = ""
    q: str = ""
    userId: str = ""
    userEmail: str = ""


class ExportJobRequest(BaseModel):
    scope: ExportScope = "all"
    outputData: ExportOutputData = "ユーザー監視一覧"
    preset: str = "last_7d"
    start: str = ""
    end: str = ""
    filters: ExportFilters = Field(default_factory=ExportFilters)


_ALL_OUTPUTS = {"ユーザー監視一覧", "メッセージ明細"}
_USER_OUTPUTS = {"ユーザーサマリー", "メッセージ明細"}
_USER_MONITORING_HEADERS = [
    "ユーザーID",
    "メールアドレス",
    "最終利用日時",
    "直近7日利用日数",
    "直近7日メッセージ数",
    "根拠カバレッジ率",
    "低評価率",
    "活性度区分",
]
_USER_SUMMARY_HEADERS = [
    "ユーザーID",
    "メールアドレス",
    "最終利用日時",
    "活性度区分",
    "メッセージ数",
    "回答成功率",
    "低カバレッジ率",
    "低評価率",
    "追問数",
]
_MESSAGE_HEADERS = [
    "user_id",
    "user_email",
    "conversation_id",
    "title",
    "created_at",
    "役割",
    "message原文",
    "質問カテゴリ",
    "モード",
    "デバイス",
    "フィードバック",
]


def _build_window(
    *,
    settings: Settings,
    days: int,
    preset: str,
    start: str,
    end: str,
) -> MetricsTimeWindow:
    try:
        return resolve_time_window(
            settings=settings,
            days=days,
            preset=preset,
            start=start,
            end=end,
        )
    except TimeWindowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _rows_to_csv(rows: Iterable[Dict[str, Any]], headers: List[str] | None = None) -> str:
    rows_list = list(rows)
    if headers is None:
        headers = []
        seen = set()
        for row in rows_list:
            for key in row.keys():
                if key not in seen:
                    seen.add(key)
                    headers.append(key)
    if not headers:
        return ""

    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows_list:
        writer.writerow(row)
    return buf.getvalue()


def _csv_response(filename: str, rows: Iterable[Dict[str, Any]]) -> Response:
    body = "\ufeff" + _rows_to_csv(rows)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _display_rate(value: Any) -> str:
    try:
        if value is None:
            return "-"
        return f"{float(value) * 100:.1f}%"
    except Exception:
        return "-"


def _display_count(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except Exception:
        return "0"


def _activity_level_from_summary(summary: Dict[str, Any]) -> str:
    message_count_3d = int(summary.get("messageCount3d") or 0)
    message_count_7d = int(summary.get("messageCount7d") or 0)
    message_count_14d = int(summary.get("messageCount14d") or 0)
    if message_count_3d >= 3:
        return "高アクティブ"
    if 1 <= message_count_7d <= 2:
        return "中アクティブ"
    if message_count_14d >= 1:
        return "低アクティブ"
    return "休眠ユーザー"


def _window_for_export(*, settings: Settings, request: ExportJobRequest) -> MetricsTimeWindow:
    preset = "" if request.preset == "custom" else request.preset
    if request.preset == "custom" and (not request.start or not request.end):
        raise HTTPException(status_code=422, detail="custom export requires start and end")
    return _build_window(settings=settings, days=7, preset=preset, start=request.start, end=request.end)


def _user_monitoring_rows_for_export(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "ユーザーID": row.get("userId") or "",
                "メールアドレス": row.get("userEmail") or "",
                "最終利用日時": row.get("lastActiveAtJst") or "",
                "直近7日利用日数": _display_count(row.get("activeDays7")),
                "直近7日メッセージ数": _display_count(row.get("messageCount7d")),
                "根拠カバレッジ率": _display_rate(row.get("coverageRate")),
                "低評価率": _display_rate(row.get("badFeedbackRate")),
                "活性度区分": row.get("activityLevel") or "",
            }
        )
    return out


def _user_summary_row_for_export(*, profile: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ユーザーID": profile.get("userId") or "",
        "メールアドレス": profile.get("userEmail") or "",
        "最終利用日時": summary.get("lastActiveAtJst") or profile.get("updatedAtJst") or "",
        "活性度区分": summary.get("activityLevel") or _activity_level_from_summary(summary),
        "メッセージ数": _display_count(summary.get("messageCount")),
        "回答成功率": _display_rate(summary.get("answerSuccessRate")),
        "低カバレッジ率": _display_rate(summary.get("lowCoverageRate")),
        "低評価率": _display_rate(summary.get("badFeedbackRate")),
        "追問数": _display_count(summary.get("followupCount")),
    }


def _job_filename(*, request: ExportJobRequest, window: MetricsTimeWindow) -> str:
    preset = request.preset or window.preset or f"{window.requested_days}d"
    if request.outputData == "ユーザー監視一覧":
        prefix = "user_monitoring"
    elif request.outputData == "ユーザーサマリー":
        prefix = "user_summary"
    else:
        prefix = "message_details"
    suffix = request.scope
    return f"{prefix}_{suffix}_{preset}.csv"


def _validate_export_request(request: ExportJobRequest) -> None:
    allowed = _USER_OUTPUTS if request.scope == "user" else _ALL_OUTPUTS
    if request.outputData not in allowed:
        raise HTTPException(status_code=422, detail=f"{request.outputData} is not supported for {request.scope} export")
    if request.scope == "user" and not (request.filters.userId or request.filters.userEmail):
        raise HTTPException(status_code=422, detail="user export requires filters.userId or filters.userEmail")


def _raise_legacy_export_gone() -> None:
    raise HTTPException(status_code=410, detail="deprecated; use POST /api/export/jobs")


def _store_export_job(
    *,
    request: ExportJobRequest,
    window: MetricsTimeWindow,
    admin: AdminIdentity,
    fs: FirestoreHistoryService,
    headers: List[str],
    rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    job_id = f"export-{uuid.uuid4().hex[:16]}"
    filename = _job_filename(request=request, window=window)
    content = "\ufeff" + _rows_to_csv(rows, headers=headers)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    request_payload = request.model_dump()
    stored = fs.save_export_job(
        job_id=job_id,
        admin_email=admin.email,
        request_payload={
            **request_payload,
            "window": {
                "start": window.start_utc.isoformat(),
                "end": window.end_utc.isoformat(),
                "timezone": window.timezone,
            },
        },
        filename=filename,
        content=content,
        row_count=len(rows),
        expires_at=expires_at,
    )
    audit_payload = {
        "event": "export_job_created",
        "job_id": job_id,
        "admin_email": admin.email,
        "scope": request.scope,
        "output_data": request.outputData,
        "preset": request.preset,
        "start": request.start,
        "end": request.end,
        "filters": request.filters.model_dump(),
        "row_count": len(rows),
        "message_content_included": request.outputData == "メッセージ明細",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info("export_audit_json=%s", json.dumps(audit_payload, ensure_ascii=False, sort_keys=True))
    return {
        "jobId": job_id,
        "status": "ready",
        "downloadUrl": f"/api/export/jobs/{job_id}/download",
        "expiresAt": expires_at.isoformat(),
        "filename": stored.get("filename") or filename,
        "rowCount": len(rows),
    }


@router.post("/jobs")
def create_export_job(
    request: ExportJobRequest,
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> Dict[str, Any]:
    _validate_export_request(request)
    window = _window_for_export(settings=settings, request=request)
    try:
        if request.outputData == "ユーザー監視一覧":
            source_rows = bq.get_request_user_monitoring_rows(
                window=window,
                activity=request.filters.activity,
                q=request.filters.q,
                limit=1000,
            )
            rows = _user_monitoring_rows_for_export(source_rows)
            headers = _USER_MONITORING_HEADERS
        elif request.outputData == "ユーザーサマリー":
            profile = fs.resolve_user_profile(user_id=request.filters.userId, user_email=request.filters.userEmail)
            if profile is None:
                raise HTTPException(status_code=404, detail="user not found")
            summary_payload = bq.get_user_detail_summary(
                window=window,
                user_key=str(profile.get("userId") or request.filters.userId),
                user_keys=[
                    str(profile.get("userId") or ""),
                    str(profile.get("userEmail") or ""),
                    request.filters.userId,
                    request.filters.userEmail,
                ],
            )
            rows = [_user_summary_row_for_export(profile=profile, summary=summary_payload.get("summary") or {})]
            headers = _USER_SUMMARY_HEADERS
        else:
            rows = fs.export_message_detail_rows(
                window=window,
                user_id=request.filters.userId,
                user_email=request.filters.userEmail,
                include_hidden=True,
                include_user_columns=True,
            )
            headers = _MESSAGE_HEADERS
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"export job failed: {exc}") from exc
    return _store_export_job(request=request, window=window, admin=_admin, fs=fs, headers=headers, rows=rows)


@router.get("/jobs/{job_id}")
def get_export_job(
    job_id: str,
    _admin: AdminIdentity = Depends(require_admin),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> Dict[str, Any]:
    job = fs.get_export_job(job_id=job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="export job not found")
    expires_at = job.get("expiresAt")
    if isinstance(expires_at, datetime) and expires_at.astimezone(timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="export job expired")
    return {
        "jobId": job.get("jobId") or job_id,
        "status": job.get("status") or "ready",
        "downloadUrl": f"/api/export/jobs/{job_id}/download",
        "expiresAt": expires_at.isoformat() if isinstance(expires_at, datetime) else str(expires_at or ""),
        "filename": job.get("filename") or "",
        "rowCount": job.get("rowCount") or 0,
    }


@router.get("/jobs/{job_id}/download")
def download_export_job(
    job_id: str,
    _admin: AdminIdentity = Depends(require_admin),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> Response:
    job = fs.get_export_job(job_id=job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="export job not found")
    expires_at = job.get("expiresAt")
    if isinstance(expires_at, datetime) and expires_at.astimezone(timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="export job expired")
    content = str(job.get("content") or "")
    filename = str(job.get("filename") or f"{job_id}.csv")
    return Response(
        content=content,
        media_type=str(job.get("contentType") or "text/csv; charset=utf-8"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/usage.csv")
def export_usage_csv(
    days: int = Query(default=30, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> Response:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        rows = bq.get_usage_timeseries(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"usage export failed: {exc}") from exc
    return _csv_response(f"usage_{window.requested_days}d.csv", rows)


@router.get("/errors/trend.csv")
def export_errors_trend_csv(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> Response:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        report = bq.get_error_report(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"errors trend export failed: {exc}") from exc
    return _csv_response(f"errors_trend_{window.requested_days}d.csv", report.get("trend", []))


@router.get("/errors/endpoints.csv")
def export_errors_endpoints_csv(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> Response:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        report = bq.get_error_report(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"errors endpoint export failed: {exc}") from exc
    return _csv_response(f"errors_endpoints_{window.requested_days}d.csv", report.get("topEndpoints", []))


@router.get("/errors/types.csv")
def export_errors_types_csv(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> Response:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        report = bq.get_error_report(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"errors type export failed: {exc}") from exc
    return _csv_response(f"errors_types_{window.requested_days}d.csv", report.get("topErrors", []))


@router.get("/devices.csv")
def export_devices_csv(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> Response:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        rows = bq.get_device_report(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"devices export failed: {exc}") from exc
    return _csv_response(f"devices_{window.requested_days}d.csv", rows)


@router.get("/query-suggest/stages.csv")
def export_qs_stages_csv(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> Response:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        report = bq.get_query_suggest_report(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"query-suggest stage export failed: {exc}") from exc
    return _csv_response(f"query_suggest_stages_{window.requested_days}d.csv", report.get("stages", []))


@router.get("/query-suggest/fallbacks.csv")
def export_qs_fallbacks_csv(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> Response:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        report = bq.get_query_suggest_report(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"query-suggest fallback export failed: {exc}") from exc
    return _csv_response(f"query_suggest_fallbacks_{window.requested_days}d.csv", report.get("fallbackSources", []))


@router.get("/query-suggest/facts.csv")
def export_qs_facts_csv(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> Response:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        facts = fs.aggregate_query_suggest_facts(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"query-suggest facts export failed: {exc}") from exc
    return _csv_response(f"query_suggest_facts_{window.requested_days}d.csv", [facts])


@router.get("/user-monitoring.csv")
def export_user_monitoring_csv(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default="last_7d"),
    start: str = Query(default=""),
    end: str = Query(default=""),
    activity: str = Query(default=""),
    q: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> Response:
    _raise_legacy_export_gone()


@router.get("/users.csv")
def export_users_csv(
    user_id: str = Query(...),
    include_hidden: bool = Query(default=True),
    _admin: AdminIdentity = Depends(require_admin),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> Response:
    _raise_legacy_export_gone()


@router.get("/conversations.csv")
def export_conversations_csv(
    user_id: str = Query(...),
    conversation_id: str = Query(...),
    _admin: AdminIdentity = Depends(require_admin),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> Response:
    _raise_legacy_export_gone()


@router.get("/messages.csv")
def export_messages_csv(
    user_id: str = Query(default=""),
    conversation_id: str = Query(default=""),
    include_hidden: bool = Query(default=True),
    _admin: AdminIdentity = Depends(require_admin),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> Response:
    _raise_legacy_export_gone()
