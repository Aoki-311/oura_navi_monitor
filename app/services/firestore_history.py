from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

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


def _normalize_mode(value: Any) -> str:
    mode = _as_text(value).lower()
    if mode in {"internal", "websearch", "deepthinking", "standard"}:
        return mode
    return "unknown"


_MODE_METRIC_KEYS = ("internal", "websearch")


def _normalize_chat_flow(value: Any) -> str:
    flow = _as_text(value).lower()
    if flow in {"new_chat", "continued_chat"}:
        return flow
    return ""


def _question_kind_from_message(payload: Dict[str, Any], *, user_turn_index: int | None = None) -> str:
    flow = _normalize_chat_flow(payload.get("chatFlowType"))
    if flow == "continued_chat":
        return "followup"
    if flow == "new_chat":
        return "new"
    if _as_text(payload.get("parentTurnId")):
        return "followup"
    if user_turn_index is not None:
        return "new" if user_turn_index == 0 else "followup"
    return ""


def _has_grounded_citation(payload: Dict[str, Any]) -> bool:
    grounded = payload.get("grounded")
    if not isinstance(grounded, dict):
        return False
    for key in ("citations", "citationIndex", "citation_index", "sources", "evidence"):
        value = grounded.get(key)
        if isinstance(value, list) and len(value) > 0:
            return True
    return False


def _normalize_error_reason(value: Any) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return "unknown"
    if len(text) > 120:
        text = text[:117].rstrip() + "..."
    return text


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
        tz_name = str(settings.monitor_timezone or "Asia/Tokyo").strip() or "Asia/Tokyo"
        self._tz_name = tz_name
        try:
            self._tz = ZoneInfo(tz_name)
        except Exception:
            self._tz = timezone(timedelta(hours=9))

    def _root(self):
        return self._client.collection(self._root_collection)

    def _to_local_text(self, value: Any) -> str:
        dt = _parse_iso(value)
        if dt is None:
            return ""
        return dt.astimezone(self._tz).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _in_window(*, dt: datetime | None, start: datetime, end: datetime) -> bool:
        return dt is not None and start <= dt < end

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
        size = max(1, min(int(limit or 100), 2000))
        keyword = _as_text(q).lower()
        query = self._root().order_by("updatedAt", direction=firestore.Query.DESCENDING).limit(size)
        out: List[Dict[str, Any]] = []
        for doc in query.stream():
            payload = doc.to_dict() or {}
            user_email = _as_text(payload.get("userEmail"))
            subject = _as_text(payload.get("subject"))
            if keyword and keyword not in doc.id.lower() and keyword not in user_email.lower() and keyword not in subject.lower():
                continue
            updated_at = _as_text(payload.get("updatedAt") or payload.get("lastSeenAt"))
            last_seen_at = _as_text(payload.get("lastSeenAt"))
            out.append(
                {
                    "userId": doc.id,
                    "updatedAt": updated_at,
                    "updatedAtJst": self._to_local_text(updated_at),
                    "lastSeenAt": last_seen_at,
                    "lastSeenAtJst": self._to_local_text(last_seen_at),
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
            if self._in_window(dt=updated, start=window_start, end=window_end):
                aggregate.active_users_in_window += 1
            if self._in_window(dt=updated, start=day_start, end=window_end):
                aggregate.dau += 1
            if self._in_window(dt=updated, start=week_start, end=window_end):
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
            updated_at = _as_text(payload.get("updatedAt"))
            created_at = _as_text(payload.get("createdAt"))
            deleted_at = _as_text(payload.get("deletedAt"))
            out.append(
                {
                    "id": doc.id,
                    "title": title,
                    "mode": mode,
                    "updatedAt": updated_at,
                    "updatedAtJst": self._to_local_text(updated_at),
                    "createdAt": created_at,
                    "createdAtJst": self._to_local_text(created_at),
                    "visibility": visibility,
                    "isFavorite": bool(payload.get("isFavorite")),
                    "messageCount": payload.get("messageCount"),
                    "integrityState": _as_text(payload.get("integrityState")),
                    "lastMessagePreview": preview,
                    "deletedAt": deleted_at,
                    "deletedAtJst": self._to_local_text(deleted_at),
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
        conv_mode = _as_text(conv_payload.get("mode"))

        size = max(1, min(int(limit or 500), 2000))
        query = conv_ref.collection("messages").order_by("timestamp", direction=firestore.Query.ASCENDING).limit(size)
        messages: List[Dict[str, Any]] = []
        user_turn_index = -1
        for msg in query.stream():
            payload = msg.to_dict() or {}
            timestamp = _as_text(payload.get("timestamp"))
            role_text = _as_text(payload.get("role"))
            role = role_text.lower()
            inferred_user_turn_index = None
            if role == "user":
                user_turn_index += 1
                inferred_user_turn_index = user_turn_index
            messages.append(
                {
                    "id": msg.id,
                    "role": role_text,
                    "content": _as_text(payload.get("content")),
                    "timestamp": timestamp,
                    "timestampJst": self._to_local_text(timestamp),
                    "status": _as_text(payload.get("status")),
                    "errorMessage": _as_text(payload.get("errorMessage")),
                    "feedback": _as_text(payload.get("feedback")),
                    "attachmentNames": payload.get("attachmentNames") or [],
                    "attachmentFileIds": payload.get("attachmentFileIds") or [],
                    "modeAtSend": _as_text(payload.get("modeAtSend") or conv_mode),
                    "chatFlowType": _as_text(payload.get("chatFlowType")),
                    "questionKind": _question_kind_from_message(payload, user_turn_index=inferred_user_turn_index),
                    "conversationIdAtSend": _as_text(payload.get("conversationIdAtSend")),
                    "turnId": _as_text(payload.get("turnId")),
                    "parentTurnId": _as_text(payload.get("parentTurnId")),
                    "clientOrigin": _as_text(payload.get("clientOrigin")),
                }
            )

        updated_at = _as_text(conv_payload.get("updatedAt"))
        created_at = _as_text(conv_payload.get("createdAt"))
        return {
            "conversation": {
                "id": conv_id,
                "title": _as_text(conv_payload.get("title")),
                "mode": _as_text(conv_payload.get("mode")),
                "updatedAt": updated_at,
                "updatedAtJst": self._to_local_text(updated_at),
                "createdAt": created_at,
                "createdAtJst": self._to_local_text(created_at),
                "visibility": _as_text(conv_payload.get("visibility") or "active"),
                "isFavorite": bool(conv_payload.get("isFavorite")),
                "messageCount": conv_payload.get("messageCount"),
                "integrityState": _as_text(conv_payload.get("integrityState")),
            },
            "messages": messages,
        }

    def export_user_conversation_messages(
        self,
        *,
        user_id: str,
        include_hidden: bool = True,
    ) -> List[Dict[str, Any]]:
        user = _as_text(user_id)
        if not user:
            return []
        user_doc = self._root().document(user).get()
        user_payload = user_doc.to_dict() or {}
        user_email = _as_text(user_payload.get("userEmail"))

        rows: List[Dict[str, Any]] = []
        conv_query = self._root().document(user).collection("conversations").order_by(
            "updatedAt", direction=firestore.Query.DESCENDING
        )
        for conv_doc in conv_query.stream():
            conv_payload = conv_doc.to_dict() or {}
            visibility = _as_text(conv_payload.get("visibility") or "active").lower()
            if visibility == "hidden" and not include_hidden:
                continue

            base = {
                "userId": user,
                "userEmail": user_email,
                "conversationId": conv_doc.id,
                "conversationTitle": _as_text(conv_payload.get("title")),
                "conversationMode": _as_text(conv_payload.get("mode")),
                "conversationUpdatedAt": _as_text(conv_payload.get("updatedAt")),
                "conversationUpdatedAtJst": self._to_local_text(conv_payload.get("updatedAt")),
                "conversationCreatedAt": _as_text(conv_payload.get("createdAt")),
                "conversationCreatedAtJst": self._to_local_text(conv_payload.get("createdAt")),
                "conversationVisibility": visibility,
                "conversationIsFavorite": bool(conv_payload.get("isFavorite")),
                "conversationIntegrityState": _as_text(conv_payload.get("integrityState")),
                "conversationMessageCount": conv_payload.get("messageCount"),
            }
            conv_mode = _as_text(conv_payload.get("mode"))

            has_message = False
            user_turn_index = -1
            msg_query = conv_doc.reference.collection("messages").order_by(
                "timestamp", direction=firestore.Query.ASCENDING
            )
            for msg_doc in msg_query.stream():
                has_message = True
                msg = msg_doc.to_dict() or {}
                timestamp = _as_text(msg.get("timestamp"))
                role = _as_text(msg.get("role")).lower()
                inferred_user_turn_index = None
                if role == "user":
                    user_turn_index += 1
                    inferred_user_turn_index = user_turn_index
                rows.append(
                    {
                        **base,
                        "messageId": msg_doc.id,
                        "messageTimestamp": timestamp,
                        "messageTimestampJst": self._to_local_text(timestamp),
                        "messageRole": _as_text(msg.get("role")),
                        "messageModeAtSend": _as_text(msg.get("modeAtSend") or conv_mode),
                        "messageChatFlowType": _as_text(msg.get("chatFlowType")),
                        "messageQuestionKind": _question_kind_from_message(
                            msg, user_turn_index=inferred_user_turn_index
                        ),
                        "messageStatus": _as_text(msg.get("status")),
                        "messageFeedback": _as_text(msg.get("feedback")),
                        "messageContent": _as_text(msg.get("content")),
                        "messageErrorMessage": _as_text(msg.get("errorMessage")),
                        "messageAttachmentNames": "|".join([str(x) for x in (msg.get("attachmentNames") or [])]),
                        "messageAttachmentFileIds": "|".join([str(x) for x in (msg.get("attachmentFileIds") or [])]),
                        "turnId": _as_text(msg.get("turnId")),
                        "parentTurnId": _as_text(msg.get("parentTurnId")),
                        "clientOrigin": _as_text(msg.get("clientOrigin")),
                    }
                )
            if not has_message:
                rows.append(
                    {
                        **base,
                        "messageId": "",
                        "messageTimestamp": "",
                        "messageTimestampJst": "",
                        "messageRole": "",
                        "messageModeAtSend": "",
                        "messageChatFlowType": "",
                        "messageQuestionKind": "",
                        "messageStatus": "",
                        "messageFeedback": "",
                        "messageContent": "",
                        "messageErrorMessage": "",
                        "messageAttachmentNames": "",
                        "messageAttachmentFileIds": "",
                        "turnId": "",
                        "parentTurnId": "",
                        "clientOrigin": "",
                    }
                )
        return rows

    def export_conversation_messages(self, *, user_id: str, conversation_id: str) -> List[Dict[str, Any]]:
        user = _as_text(user_id)
        conv_id = _as_text(conversation_id)
        if not user or not conv_id:
            return []

        conv_ref = self._root().document(user).collection("conversations").document(conv_id)
        conv_doc = conv_ref.get()
        if not conv_doc.exists:
            return []
        conv_payload = conv_doc.to_dict() or {}

        user_doc = self._root().document(user).get()
        user_payload = user_doc.to_dict() or {}
        user_email = _as_text(user_payload.get("userEmail"))

        base = {
            "userId": user,
            "userEmail": user_email,
            "conversationId": conv_id,
            "conversationTitle": _as_text(conv_payload.get("title")),
            "conversationMode": _as_text(conv_payload.get("mode")),
            "conversationUpdatedAt": _as_text(conv_payload.get("updatedAt")),
            "conversationUpdatedAtJst": self._to_local_text(conv_payload.get("updatedAt")),
            "conversationCreatedAt": _as_text(conv_payload.get("createdAt")),
            "conversationCreatedAtJst": self._to_local_text(conv_payload.get("createdAt")),
            "conversationVisibility": _as_text(conv_payload.get("visibility") or "active"),
            "conversationIsFavorite": bool(conv_payload.get("isFavorite")),
            "conversationIntegrityState": _as_text(conv_payload.get("integrityState")),
            "conversationMessageCount": conv_payload.get("messageCount"),
        }
        conv_mode = _as_text(conv_payload.get("mode"))

        rows: List[Dict[str, Any]] = []
        user_turn_index = -1
        query = conv_ref.collection("messages").order_by("timestamp", direction=firestore.Query.ASCENDING)
        for msg_doc in query.stream():
            msg = msg_doc.to_dict() or {}
            timestamp = _as_text(msg.get("timestamp"))
            role = _as_text(msg.get("role")).lower()
            inferred_user_turn_index = None
            if role == "user":
                user_turn_index += 1
                inferred_user_turn_index = user_turn_index
            rows.append(
                {
                    **base,
                    "messageId": msg_doc.id,
                    "messageTimestamp": timestamp,
                    "messageTimestampJst": self._to_local_text(timestamp),
                    "messageRole": _as_text(msg.get("role")),
                    "messageModeAtSend": _as_text(msg.get("modeAtSend") or conv_mode),
                    "messageChatFlowType": _as_text(msg.get("chatFlowType")),
                    "messageQuestionKind": _question_kind_from_message(msg, user_turn_index=inferred_user_turn_index),
                    "messageStatus": _as_text(msg.get("status")),
                    "messageFeedback": _as_text(msg.get("feedback")),
                    "messageContent": _as_text(msg.get("content")),
                    "messageErrorMessage": _as_text(msg.get("errorMessage")),
                    "messageAttachmentNames": "|".join([str(x) for x in (msg.get("attachmentNames") or [])]),
                    "messageAttachmentFileIds": "|".join([str(x) for x in (msg.get("attachmentFileIds") or [])]),
                    "turnId": _as_text(msg.get("turnId")),
                    "parentTurnId": _as_text(msg.get("parentTurnId")),
                    "clientOrigin": _as_text(msg.get("clientOrigin")),
                }
            )

        if not rows:
            rows.append(
                {
                    **base,
                    "messageId": "",
                    "messageTimestamp": "",
                    "messageTimestampJst": "",
                    "messageRole": "",
                    "messageModeAtSend": "",
                    "messageChatFlowType": "",
                    "messageQuestionKind": "",
                    "messageStatus": "",
                    "messageFeedback": "",
                    "messageContent": "",
                    "messageErrorMessage": "",
                    "messageAttachmentNames": "",
                    "messageAttachmentFileIds": "",
                    "turnId": "",
                    "parentTurnId": "",
                    "clientOrigin": "",
                }
            )
        return rows

    def aggregate_monitor_metrics(self, *, window: MetricsTimeWindow) -> Dict[str, Any]:
        window_start = window.start_utc
        window_end = window.end_utc
        window_start_iso = window_start.isoformat()
        window_end_iso = window_end.isoformat()
        day_start = window_end - timedelta(days=1)
        week_start = window_end - timedelta(days=7)

        users = self.list_users(limit=self._settings.monitor_max_users_scan)

        global_mode_counts: Dict[str, int] = {key: 0 for key in _MODE_METRIC_KEYS}
        global_error_reasons: Dict[str, int] = {}

        dau = 0
        wau = 0
        active_users_in_window = 0
        conversation_count = 0
        active_conversation_count = 0
        favorite_conversation_count = 0
        integrity_risk_conversation_count = 0
        message_count = 0
        assistant_message_count = 0
        citation_covered_count = 0
        message_failure_count = 0
        feedback_good_count = 0
        feedback_total_count = 0
        new_question_count = 0
        followup_count = 0

        users_out: List[Dict[str, Any]] = []

        for user in users:
            user_id = _as_text(user.get("userId"))
            user_email = _as_text(user.get("userEmail"))
            updated = _parse_iso(user.get("updatedAt") or user.get("lastSeenAt"))
            if self._in_window(dt=updated, start=window_start, end=window_end):
                active_users_in_window += 1
            if self._in_window(dt=updated, start=day_start, end=window_end):
                dau += 1
            if self._in_window(dt=updated, start=week_start, end=window_end):
                wau += 1

            user_mode_counts: Dict[str, int] = {key: 0 for key in _MODE_METRIC_KEYS}
            user_error_reasons: Dict[str, int] = {}

            user_conversation_count = 0
            user_active_conversation_count = 0
            user_favorite_conversation_count = 0
            user_integrity_risk_conversation_count = 0
            user_message_count = 0
            user_assistant_message_count = 0
            user_citation_covered_count = 0
            user_message_failure_count = 0
            user_feedback_good_count = 0
            user_feedback_total_count = 0
            user_new_question_count = 0
            user_followup_count = 0

            conv_ref = self._root().document(user_id).collection("conversations")
            for conv_doc in conv_ref.stream():
                conv_payload = conv_doc.to_dict() or {}
                visibility = _as_text(conv_payload.get("visibility") or "active").lower()
                if visibility == "hidden":
                    continue
                is_favorite = bool(conv_payload.get("isFavorite"))
                integrity_state = _as_text(conv_payload.get("integrityState")).lower()
                conv_mode = _normalize_mode(conv_payload.get("mode"))
                conv_has_window_message = False
                user_turn_index = -1

                msg_query = (
                    conv_doc.reference.collection("messages")
                    .where(filter=FieldFilter("timestamp", ">=", window_start_iso))
                    .where(filter=FieldFilter("timestamp", "<", window_end_iso))
                    .order_by("timestamp", direction=firestore.Query.ASCENDING)
                )
                for msg_doc in msg_query.stream():
                    msg_payload = msg_doc.to_dict() or {}
                    role = _as_text(msg_payload.get("role")).lower()
                    inferred_user_turn_index = None
                    if role == "user":
                        user_turn_index += 1
                        inferred_user_turn_index = user_turn_index
                    if not conv_has_window_message:
                        conv_has_window_message = True
                        conversation_count += 1
                        active_conversation_count += 1
                        user_conversation_count += 1
                        user_active_conversation_count += 1
                        if is_favorite:
                            favorite_conversation_count += 1
                            user_favorite_conversation_count += 1
                        if integrity_state in {"empty", "empty_shell", "unknown"}:
                            integrity_risk_conversation_count += 1
                            user_integrity_risk_conversation_count += 1

                    message_count += 1
                    user_message_count += 1

                    mode = _normalize_mode(msg_payload.get("modeAtSend") or conv_mode)
                    if mode in _MODE_METRIC_KEYS:
                        user_mode_counts[mode] = user_mode_counts.get(mode, 0) + 1
                        global_mode_counts[mode] = global_mode_counts.get(mode, 0) + 1

                    if role == "assistant":
                        assistant_message_count += 1
                        user_assistant_message_count += 1
                        if _has_grounded_citation(msg_payload):
                            citation_covered_count += 1
                            user_citation_covered_count += 1

                    if role == "user":
                        q_kind = _question_kind_from_message(msg_payload, user_turn_index=inferred_user_turn_index)
                        if q_kind == "followup":
                            followup_count += 1
                            user_followup_count += 1
                        elif q_kind == "new":
                            new_question_count += 1
                            user_new_question_count += 1

                    status = _as_text(msg_payload.get("status")).lower()
                    error_message = _as_text(msg_payload.get("errorMessage"))
                    if status == "error" or bool(error_message):
                        message_failure_count += 1
                        user_message_failure_count += 1
                        reason = _normalize_error_reason(error_message)
                        global_error_reasons[reason] = global_error_reasons.get(reason, 0) + 1
                        user_error_reasons[reason] = user_error_reasons.get(reason, 0) + 1

                    feedback = _as_text(msg_payload.get("feedback")).lower()
                    if feedback in {"good", "bad"}:
                        feedback_total_count += 1
                        user_feedback_total_count += 1
                        if feedback == "good":
                            feedback_good_count += 1
                            user_feedback_good_count += 1

            user_like_rate = (
                user_feedback_good_count / user_feedback_total_count if user_feedback_total_count > 0 else None
            )
            user_failure_rate = (
                user_message_failure_count / user_message_count if user_message_count > 0 else None
            )
            user_citation_rate = (
                user_citation_covered_count / user_assistant_message_count
                if user_assistant_message_count > 0
                else None
            )
            user_stickiness = (
                user_message_count / user_active_conversation_count
                if user_active_conversation_count > 0
                else None
            )
            user_favorite_rate = (
                user_favorite_conversation_count / user_conversation_count if user_conversation_count > 0 else None
            )
            user_integrity_risk_rate = (
                user_integrity_risk_conversation_count / user_conversation_count
                if user_conversation_count > 0
                else None
            )
            user_question_total = user_new_question_count + user_followup_count
            user_followup_rate = (
                user_followup_count / user_question_total if user_question_total > 0 else None
            )

            users_out.append(
                {
                    "userId": user_id,
                    "userEmail": user_email,
                    "subject": _as_text(user.get("subject")),
                    "updatedAt": _as_text(user.get("updatedAt")),
                    "updatedAtJst": _as_text(user.get("updatedAtJst")),
                    "conversationCount": user_conversation_count,
                    "activeConversationCount": user_active_conversation_count,
                    "messageCount": user_message_count,
                    "activeSessionStickiness": user_stickiness,
                    "feedbackGoodCount": user_feedback_good_count,
                    "feedbackTotalCount": user_feedback_total_count,
                    "feedbackLikeRate": user_like_rate,
                    "messageFailureCount": user_message_failure_count,
                    "messageFailureRate": user_failure_rate,
                    "assistantMessageCount": user_assistant_message_count,
                    "citationCoveredCount": user_citation_covered_count,
                    "citationCoverageRate": user_citation_rate,
                    "favoriteConversationCount": user_favorite_conversation_count,
                    "favoriteConversationRate": user_favorite_rate,
                    "integrityRiskConversationCount": user_integrity_risk_conversation_count,
                    "integrityRiskRate": user_integrity_risk_rate,
                    "modeCounts": user_mode_counts,
                    "modeDistribution": [
                        {"mode": key, "count": user_mode_counts.get(key, 0)}
                        for key in _MODE_METRIC_KEYS
                    ],
                    "newQuestionCount": user_new_question_count,
                    "followupCount": user_followup_count,
                    "followupRate": user_followup_rate,
                    "topMessageErrors": [
                        {"errorReason": key, "count": count}
                        for key, count in sorted(
                            user_error_reasons.items(), key=lambda item: item[1], reverse=True
                        )[:5]
                    ],
                }
            )

        feedback_like_rate = (feedback_good_count / feedback_total_count) if feedback_total_count > 0 else None
        message_failure_rate = (message_failure_count / message_count) if message_count > 0 else None
        citation_coverage_rate = (
            citation_covered_count / assistant_message_count if assistant_message_count > 0 else None
        )
        active_session_stickiness = (
            message_count / active_conversation_count if active_conversation_count > 0 else None
        )
        favorite_conversation_rate = (
            favorite_conversation_count / conversation_count if conversation_count > 0 else None
        )
        integrity_risk_rate = (
            integrity_risk_conversation_count / conversation_count if conversation_count > 0 else None
        )
        question_total = new_question_count + followup_count
        followup_rate = (followup_count / question_total) if question_total > 0 else None

        top_message_errors = [
            {"errorReason": key, "count": count}
            for key, count in sorted(global_error_reasons.items(), key=lambda item: item[1], reverse=True)[:10]
        ]

        mode_distribution = [
            {"mode": key, "count": global_mode_counts.get(key, 0)}
            for key in _MODE_METRIC_KEYS
        ]

        users_out.sort(
            key=lambda item: (
                int(item.get("messageCount") or 0),
                int(item.get("conversationCount") or 0),
            ),
            reverse=True,
        )

        return {
            "days": window.requested_days,
            "windowStart": window_start.isoformat(),
            "windowEnd": window_end.isoformat(),
            "timezone": self._tz_name,
            "dau": dau,
            "wau": wau,
            "activeUsersInWindow": active_users_in_window,
            "conversationCount": conversation_count,
            "activeConversationCount": active_conversation_count,
            "messageCount": message_count,
            "activeSessionStickiness": active_session_stickiness,
            "feedbackGoodCount": feedback_good_count,
            "feedbackTotalCount": feedback_total_count,
            "feedbackLikeRate": feedback_like_rate,
            "messageFailureCount": message_failure_count,
            "messageFailureRate": message_failure_rate,
            "assistantMessageCount": assistant_message_count,
            "citationCoveredCount": citation_covered_count,
            "citationCoverageRate": citation_coverage_rate,
            "favoriteConversationCount": favorite_conversation_count,
            "favoriteConversationRate": favorite_conversation_rate,
            "integrityRiskConversationCount": integrity_risk_conversation_count,
            "integrityRiskRate": integrity_risk_rate,
            "modeDistribution": mode_distribution,
            "newQuestionCount": new_question_count,
            "followupCount": followup_count,
            "followupRate": followup_rate,
            "topMessageErrors": top_message_errors,
            "users": users_out,
            "usersScanned": len(users),
        }
