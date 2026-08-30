from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.contracts.admin import (
    LABEL_COLORS,
    LabelCreate,
    LabelDelete,
    LabelListResponse,
    LabelPatch,
    LabelView,
    ManagementMetadataResponse,
    ScopePreviewRequest,
    ScopePreviewResponse,
    UserCreate,
    UserListResponse,
    UserPatch,
    UserView,
)
from app.dependencies import get_user_management_service
from app.domain.management_errors import ManagementError
from app.domain.analysis_scopes import SCOPE_POLICY_VERSION
from app.domain.label_records import (
    CanonicalLabelRecord,
    read_canonical_label,
    read_canonical_label_collection,
)
from app.security.auth import AdminIdentity, require_admin
from app.domain.roster_records import (
    CanonicalRosterRecord,
    read_canonical_roster,
    read_canonical_roster_collection,
)
from app.services.user_management import UserManagementService

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _user_payload(
    value: dict[str, Any] | CanonicalRosterRecord,
) -> dict[str, Any]:
    record = (
        value
        if isinstance(value, CanonicalRosterRecord)
        else read_canonical_roster(value)
    )
    value = record.value
    evaluation = record.evaluation
    return {
        "rosterId": str(value.get("roster_id") or ""),
        "name": str(value.get("name") or ""),
        "email": str(value.get("email") or ""),
        "area": str(value.get("area") or ""),
        "areaKey": str(value.get("area_key") or ""),
        "workplace": str(value.get("workplace") or ""),
        "role": evaluation.normalized_role,
        "department": str(value.get("department") or ""),
        "mrExperience": value.get("mr_experience") or "-",
        "labelIds": list(value.get("label_ids") or []),
        "isActive": bool(value.get("is_active")),
        "identityBound": bool(
            str(value.get("chat_user_id") or "").strip()
            or str(value.get("user_id") or "").strip()
        ),
        "globalScopeEnabled": bool(
            record.analytics_eligible and evaluation.membership.global_enabled
        ),
        "userMapScopeEnabled": bool(
            record.analytics_eligible and evaluation.membership.user_map_enabled
        ),
        "scopePolicyVersion": SCOPE_POLICY_VERSION,
        "rosterIssues": list(record.issues),
        "updatedAt": _iso(value.get("updated_at")),
        "updatedBy": value.get("updated_by") or "",
    }


def _label_payload(
    value: dict[str, Any] | CanonicalLabelRecord,
) -> dict[str, Any]:
    record = (
        value
        if isinstance(value, CanonicalLabelRecord)
        else read_canonical_label(value)
    )
    value = record.value
    return {
        "labelId": value.get("label_id"),
        "name": value.get("name"),
        "color": value.get("color"),
        "usageCount": int(value.get("usage_count") or 0),
        "isActive": bool(value.get("is_active")),
        "labelIssues": list(record.issues),
        "updatedAt": _iso(value.get("updated_at")),
        "updatedBy": value.get("updated_by") or "",
    }


def _post_write_user_record(
    service: UserManagementService,
    stored: dict[str, Any],
) -> CanonicalRosterRecord:
    """Resolve a mutation response only from the post-write full snapshot."""

    target_id = str(
        stored.get("_document_id") or stored.get("roster_id") or ""
    ).strip()
    if not target_id:
        raise ManagementError(
            "readback_conflict",
            "written user has no stable roster id",
        )
    try:
        records = read_canonical_roster_collection(
            service.list_users(include_inactive=True)
        )
    except Exception as exc:
        raise ManagementError(
            "readback_conflict",
            "could not read the post-write roster snapshot",
        ) from exc
    matches = [
        record
        for record in records
        if str(record.document_id or record.value.get("roster_id") or "")
        == target_id
    ]
    if len(matches) != 1:
        raise ManagementError(
            "readback_conflict",
            "post-write roster snapshot did not contain one target record",
        )
    return matches[0]


def _post_write_label_record(
    service: UserManagementService,
    stored: dict[str, Any],
) -> CanonicalLabelRecord:
    """Resolve a label mutation from the full canonical post-write catalog."""

    target_id = str(
        stored.get("_document_id") or stored.get("label_id") or ""
    ).strip()
    if not target_id:
        raise ManagementError(
            "readback_conflict",
            "written label has no stable label id",
        )
    try:
        records = read_canonical_label_collection(
            service.list_labels(include_inactive=True)
        )
    except Exception as exc:
        raise ManagementError(
            "readback_conflict",
            "could not read the post-write label snapshot",
        ) from exc
    matches = [
        record
        for record in records
        if str(record.document_id or record.value.get("label_id") or "")
        == target_id
    ]
    if len(matches) != 1:
        raise ManagementError(
            "readback_conflict",
            "post-write label snapshot did not contain one target record",
        )
    return matches[0]


def _translated(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": str(exc.args[0] if exc.args else "not found")},
        )
    if isinstance(exc, ManagementError):
        status_code = 409 if exc.code in {
            "bound_email",
            "duplicate_email",
            "duplicate_identity",
            "duplicate_label",
            "label_in_use",
            "readback_conflict",
            "scope_policy_conflict",
            "update_conflict",
        } else 422
        return HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": str(exc)},
        )
    return HTTPException(
        status_code=422,
        detail={"code": "invalid_request", "message": str(exc)},
    )


@router.get("/users", response_model=UserListResponse)
def list_users(
    include_inactive: bool = Query(default=True),
    _admin: AdminIdentity = Depends(require_admin),
    service: UserManagementService = Depends(get_user_management_service),
) -> dict[str, Any]:
    records = read_canonical_roster_collection(
        service.list_users(include_inactive=include_inactive)
    )
    return {"users": [_user_payload(record) for record in records]}


@router.get("/metadata", response_model=ManagementMetadataResponse)
def management_metadata(
    _admin: AdminIdentity = Depends(require_admin),
    service: UserManagementService = Depends(get_user_management_service),
) -> dict[str, Any]:
    metadata = service.metadata()
    return {**metadata, "labelColors": sorted(LABEL_COLORS)}


@router.post("/scope-preview", response_model=ScopePreviewResponse)
def scope_preview(
    payload: ScopePreviewRequest,
    _admin: AdminIdentity = Depends(require_admin),
    service: UserManagementService = Depends(get_user_management_service),
) -> dict[str, Any]:
    return service.scope_preview(
        role=payload.role,
        department=payload.department,
        is_active=payload.is_active,
    )


@router.post("/users", status_code=status.HTTP_201_CREATED, response_model=UserView)
def create_user(payload: UserCreate, admin: AdminIdentity = Depends(require_admin), service: UserManagementService = Depends(get_user_management_service)) -> dict[str, Any]:
    try:
        stored = service.create_user(payload, actor=admin.email)
        return _user_payload(_post_write_user_record(service, stored))
    except (KeyError, ValueError) as exc:
        raise _translated(exc) from exc


@router.patch("/users/{roster_id}", response_model=UserView)
def update_user(roster_id: str, payload: UserPatch, admin: AdminIdentity = Depends(require_admin), service: UserManagementService = Depends(get_user_management_service)) -> dict[str, Any]:
    try:
        stored = service.update_user(roster_id, payload, actor=admin.email)
        return _user_payload(_post_write_user_record(service, stored))
    except (KeyError, ValueError) as exc:
        raise _translated(exc) from exc


@router.get("/labels", response_model=LabelListResponse)
def list_labels(include_inactive: bool = Query(default=True), _admin: AdminIdentity = Depends(require_admin), service: UserManagementService = Depends(get_user_management_service)) -> dict[str, Any]:
    records = read_canonical_label_collection(
        service.list_labels(include_inactive=include_inactive)
    )
    return {"labels": [_label_payload(record) for record in records]}


@router.post("/labels", status_code=status.HTTP_201_CREATED, response_model=LabelView)
def create_label(payload: LabelCreate, admin: AdminIdentity = Depends(require_admin), service: UserManagementService = Depends(get_user_management_service)) -> dict[str, Any]:
    try:
        stored = service.create_label(payload, actor=admin.email)
        return _label_payload(_post_write_label_record(service, stored))
    except (KeyError, ValueError) as exc:
        raise _translated(exc) from exc


@router.patch("/labels/{label_id}", response_model=LabelView)
def update_label(label_id: str, payload: LabelPatch, admin: AdminIdentity = Depends(require_admin), service: UserManagementService = Depends(get_user_management_service)) -> dict[str, Any]:
    try:
        stored = service.update_label(label_id, payload, actor=admin.email)
        return _label_payload(_post_write_label_record(service, stored))
    except (KeyError, ValueError) as exc:
        raise _translated(exc) from exc


@router.delete("/labels/{label_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_label(label_id: str, payload: LabelDelete, admin: AdminIdentity = Depends(require_admin), service: UserManagementService = Depends(get_user_management_service)) -> Response:
    try:
        service.delete_label(
            label_id,
            actor=admin.email,
            expected_updated_at=payload.expected_updated_at,
        )
    except (KeyError, ValueError) as exc:
        raise _translated(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
