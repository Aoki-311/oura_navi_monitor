from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.contracts.news_usage import NewsUsageDashboardResponse, NewsUsageReportResponse
from app.repositories.news_usage_repository import NewsUsageRepository
from app.security.auth import AdminIdentity, require_admin
from app.services.news_usage_service import (
    NewsUsageQuery,
    NewsUsageService,
    NewsUsageSnapshotConflictError,
)
from app.settings import Settings, get_settings
from app.time_window import TimeWindowValidationError, resolve_time_window


router = APIRouter(prefix="/api/news-usage", tags=["news-usage"])


@lru_cache(maxsize=1)
def get_news_usage_service() -> NewsUsageService:
    settings = get_settings()
    return NewsUsageService(
        repository=NewsUsageRepository(settings), settings=settings
    )


def _window(
    *, settings: Settings, days: int, preset: str, start: str, end: str, as_of: str
):
    try:
        return resolve_time_window(
            settings=settings,
            days=days,
            preset=preset,
            start=start,
            end=end,
            as_of=as_of,
        )
    except TimeWindowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _selection(
    *,
    channel: str,
    environment: str,
    business_unit: str,
    geography: str,
    category: str,
    society: str,
    q: str,
) -> NewsUsageQuery:
    return NewsUsageQuery(
        channel=channel,
        environment=environment,
        business_unit=business_unit,
        geography=geography,
        category=category,
        society=society,
        query=q,
    )


def _report(
    *,
    settings: Settings,
    service: NewsUsageService,
    days: int,
    preset: str,
    start: str,
    end: str,
    as_of: str,
    channel: str,
    environment: str,
    business_unit: str,
    geography: str,
    category: str,
    society: str,
    q: str,
) -> dict:
    window = _window(
        settings=settings,
        days=days,
        preset=preset,
        start=start,
        end=end,
        as_of=as_of,
    )
    try:
        return service.report(
            window=window,
            query=_selection(
                channel=channel,
                environment=environment,
                business_unit=business_unit,
                geography=geography,
                category=category,
                society=society,
                q=q,
            ),
        )
    except NewsUsageSnapshotConflictError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": exc.code,
                "message": "News / 学会の公開済み利用データを一貫して確認できません。",
            },
        ) from exc


def _dashboard(
    *, settings: Settings, service: NewsUsageService, days: int, preset: str,
    start: str, end: str, as_of: str, roster_id: str = "", area_key: str = "",
) -> dict:
    window = _window(
        settings=settings, days=days, preset=preset, start=start, end=end, as_of=as_of,
    )
    try:
        return service.dashboard(window=window, roster_id=roster_id, area_key=area_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="user not found") from exc
    except NewsUsageSnapshotConflictError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": "公開済みの利用データを確認できません。"},
        ) from exc


@router.get("/overview", response_model=NewsUsageDashboardResponse)
def news_usage_overview(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    as_of: str = Query(default="", max_length=80),
    area_key: str = Query(default="", max_length=80),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    service: NewsUsageService = Depends(get_news_usage_service),
) -> dict:
    return _dashboard(
        settings=settings, service=service, days=days, preset=preset,
        start=start, end=end, as_of=as_of, area_key=area_key,
    )


@router.get("/users/{roster_id}", response_model=NewsUsageDashboardResponse)
def news_usage_user(
    roster_id: str,
    days: int = Query(default=30, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    as_of: str = Query(default="", max_length=80),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    service: NewsUsageService = Depends(get_news_usage_service),
) -> dict:
    return _dashboard(
        settings=settings, service=service, days=days, preset=preset,
        start=start, end=end, as_of=as_of, roster_id=roster_id,
    )


@router.get("/report", response_model=NewsUsageReportResponse)
def news_usage_report(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    as_of: str = Query(default="", max_length=80),
    channel: str = Query(default="", pattern="^(|news|society)$"),
    environment: str = Query(default="", max_length=160),
    business_unit: str = Query(default="", max_length=160),
    geography: str = Query(default="", pattern="^(|domestic|overseas)$"),
    category: str = Query(default="", max_length=160),
    society: str = Query(default="", max_length=160),
    q: str = Query(default="", max_length=120),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    service: NewsUsageService = Depends(get_news_usage_service),
) -> dict:
    return _report(
        settings=settings,
        service=service,
        days=days,
        preset=preset,
        start=start,
        end=end,
        as_of=as_of,
        channel=channel,
        environment=environment,
        business_unit=business_unit,
        geography=geography,
        category=category,
        society=society,
        q=q,
    )


@router.get("/report.csv")
def news_usage_report_csv(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    as_of: str = Query(default="", max_length=80),
    channel: str = Query(default="", pattern="^(|news|society)$"),
    environment: str = Query(default="", max_length=160),
    business_unit: str = Query(default="", max_length=160),
    geography: str = Query(default="", pattern="^(|domestic|overseas)$"),
    category: str = Query(default="", max_length=160),
    society: str = Query(default="", max_length=160),
    q: str = Query(default="", max_length=120),
    expected_published_run_id: str = Query(min_length=1, max_length=160),
    expected_roster_fingerprint: str = Query(min_length=1, max_length=160),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    service: NewsUsageService = Depends(get_news_usage_service),
) -> Response:
    report = _report(
        settings=settings,
        service=service,
        days=days,
        preset=preset,
        start=start,
        end=end,
        as_of=as_of,
        channel=channel,
        environment=environment,
        business_unit=business_unit,
        geography=geography,
        category=category,
        society=society,
        q=q,
    )
    if report.get("state", {}).get("availability") != "available":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "news_usage_export_unavailable",
                "message": "公開済みのNews / 学会利用データがないためCSVを作成できません。",
            },
        )
    if (
        report.get("publishedRunId") != expected_published_run_id
        or report.get("rosterFingerprint") != expected_roster_fingerprint
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapshot_changed",
                "message": "表示後に公開データが更新されました。再読込してからCSVを作成してください。",
            },
        )
    filename = f"news-usage-{report['windowStart'][:10]}-{report['windowEnd'][:10]}.csv"
    return Response(
        content=service.csv_bytes(report),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
