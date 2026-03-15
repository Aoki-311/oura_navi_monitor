from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_firestore_history_service
from app.security.auth import AdminIdentity, require_admin
from app.services.firestore_history import FirestoreHistoryService

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("/users")
def history_users(
    limit: int = Query(default=100, ge=1, le=500),
    q: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> dict:
    try:
        users = fs.list_users(limit=limit, q=q)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"list users failed: {exc}") from exc
    return {
        "count": len(users),
        "users": users,
    }


@router.get("/users/{user_id}/conversations")
def history_user_conversations(
    user_id: str,
    include_hidden: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    q: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> dict:
    try:
        conversations = fs.list_user_conversations(user_id=user_id, include_hidden=include_hidden, limit=limit, q=q)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"list conversations failed: {exc}") from exc
    return {
        "userId": user_id,
        "count": len(conversations),
        "conversations": conversations,
    }


@router.get("/users/{user_id}/conversations/{conversation_id}")
def history_conversation_messages(
    user_id: str,
    conversation_id: str,
    limit: int = Query(default=500, ge=1, le=2000),
    _admin: AdminIdentity = Depends(require_admin),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> dict:
    try:
        result = fs.get_conversation_messages(user_id=user_id, conversation_id=conversation_id, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"get conversation messages failed: {exc}") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    return {
        "userId": user_id,
        "conversationId": conversation_id,
        **result,
    }
