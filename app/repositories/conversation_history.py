from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from google.cloud import firestore

from app.settings import Settings


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class ConversationHistoryRepository:
    """The sole Monitor reader for an already resolved LCS chat user."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        database = str(settings.monitor_firestore_database or "(default)").strip() or "(default)"
        self._client = client or firestore.Client(project=settings.monitor_project_id, database=database)
        self._root_collection = settings.monitor_firestore_chat_collection
        self._timezone = ZoneInfo(settings.monitor_timezone)

    def _root(self):
        return self._client.collection(self._root_collection)

    def _local_text(self, value: Any) -> str:
        parsed = _parse_datetime(value)
        return parsed.astimezone(self._timezone).strftime("%Y-%m-%d %H:%M:%S") if parsed else ""

    def list_conversations(self, *, chat_user_id: str, limit: int = 200) -> list[dict[str, Any]]:
        if not str(chat_user_id or "").strip():
            return []
        query = (
            self._root()
            .document(chat_user_id)
            .collection("conversations")
            .order_by("updatedAt", direction=firestore.Query.DESCENDING)
            .limit(max(1, min(int(limit), 500)))
        )
        rows: list[dict[str, Any]] = []
        for document in query.stream():
            payload = document.to_dict() or {}
            if str(payload.get("visibility") or "active").lower() == "hidden":
                continue
            rows.append(
                {
                    "conversationId": document.id,
                    "title": str(payload.get("title") or ""),
                    "messageCount": int(payload.get("messageCount") or 0),
                    "updatedAt": str(payload.get("updatedAt") or ""),
                    "updatedAtJst": self._local_text(payload.get("updatedAt")),
                }
            )
        return rows

    def list_messages(
        self,
        *,
        chat_user_id: str,
        conversation_id: str,
        limit: int = 100,
        cursor: str = "",
    ) -> dict[str, Any]:
        if not chat_user_id or not conversation_id:
            return {"messages": [], "page": {"nextCursor": ""}}
        query = (
            self._root()
            .document(chat_user_id)
            .collection("conversations")
            .document(conversation_id)
            .collection("messages")
            .order_by("timestamp", direction=firestore.Query.ASCENDING)
        )
        documents = list(query.stream())
        offset = 0
        if cursor:
            try:
                offset = max(0, int(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii")))
            except Exception as exc:
                raise ValueError("invalid message cursor") from exc
        page_size = max(1, min(int(limit), 500))
        selected = documents[offset : offset + page_size]
        messages = []
        for document in selected:
            payload = document.to_dict() or {}
            role = str(payload.get("role") or "").lower()
            messages.append(
                {
                    "messageId": document.id,
                    "timestampJst": self._local_text(payload.get("timestamp") or payload.get("updatedAt")),
                    "role": role,
                    "roleLabel": "ユーザー" if role == "user" else "アシスタント",
                    "content": str(payload.get("content") or ""),
                    "mode": str(payload.get("modeAtSend") or ""),
                    "feedback": str(payload.get("feedback") or "none"),
                    "status": str(payload.get("status") or ""),
                }
            )
        next_offset = offset + len(selected)
        next_cursor = (
            base64.urlsafe_b64encode(str(next_offset).encode("ascii")).decode("ascii")
            if next_offset < len(documents)
            else ""
        )
        return {"messages": messages, "page": {"nextCursor": next_cursor}}
