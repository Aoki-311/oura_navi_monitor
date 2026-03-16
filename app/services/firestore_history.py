from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.settings import Settings
from app.time_window import MetricsTimeWindow


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _as_text(value: Any) -> str:
    return str(value or "").strip()


@dataclass
class UsageAggregate:
    dau: int = 0
    wau: int = 0
    active_users_in_window: int = 0
    conversation_count: int = 0
    message_count: int = 0
    feedback_good_count: int = 0
    feedback_total_count: int = 0


class FirestoreHistoryService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        db_id = str(settings.monitor_firestore_database or "(default)").strip() or "(default)"
        self._client = firestore.Client(project=settings.monitor_project_id, database=db_id)
        self._root_collection = settings.monitor_firestore_chat_collection

    def _root(self):
        return self._client.collection(self._root_collection)

    def _aggregate_feedback_counts_fast_path(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[int, int] | None:
        """
        Best-effort fast path:
        Count only explicit feedback messages via collection_group query.
        Falls back to None when index/permission/query support is unavailable.
        """
        start_iso = window_start.isoformat()
        end_iso = window_end.isoformat()
        root_prefix = f"{self._root_collection}/"

        def _count_for_value(value: str) -> int:
            query = (
                self._client.collection_group("messages")
                .where(filter=FieldFilter("feedback", "==", value))
                .where(filter=FieldFilter("timestamp", ">=", start_iso))
                .where(filter=FieldFilter("timestamp", "<", end_iso))
            )
            count = 0
            for doc in query.stream():
                path = _as_text(getattr(doc.reference, "path", ""))
                if path.startswith(root_prefix):
                    count += 1
            return count

        try:
            good = _count_for_value("good")
            bad = _count_for_value("bad")
            return good, (good + bad)
        except Exception:
            return None

    def list_users(self, *, limit: int = 100, q: str = "") -> List[Dict[str, Any]]:
        size = max(1, min(int(limit or 100), 500))
        keyword = _as_text(q).lower()
        query = self._root().order_by("updatedAt", direction=firestore.Query.DESCENDING).limit(size)
        out: List[Dict[str, Any]] = []
        for doc in query.stream():
            payload = doc.to_dict() or {}
            user_email = _as_text(payload.get("userEmail"))
            subject = _as_text(payload.get("subject"))
            if keyword and keyword not in doc.id.lower() and keyword not in user_email.lower() and keyword not in subject.lower():
                continue
            out.append(
                {
                    "userId": doc.id,
                    "updatedAt": _as_text(payload.get("updatedAt") or payload.get("lastSeenAt")),
                    "lastSeenAt": _as_text(payload.get("lastSeenAt")),
                    "identitySource": _as_text(payload.get("identitySource")),
                    "identityVerified": bool(payload.get("identityVerified")),
                    "userEmail": user_email,
                    "subject": subject,
                }
            )
        return out

    def aggregate_usage(self, *, window: MetricsTimeWindow) -> Dict[str, Any]:
        window_start = window.start_utc
        window_end = window.end_utc
        day_start = window_end - timedelta(days=1)
        week_start = window_end - timedelta(days=7)
        feedback_fast_path = self._aggregate_feedback_counts_fast_path(
            window_start=window_start,
            window_end=window_end,
        )
        feedback_source = "collection_group" if feedback_fast_path is not None else "message_scan"

        users = self.list_users(limit=self._settings.monitor_max_users_scan)
        aggregate = UsageAggregate()
        if feedback_fast_path is not None:
            aggregate.feedback_good_count = feedback_fast_path[0]
            aggregate.feedback_total_count = feedback_fast_path[1]

        for user in users:
            updated = _parse_iso(user.get("updatedAt") or user.get("lastSeenAt"))
            if updated is None:
                continue
            user_id = _as_text(user.get("userId"))
            if window_start <= updated < window_end:
                aggregate.active_users_in_window += 1
            if day_start <= updated < window_end:
                aggregate.dau += 1
            if week_start <= updated < window_end:
                aggregate.wau += 1

            conversations_ref = self._root().document(user_id).collection("conversations")
            for conv in conversations_ref.stream():
                conv_payload = conv.to_dict() or {}
                visibility = _as_text(conv_payload.get("visibility") or "active").lower()
                if visibility == "hidden":
                    continue
                conv_updated = _parse_iso(conv_payload.get("updatedAt"))
                if conv_updated is None or conv_updated < window_start or conv_updated >= window_end:
                    continue
                aggregate.conversation_count += 1
                message_count = conv_payload.get("messageCount")
                message_docs_for_feedback = None
                if isinstance(message_count, int) and message_count >= 0:
                    aggregate.message_count += message_count
                else:
                    message_docs_for_feedback = list(conv.reference.collection("messages").stream())
                    msg_count = len(message_docs_for_feedback)
                    aggregate.message_count += msg_count

                if feedback_fast_path is None:
                    message_iter = (
                        message_docs_for_feedback
                        if message_docs_for_feedback is not None
                        else conv.reference.collection("messages").stream()
                    )
                    for msg_doc in message_iter:
                        msg_payload = msg_doc.to_dict() or {}
                        feedback = _as_text(msg_payload.get("feedback")).lower()
                        if feedback not in {"good", "bad"}:
                            continue
                        msg_ts = _parse_iso(msg_payload.get("timestamp") or msg_payload.get("updatedAt"))
                        if msg_ts is not None and (msg_ts < window_start or msg_ts >= window_end):
                            continue
                        aggregate.feedback_total_count += 1
                        if feedback == "good":
                            aggregate.feedback_good_count += 1

        feedback_like_rate = (
            aggregate.feedback_good_count / aggregate.feedback_total_count
            if aggregate.feedback_total_count > 0
            else None
        )

        return {
            "days": window.requested_days,
            "dau": aggregate.dau,
            "wau": aggregate.wau,
            "activeUsersInWindow": aggregate.active_users_in_window,
            "conversationCount": aggregate.conversation_count,
            "messageCount": aggregate.message_count,
            "feedbackGoodCount": aggregate.feedback_good_count,
            "feedbackTotalCount": aggregate.feedback_total_count,
            "feedbackLikeRate": feedback_like_rate,
            "feedbackLikeRateSource": feedback_source,
            "usersScanned": len(users),
        }

    def aggregate_query_suggest_facts(self, *, window: MetricsTimeWindow) -> Dict[str, Any]:
        window_start = window.start_utc
        window_end = window.end_utc

        total_impressions = 0
        total_clicks = 0
        total_adoptions = 0
        total_edit_after_accept = 0
        total_dismisses = 0
        documents_scanned = 0

        users = self.list_users(limit=self._settings.monitor_max_users_scan)
        for user in users:
            user_id = _as_text(user.get("userId"))
            conversations_ref = self._root().document(user_id).collection("conversations")
            for conv in conversations_ref.stream():
                conv_payload = conv.to_dict() or {}
                visibility = _as_text(conv_payload.get("visibility") or "active").lower()
                if visibility == "hidden":
                    continue

                runtime_summary = conv_payload.get("querySuggestRuntimeSummary")
                runtime_payload: Dict[str, Any]
                if isinstance(runtime_summary, dict) and runtime_summary:
                    runtime_payload = runtime_summary
                else:
                    runtime_doc = conv.reference.collection("runtime").document("query_suggest").get()
                    runtime_payload = runtime_doc.to_dict() or {}
                documents_scanned += 1

                for fact in runtime_payload.get("suggestionFacts", []) or []:
                    if not isinstance(fact, dict):
                        continue
                    last_event = _parse_iso(fact.get("lastEventAt"))
                    if last_event is not None and (last_event < window_start or last_event >= window_end):
                        continue
                    total_impressions += max(0, int(fact.get("impressions") or 0))
                    total_clicks += max(0, int(fact.get("clicks") or 0))
                    total_adoptions += max(0, int(fact.get("adoptions") or 0))
                    total_edit_after_accept += max(0, int(fact.get("editAfterAccepts") or 0))
                    total_dismisses += max(0, int(fact.get("dismisses") or 0))

        click_rate = (total_clicks / total_impressions) if total_impressions > 0 else None
        adoption_rate = (total_adoptions / total_clicks) if total_clicks > 0 else None
        edit_after_accept_rate = (
            total_edit_after_accept / total_adoptions if total_adoptions > 0 else None
        )

        return {
            "days": window.requested_days,
            "impressions": total_impressions,
            "clicks": total_clicks,
            "adoptions": total_adoptions,
            "editAfterAccepts": total_edit_after_accept,
            "dismisses": total_dismisses,
            "clickRate": click_rate,
            "adoptionRate": adoption_rate,
            "editAfterAcceptRate": edit_after_accept_rate,
            "documentsScanned": documents_scanned,
        }

    def list_user_conversations(
        self,
        *,
        user_id: str,
        include_hidden: bool = False,
        limit: int = 200,
        q: str = "",
    ) -> List[Dict[str, Any]]:
        user = _as_text(user_id)
        if not user:
            return []
        size = max(1, min(int(limit or 200), 500))
        keyword = _as_text(q).lower()
        query = (
            self._root()
            .document(user)
            .collection("conversations")
            .order_by("updatedAt", direction=firestore.Query.DESCENDING)
            .limit(size)
        )
        out: List[Dict[str, Any]] = []
        for doc in query.stream():
            payload = doc.to_dict() or {}
            visibility = _as_text(payload.get("visibility") or "active").lower()
            if visibility == "hidden" and not include_hidden:
                continue
            title = _as_text(payload.get("title"))
            preview = _as_text(payload.get("lastMessagePreview"))
            mode = _as_text(payload.get("mode"))
            if keyword and keyword not in doc.id.lower() and keyword not in title.lower() and keyword not in preview.lower() and keyword not in mode.lower():
                continue
            out.append(
                {
                    "id": doc.id,
                    "title": title,
                    "mode": mode,
                    "updatedAt": _as_text(payload.get("updatedAt")),
                    "createdAt": _as_text(payload.get("createdAt")),
                    "visibility": visibility,
                    "isFavorite": bool(payload.get("isFavorite")),
                    "messageCount": payload.get("messageCount"),
                    "integrityState": _as_text(payload.get("integrityState")),
                    "lastMessagePreview": preview,
                    "deletedAt": _as_text(payload.get("deletedAt")),
                }
            )
        return out

    def get_conversation_messages(
        self,
        *,
        user_id: str,
        conversation_id: str,
        limit: int = 500,
    ) -> Dict[str, Any] | None:
        user = _as_text(user_id)
        conv_id = _as_text(conversation_id)
        if not user or not conv_id:
            return None

        conv_ref = self._root().document(user).collection("conversations").document(conv_id)
        conv_doc = conv_ref.get()
        if not conv_doc.exists:
            return None
        conv_payload = conv_doc.to_dict() or {}

        size = max(1, min(int(limit or 500), 2000))
        query = conv_ref.collection("messages").order_by("timestamp", direction=firestore.Query.ASCENDING).limit(size)
        messages: List[Dict[str, Any]] = []
        for msg in query.stream():
            payload = msg.to_dict() or {}
            messages.append(
                {
                    "id": msg.id,
                    "role": _as_text(payload.get("role")),
                    "content": _as_text(payload.get("content")),
                    "timestamp": _as_text(payload.get("timestamp")),
                    "status": _as_text(payload.get("status")),
                    "errorMessage": _as_text(payload.get("errorMessage")),
                    "feedback": _as_text(payload.get("feedback")),
                    "attachmentNames": payload.get("attachmentNames") or [],
                    "attachmentFileIds": payload.get("attachmentFileIds") or [],
                }
            )

        return {
            "conversation": {
                "id": conv_id,
                "title": _as_text(conv_payload.get("title")),
                "mode": _as_text(conv_payload.get("mode")),
                "updatedAt": _as_text(conv_payload.get("updatedAt")),
                "createdAt": _as_text(conv_payload.get("createdAt")),
                "visibility": _as_text(conv_payload.get("visibility") or "active"),
                "isFavorite": bool(conv_payload.get("isFavorite")),
                "messageCount": conv_payload.get("messageCount"),
                "integrityState": _as_text(conv_payload.get("integrityState")),
            },
            "messages": messages,
        }
