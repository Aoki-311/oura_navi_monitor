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
    UserCreate,
    UserListResponse,
    UserPatch,
    UserView,
)
from app.dependencies import get_user_management_service
from app.domain.management_errors import ManagementError
from app.domain.analysis_scopes import Department, membership_for
from app.security.auth import AdminIdentity, require_admin
from app.services.user_management import UserManagementService

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _user_payload(value: dict[str, Any]) -> dict[str, Any]:
    membership = membership_for(
        Department(value.get("department")), is_active=bool(value.get("is_active"))
    )
    return {
        "rosterId": value.get("roster_id"),
        "name": value.get("name"),
        "email": value.get("email"),
        "area": value.get("area"),
        "areaKey": value.get("area_key"),
        "workplace": value.get("workplace"),
        "role": value.get("role"),
        "department": value.get("department"),
        "mrExperience": value.get("mr_experience") or "-",
        "labelIds": list(value.get("label_ids") or []),
        "isActive": bool(value.get("is_active")),
        "identityBound": bool(
            str(value.get("chat_user_id") or "").strip()
            or str(value.get("user_id") or "").strip()
        ),
        "globalScopeEnabled": membership.global_enabled,
        "userMapScopeEnabled": membership.user_map_enabled,
        "updatedAt": _iso(value.get("updated_at")),
        "updatedBy": value.get("updated_by") or "",
    }


def _label_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "labelId": value.get("label_id"),
        "name": value.get("name"),
        "color": value.get("color"),
        "usageCount": int(value.get("usage_count") or 0),
        "isActive": bool(value.get("is_active")),
        "updatedAt": _iso(value.get("updated_at")),
        "updatedBy": value.get("updated_by") or "",
    }


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
    return {"users": [_user_payload(item) for item in service.list_users(include_inactive=include_inactive)]}


@router.get("/metadata", response_model=ManagementMetadataResponse)
def management_metadata(
    _admin: AdminIdentity = Depends(require_admin),
    service: UserManagementService = Depends(get_user_management_service),
) -> dict[str, Any]:
    metadata = service.metadata()
    return {**metadata, "labelColors": sorted(LABEL_COLORS)}


@router.post("/users", status_code=status.HTTP_201_CREATED, response_model=UserView)
def create_user(payload: UserCreate, admin: AdminIdentity = Depends(require_admin), service: UserManagementService = Depends(get_user_management_service)) -> dict[str, Any]:
    try:
        return _user_payload(service.create_user(payload, actor=admin.email))
    except (KeyError, ValueError) as exc:
        raise _translated(exc) from exc


@router.patch("/users/{roster_id}", response_model=UserView)
def update_user(roster_id: str, payload: UserPatch, admin: AdminIdentity = Depends(require_admin), service: UserManagementService = Depends(get_user_management_service)) -> dict[str, Any]:
    try:
        return _user_payload(service.update_user(roster_id, payload, actor=admin.email))
    except (KeyError, ValueError) as exc:
        raise _translated(exc) from exc


@router.get("/labels", response_model=LabelListResponse)
def list_labels(include_inactive: bool = Query(default=True), _admin: AdminIdentity = Depends(require_admin), service: UserManagementService = Depends(get_user_management_service)) -> dict[str, Any]:
    return {"labels": [_label_payload(item) for item in service.list_labels(include_inactive=include_inactive)]}


@router.post("/labels", status_code=status.HTTP_201_CREATED, response_model=LabelView)
def create_label(payload: LabelCreate, admin: AdminIdentity = Depends(require_admin), service: UserManagementService = Depends(get_user_management_service)) -> dict[str, Any]:
    try:
        return _label_payload(service.create_label(payload, actor=admin.email))
    except (KeyError, ValueError) as exc:
        raise _translated(exc) from exc


@router.patch("/labels/{label_id}", response_model=LabelView)
def update_label(label_id: str, payload: LabelPatch, admin: AdminIdentity = Depends(require_admin), service: UserManagementService = Depends(get_user_management_service)) -> dict[str, Any]:
    try:
        return _label_payload(service.update_label(label_id, payload, actor=admin.email))
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
