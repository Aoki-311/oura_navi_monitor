from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.analytics import UserDetailResponse, UsersResponse
from app.dependencies import get_analytics_service, get_export_job_repository
from app.repositories.export_jobs import ExportJobRepository
from app.security.auth import AdminIdentity, require_admin
from app.services.analytics_service import AnalyticsService
from app.settings import Settings, get_settings
from app.time_window import TimeWindowValidationError, resolve_time_window

router = APIRouter(prefix="/api/export", tags=["export"])


class ExportJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["users", "user_detail"]
    rosterId: str = Field(default="", max_length=80)
    days: int = Field(default=30, ge=1, le=365)
    preset: str = Field(default="")
    start: str = Field(default="")
    end: str = Field(default="")
    q: str = Field(default="", max_length=120)
    areaKey: str = Field(default="", max_length=80)
    activity: str = Field(default="", pattern="^(|high|middle|low|dormant)$")

    @model_validator(mode="after")
    def _detail_requires_roster(self) -> "ExportJobRequest":
        if self.kind == "user_detail" and not self.rosterId:
            raise ValueError("rosterId is required for user_detail")
        return self


class ExportJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobId: str
    status: Literal["ready"]
    filename: str
    rowCount: int
    expiresAt: str
    downloadUrl: str


def _csv_text(headers: list[str], rows: list[list[object]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return "\ufeff" + stream.getvalue()


def _percent(value: object) -> str:
    return "-" if value is None else f"{float(value) * 100:.1f}%"


def _create_content(request: ExportJobRequest, service: AnalyticsService, settings: Settings) -> tuple[str, str, int]:
    if request.kind == "users":
        try:
            window = resolve_time_window(
                settings=settings, days=request.days, preset=request.preset, start=request.start, end=request.end
            )
        except TimeWindowValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        payload = UsersResponse.model_validate(
            service.users(q=request.q, area_key=request.areaKey, activity=request.activity, window=window)
        )
        rows = [
            [
                item.name, item.email, item.area, item.lastActiveAt or "-",
                item.activeDays7, item.questionCount7,
                _percent(item.completeDeliveryRate), item.activityLabel,
            ]
            for item in payload.users
        ]
        return (
            "monitor_users.csv",
            _csv_text(["社員名", "メール", "エリア", "最終利用", "直近7日利用日数", "直近7日メッセージ数", "回答成功率", "活性度"], rows),
            len(rows),
        )
    try:
        window = resolve_time_window(
            settings=settings, days=request.days, preset=request.preset, start=request.start, end=request.end
        )
    except TimeWindowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        payload = UserDetailResponse.model_validate(
            service.user_detail(request.rosterId, window=window)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="user not found") from exc
    profile, summary = payload.profile, payload.summary
    rows = [[
        profile.name, profile.email, profile.area, profile.workplace,
        profile.department, profile.mrExperience, summary.lastActiveAt or "-",
        summary.activeDays, summary.questions,
        summary.questionsPerActiveDay if summary.questionsPerActiveDay is not None else "-",
        _percent(summary.completeDeliveryRate),
    ]]
    return (
        f"monitor_user_{request.rosterId}.csv",
        _csv_text(["社員名", "メール", "エリア", "勤務地", "部門", "MR経験", "最終利用", "利用日数", "質問数", "利用日平均質問", "回答成功率"], rows),
        1,
    )


@router.post("/jobs", status_code=201, response_model=ExportJobResponse)
def create_export_job(
    request: ExportJobRequest,
    admin: AdminIdentity = Depends(require_admin),
    service: AnalyticsService = Depends(get_analytics_service),
    repository: ExportJobRepository = Depends(get_export_job_repository),
    settings: Settings = Depends(get_settings),
) -> dict:
    repository.cleanup_expired()
    filename, content, row_count = _create_content(request, service, settings)
    now = datetime.now(timezone.utc)
    job = repository.put({
        "job_id": f"export_{uuid4().hex}", "status": "ready", "filename": filename,
        "content": content, "row_count": row_count, "created_by": admin.email,
        "created_at": now, "expires_at": now + timedelta(hours=1), "kind": request.kind,
    })
    return {
        "jobId": job["job_id"], "status": "ready", "filename": filename, "rowCount": row_count,
        "expiresAt": job["expires_at"].isoformat(), "downloadUrl": f"/api/export/jobs/{job['job_id']}/download",
    }


def _owned_job(job_id: str, *, admin: AdminIdentity, repository: ExportJobRepository) -> dict:
    job = repository.get(job_id)
    if job is None or job.get("created_by") != admin.email:
        raise HTTPException(status_code=404, detail="export job not found")
    if repository.is_expired(job):
        raise HTTPException(status_code=410, detail="export job expired")
    return job


@router.get("/jobs/{job_id}", response_model=ExportJobResponse)
def get_export_job(job_id: str, admin: AdminIdentity = Depends(require_admin), repository: ExportJobRepository = Depends(get_export_job_repository)) -> dict:
    job = _owned_job(job_id, admin=admin, repository=repository)
    return {
        "jobId": job_id, "status": job["status"], "filename": job["filename"],
        "rowCount": job["row_count"], "expiresAt": job["expires_at"].isoformat(),
        "downloadUrl": f"/api/export/jobs/{job_id}/download",
    }


@router.get("/jobs/{job_id}/download")
def download_export_job(job_id: str, admin: AdminIdentity = Depends(require_admin), repository: ExportJobRepository = Depends(get_export_job_repository)) -> Response:
    job = _owned_job(job_id, admin=admin, repository=repository)
    return Response(
        content=str(job["content"]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{job["filename"]}"', "Cache-Control": "no-store"},
    )
