from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.contracts.analytics import ConversationsResponse
from app.contracts.trace import TraceMessagesResponse
from app.dependencies import get_conversation_history_repository, get_user_directory_repository
from app.domain.analysis_scopes import AnalysisScope
from app.domain.roster_records import read_canonical_roster_collection
from app.repositories.conversation_history import ConversationHistoryRepository
from app.repositories.user_directory import UserDirectoryRepository
from app.security.auth import AdminIdentity, require_admin

router = APIRouter(prefix="/api/trace", tags=["conversation"])


def _trace_user(directory: UserDirectoryRepository, roster_id: str) -> dict:
    records = read_canonical_roster_collection(
        directory.list_users(include_inactive=True)
    )
    matches = [
        record
        for record in records
        if str(record.value.get("roster_id") or "").strip() == roster_id
    ]
    if (
        len(matches) != 1
        or not matches[0].identity_eligible
        or not matches[0].evaluation.membership.includes(AnalysisScope.USER_MAP)
    ):
        raise HTTPException(status_code=404, detail="user not found")
    record = matches[0]
    return record.value


@router.get("/conversations", response_model=ConversationsResponse)
def trace_conversations(
    roster_id: str = Query(min_length=1, max_length=80),
    limit: int = Query(default=200, ge=1, le=500),
    _admin: AdminIdentity = Depends(require_admin),
    directory: UserDirectoryRepository = Depends(get_user_directory_repository),
    conversations: ConversationHistoryRepository = Depends(get_conversation_history_repository),
) -> dict:
    user = _trace_user(directory, roster_id)
    chat_user_id = str(user.get("chat_user_id") or "").strip()
    if not chat_user_id:
        return {"conversations": [], "status": "identity_unmatched"}
    return {
        "conversations": conversations.list_conversations(
            chat_user_id=chat_user_id,
            limit=limit,
        ),
        "status": "ready",
    }


@router.get("/messages", response_model=TraceMessagesResponse)
def trace_messages(
    roster_id: str = Query(min_length=1, max_length=80),
    conversation_id: str = Query(min_length=1, max_length=180),
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str = Query(default="", max_length=200),
    _admin: AdminIdentity = Depends(require_admin),
    directory: UserDirectoryRepository = Depends(get_user_directory_repository),
    conversations: ConversationHistoryRepository = Depends(get_conversation_history_repository),
) -> dict:
    user = _trace_user(directory, roster_id)
    chat_user_id = str(user.get("chat_user_id") or "").strip()
    if not chat_user_id:
        return {"messages": [], "page": {"nextCursor": ""}, "status": "identity_unmatched"}
    try:
        result = conversations.list_messages(
            chat_user_id=chat_user_id,
            conversation_id=conversation_id,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**result, "status": "ready"}
