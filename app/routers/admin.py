from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.contracts.admin import (
    LabelCreate,
    LabelListResponse,
    LabelPatch,
    LabelView,
    UserCreate,
    UserListResponse,
    UserPatch,
    UserView,
)
from app.dependencies import get_user_management_service
from app.security.auth import AdminIdentity, require_admin
from app.services.user_management import UserManagementService

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _user_payload(value: dict[str, Any]) -> dict[str, Any]:
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
        return HTTPException(status_code=404, detail=str(exc.args[0] if exc.args else "not found"))
    message = str(exc)
    code = 409 if "exists" in message or "in use" in message else 422
    return HTTPException(status_code=code, detail=message)


@router.get("/users", response_model=UserListResponse)
def list_users(
    include_inactive: bool = Query(default=True),
    _admin: AdminIdentity = Depends(require_admin),
    service: UserManagementService = Depends(get_user_management_service),
) -> dict[str, Any]:
    return {"users": [_user_payload(item) for item in service.list_users(include_inactive=include_inactive)]}


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
def delete_label(label_id: str, admin: AdminIdentity = Depends(require_admin), service: UserManagementService = Depends(get_user_management_service)) -> Response:
    try:
        service.delete_label(label_id, actor=admin.email)
    except (KeyError, ValueError) as exc:
        raise _translated(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
