from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.analytics import UserDetailResponse, UsersResponse
from app.dependencies import get_analytics_service, get_export_job_repository
from app.repositories.export_jobs import ExportJobRepository
from app.security.auth import AdminIdentity, require_admin
from app.services.analytics_service import (
    AnalyticsService,
    AnalyticsSnapshotConflictError,
)
from app.settings import Settings, get_settings
from app.time_window import TimeWindowValidationError, resolve_time_window

router = APIRouter(prefix="/api/export", tags=["export"])


class ExportJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["overview_users", "user_detail"]
    rosterId: str = Field(default="", max_length=80)
    preset: str = Field(default="")
    q: str = Field(default="", max_length=120)
    areaKey: str = Field(default="", max_length=80)
    activity: str = Field(default="", pattern="^(|high|middle|low|dormant)$")
    sort: str = Field(default="last_desc", pattern="^(last_desc|name_asc|messages_desc|success_desc)$")
    expectedPublishedRunId: str = Field(min_length=1, max_length=160)
    expectedRosterFingerprint: str = Field(min_length=1, max_length=160)
    expectedContentFingerprint: str = Field(min_length=1, max_length=160)
    expectedScopePolicyVersion: str = Field(min_length=1, max_length=80)
    expectedWindowStart: str = Field(min_length=1, max_length=80)
    expectedWindowEnd: str = Field(min_length=1, max_length=80)
    expectedWindowTimezone: str = Field(min_length=1, max_length=80)
    idempotencyKey: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")

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


_MEASUREMENT_LABELS = {
    "measured": "計測済み",
    "partial": "一部計測",
    "not_measured": "未計測",
    "no_usage": "利用なし",
}
_MEASUREMENT_REASON_LABELS = {
    "complete": "対象データを全件計測",
    "no_usage": "対象データなし",
    "population_without_usage": "期間内に利用がない対象者を含む",
    "historical_unavailable": "過去データに項目なし",
    "current_data_gap": "現行データの記録欠落",
    "mixed_history_and_current_gap": "過去未記録と現行欠落が混在",
    "mixed_no_usage_and_data_gap": "期間内未利用と記録欠落が混在",
}


def _csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


def _csv_text(headers: list[str], rows: list[list[object]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow([_csv_cell(value) for value in headers])
    writer.writerows([[_csv_cell(value) for value in row] for row in rows])
    return "\ufeff" + stream.getvalue()


def _percent(value: object) -> str:
    return "" if value is None else f"{float(value) * 100:.1f}%"


def _safe_filename(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    cleaned = re.sub(r"[^0-9A-Za-z_.\-\u3040-\u30ff\u3400-\u9fff]+", "_", normalized)
    cleaned = cleaned.strip("._")
    return (cleaned or "monitor_export")[:160] + ("" if cleaned.endswith(".csv") else ".csv")


def _measurement_fields(
    value: object,
    state: str,
    reason: str,
    measured: int,
    total: int,
) -> list[object]:
    return [
        value,
        state,
        _MEASUREMENT_LABELS[state],
        reason,
        _MEASUREMENT_REASON_LABELS[reason],
        measured,
        total,
    ]


def _assert_snapshot(
    *,
    actual_run: str,
    actual_roster: str,
    actual_content: str,
    actual_policy: str,
    actual_window_start: str,
    actual_window_end: str,
    actual_window_timezone: str,
    request: ExportJobRequest,
) -> None:
    if (
        actual_run != request.expectedPublishedRunId
        or actual_roster != request.expectedRosterFingerprint
        or actual_content != request.expectedContentFingerprint
        or actual_policy != request.expectedScopePolicyVersion
        or actual_window_start != request.expectedWindowStart
        or actual_window_end != request.expectedWindowEnd
        or actual_window_timezone != request.expectedWindowTimezone
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapshot_changed",
                "message": "画面のデータ版が更新されました。再読込してからCSVを作成してください。",
            },
        )


def _assert_complete_content(payload: UsersResponse | UserDetailResponse) -> None:
    if payload.contentDiagnostics.state == "complete":
        return
    raise HTTPException(
        status_code=503,
        detail={
            "code": "content_snapshot_incomplete",
            "message": "分析ラベルを取得できないため、完全なCSVは作成できません。復旧後に再実行してください。",
            "issues": list(payload.contentDiagnostics.issues),
        },
    )


def _validated_export_payload(
    model: type[UsersResponse] | type[UserDetailResponse],
    raw: Any,
) -> UsersResponse | UserDetailResponse:
    diagnostics = raw.get("contentDiagnostics") if isinstance(raw, dict) else None
    issues = diagnostics.get("issues") if isinstance(diagnostics, dict) else None
    roster_isolated_count = (
        diagnostics.get("rosterIsolatedCount")
        if isinstance(diagnostics, dict)
        else None
    )
    if (
        not isinstance(diagnostics, dict)
        or diagnostics.get("state") != "complete"
        or diagnostics.get("labelCatalogStatus") != "available"
        or diagnostics.get("rosterStatus") != "available"
        or isinstance(roster_isolated_count, bool)
        or not isinstance(roster_isolated_count, int)
        or roster_isolated_count != 0
        or not isinstance(diagnostics.get("rosterIssueCounts"), dict)
        or bool(diagnostics.get("rosterIssueCounts"))
        or not isinstance(issues, list)
        or bool(issues)
    ):
        safe_issues = (
            [str(issue) for issue in issues if str(issue)]
            if isinstance(issues, list)
            else ["invalid_content_diagnostics"]
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "content_snapshot_incomplete",
                "message": "分析ラベルを確認できないため、完全なCSVは作成できません。復旧後に再実行してください。",
                "issues": safe_issues or ["invalid_content_diagnostics"],
            },
        )
    return model.model_validate(raw)


def _create_content(
    request: ExportJobRequest,
    service: AnalyticsService,
    settings: Settings,
) -> tuple[str, str, int, dict[str, str]]:
    generated_at = datetime.now(timezone.utc)
    if request.kind == "overview_users":
        try:
            window = resolve_time_window(
                settings=settings,
                days=settings.monitor_default_days,
                preset="",
                start=request.expectedWindowStart,
                end=request.expectedWindowEnd,
                as_of=request.expectedWindowEnd,
                require_current_as_of=False,
            )
        except TimeWindowValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        payload = _validated_export_payload(
            UsersResponse,
            service.overview_users(
                q=request.q,
                area_key=request.areaKey,
                activity=request.activity,
                sort=request.sort,
                window=window,
            ),
        )
        _assert_complete_content(payload)
        _assert_snapshot(
            actual_run=payload.publishedRunId,
            actual_roster=payload.rosterFingerprint,
            actual_content=payload.contentFingerprint,
            actual_policy=payload.scopePolicyVersion,
            actual_window_start=payload.windowStart,
            actual_window_end=payload.windowEnd,
            actual_window_timezone=payload.windowTimezone,
            request=request,
        )
        rows = [
            [
                item.rosterId, item.name, item.email, item.role, item.department,
                item.area, item.workplace, "; ".join(label.name for label in item.labels),
                item.lastActiveAt,
                item.activeDays7, item.userMessageCount7,
                _percent(item.completeDelivery.value),
                item.completeDelivery.measurementState,
                _MEASUREMENT_LABELS[item.completeDelivery.measurementState],
                item.completeDelivery.measurementReason,
                _MEASUREMENT_REASON_LABELS[item.completeDelivery.measurementReason],
                item.completeDelivery.measuredCount,
                item.completeDelivery.totalCount,
                item.activityLabel,
                payload.freshness.dataThrough,
                payload.publishedRunId,
                payload.scopePolicyVersion,
                payload.windowStart,
                payload.windowEnd,
                payload.windowTimezone,
            ]
            for item in payload.users
        ]
        stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
        return (
            _safe_filename(f"monitor_summary_{request.preset or 'custom'}_{stamp}.csv"),
            _csv_text([
                "roster_id", "社員名", "メール", "役割", "部門", "エリア", "勤務地", "分析ラベル",
                "最終利用", "直近7日利用日数", "直近7日消息数", "回答成功率", "measurement_state",
                "計測状態", "measurement_reason", "計測理由", "計測済み件数", "対象件数",
                "活性度", "データ反映時点", "公開Run", "対象ポリシー",
                "分析開始時刻", "分析終了時刻", "分析タイムゾーン",
            ], rows),
            len(rows),
            {
                "published_run_id": payload.publishedRunId,
                "data_through": payload.freshness.dataThrough,
                "scope_policy_version": payload.scopePolicyVersion,
                "roster_fingerprint": payload.rosterFingerprint,
                "content_fingerprint": payload.contentFingerprint,
                "window_start": payload.windowStart,
                "window_end": payload.windowEnd,
                "window_timezone": payload.windowTimezone,
            },
        )
    try:
        window = resolve_time_window(
            settings=settings,
            days=settings.monitor_default_days,
            preset="",
            start=request.expectedWindowStart,
            end=request.expectedWindowEnd,
            as_of=request.expectedWindowEnd,
            require_current_as_of=False,
        )
    except TimeWindowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        payload = _validated_export_payload(
            UserDetailResponse,
            service.user_detail(request.rosterId, window=window),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="user not found") from exc
    _assert_complete_content(payload)
    _assert_snapshot(
        actual_run=payload.publishedRunId,
        actual_roster=payload.rosterFingerprint,
        actual_content=payload.contentFingerprint,
        actual_policy=payload.scopePolicyVersion,
        actual_window_start=payload.windowStart,
        actual_window_end=payload.windowEnd,
        actual_window_timezone=payload.windowTimezone,
        request=request,
    )
    profile, summary = payload.profile, payload.summary
    rows: list[list[object]] = []

    def add(section: str, item: str, value: object, note: str = "") -> None:
        rows.append([section, item, value, "", "", "", "", "", "", note])

    def add_measurement(section: str, item: str, value: object, measurement: object, note: str = "") -> None:
        rows.append([
            section,
            item,
            *_measurement_fields(
                value,
                measurement.measurementState,
                measurement.measurementReason,
                measurement.measuredCount,
                measurement.totalCount,
            ),
            note,
        ])

    for item, value in (
        ("社員名", profile.name), ("メール", profile.email), ("役割", profile.role),
        ("部門", profile.department), ("エリア", profile.area), ("勤務地", profile.workplace),
        ("MR経験", profile.mrExperience), ("分析ラベル", "; ".join(label.name for label in profile.labels)),
    ):
        add("プロフィール", item, value)
    add("集計情報", "データ反映時点", payload.freshness.dataThrough)
    add("集計情報", "公開Run", payload.publishedRunId)
    add("集計情報", "対象ポリシー", payload.scopePolicyVersion)
    add("集計情報", "分析開始時刻", payload.windowStart)
    add("集計情報", "分析終了時刻", payload.windowEnd)
    add("集計情報", "分析タイムゾーン", payload.windowTimezone)
    add("個人サマリー", "最終利用", summary.lastActiveAt)
    add("個人サマリー", "利用日数", summary.activeDays)
    add("個人サマリー", "質問数", summary.questions)
    add("個人サマリー", "1日平均質問", summary.questionsPerActiveDay)
    add_measurement("個人サマリー", "回答成功率", _percent(summary.completeDelivery.value), summary.completeDelivery)
    add_measurement("個人サマリー", "P95応答時間", summary.p95Latency.valueMs, summary.p95Latency, "ミリ秒")
    for label, comparison in (("同じ地域", payload.comparisons.area), ("同じ役割", payload.comparisons.role)):
        add("比較", f"{label}・対象", comparison.label)
        add("比較", f"{label}・人数", comparison.peerCount)
        add("比較", f"{label}・平均質問", comparison.averageQuestions)
        add("比較", f"{label}・平均利用日", comparison.averageActiveDays)
        add_measurement("比較", f"{label}・平均回答成功率", _percent(comparison.averageCompleteDelivery.value), comparison.averageCompleteDelivery)
    for trend in payload.trend:
        add("利用推移", f"{trend.date}・質問数", trend.questions, "途中集計" if trend.isPartial else "")
        add_measurement("利用推移", f"{trend.date}・回答成功率", _percent(trend.completeDelivery.value), trend.completeDelivery)
    for row in payload.products:
        add("製品ニーズ", row.label, row.count)
    add_measurement("製品ニーズ", "製品判定", _percent(payload.productResolution.resolutionRate), payload.productResolution)
    for section, values, measurement in (
        ("質問種類", payload.tasks, payload.taskMeasurement),
        ("質問テーマ", payload.questionCategories, payload.questionCategoryMeasurement),
        ("利用モード", payload.modes, payload.modeMeasurement),
        ("デバイス", payload.devices, payload.deviceMeasurement),
    ):
        for row in values:
            add(section, row.label or row.key, row.count)
        add_measurement(section, "計測範囲", "", measurement)
    add("除外事項", "会話本文", "通常の分析CSVには含めません")
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    return (
        _safe_filename(f"monitor_user_{request.rosterId}_{request.preset or 'custom'}_{stamp}.csv"),
        _csv_text([
            "セクション", "項目", "値", "measurement_state", "計測状態",
            "measurement_reason", "計測理由", "計測済み件数", "対象件数", "補足",
        ], rows),
        len(rows),
        {
            "published_run_id": payload.publishedRunId,
            "data_through": payload.freshness.dataThrough,
            "scope_policy_version": payload.scopePolicyVersion,
            "roster_fingerprint": payload.rosterFingerprint,
            "content_fingerprint": payload.contentFingerprint,
            "window_start": payload.windowStart,
            "window_end": payload.windowEnd,
            "window_timezone": payload.windowTimezone,
        },
    )


def _job_response(job: dict[str, object]) -> dict[str, object]:
    job_id = str(job["job_id"])
    expires_at = job["expires_at"]
    if not isinstance(expires_at, datetime):
        raise RuntimeError("export job has an invalid expiration")
    return {
        "jobId": job_id,
        "status": "ready",
        "filename": str(job["filename"]),
        "rowCount": int(job["row_count"]),
        "expiresAt": expires_at.isoformat(),
        "downloadUrl": f"/api/export/jobs/{job_id}/download",
    }


@router.post("/jobs", status_code=201, response_model=ExportJobResponse)
def create_export_job(
    request: ExportJobRequest,
    admin: AdminIdentity = Depends(require_admin),
    service: AnalyticsService = Depends(get_analytics_service),
    repository: ExportJobRepository = Depends(get_export_job_repository),
    settings: Settings = Depends(get_settings),
) -> dict:
    repository.cleanup_expired()
    owner = admin.email.strip().lower()
    request_hash = hashlib.sha256(
        json.dumps(
            request.model_dump(exclude={"idempotencyKey"}, mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    job_id = "export_" + hashlib.sha256(
        f"{owner}\0{request.idempotencyKey}".encode("utf-8")
    ).hexdigest()[:40]
    existing = repository.get(job_id)
    if existing is not None and not repository.is_expired(existing):
        if (
            existing.get("created_by") != owner
            or existing.get("request_hash") != request_hash
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "idempotency_conflict",
                    "message": "同じCSV作成キーが別の条件で使われました。画面を再読込してください。",
                },
            )
        return _job_response(existing)

    try:
        filename, content, row_count, snapshot_metadata = _create_content(
            request, service, settings
        )
    except AnalyticsSnapshotConflictError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": exc.code,
                "message": "公開済み分析スナップショットを一貫して確認できないため、CSVを作成できません。",
            },
        ) from exc
    now = datetime.now(timezone.utc)
    job = repository.put_idempotent(
        {
            "job_id": job_id,
            "status": "ready",
            "filename": filename,
            "content": content,
            "row_count": row_count,
            "created_by": owner,
            "created_at": now,
            "expires_at": now + timedelta(hours=1),
            "kind": request.kind,
            "request_hash": request_hash,
            "snapshot_metadata": snapshot_metadata,
        }
    )
    if job.get("created_by") != owner or job.get("request_hash") != request_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotency_conflict",
                "message": "同じCSV作成キーが別の条件で使われました。画面を再読込してください。",
            },
        )
    return _job_response(job)


def _owned_job(job_id: str, *, admin: AdminIdentity, repository: ExportJobRepository) -> dict:
    job = repository.get(job_id)
    owner = admin.email.strip().lower()
    if job is None or str(job.get("created_by") or "").strip().lower() != owner:
        raise HTTPException(status_code=404, detail="export job not found")
    if repository.is_expired(job):
        raise HTTPException(status_code=410, detail="export job expired")
    return job


@router.get("/jobs/{job_id}", response_model=ExportJobResponse)
def get_export_job(job_id: str, admin: AdminIdentity = Depends(require_admin), repository: ExportJobRepository = Depends(get_export_job_repository)) -> dict:
    job = _owned_job(job_id, admin=admin, repository=repository)
    return _job_response(job)


@router.get("/jobs/{job_id}/download")
def download_export_job(job_id: str, admin: AdminIdentity = Depends(require_admin), repository: ExportJobRepository = Depends(get_export_job_repository)) -> Response:
    job = _owned_job(job_id, admin=admin, repository=repository)
    return Response(
        content=str(job["content"]), media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                "attachment; filename=monitor_export.csv; "
                f"filename*=UTF-8''{quote(str(job['filename']))}"
            ),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/jobs/{job_id}", status_code=204)
def delete_export_job(
    job_id: str,
    admin: AdminIdentity = Depends(require_admin),
    repository: ExportJobRepository = Depends(get_export_job_repository),
) -> Response:
    _owned_job(job_id, admin=admin, repository=repository)
    repository.delete(job_id)
    return Response(status_code=204)
