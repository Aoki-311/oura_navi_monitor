from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

from google.cloud import bigquery, firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.contracts.admin import normalize_email
from app.domain.analysis_scopes import Department, membership_for
from app.repositories.user_directory import UserDirectoryRepository
from app.services.user_management import UserManagementService
from app.settings import Settings


def _timestamp(value: Any) -> datetime | None:
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


def _firestore_iso(value: datetime) -> str:
    resolved = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _stable_id(*parts: str) -> str:
    seed = "|".join(str(part or "").strip() for part in parts)
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def project_conversation(
    *, roster_id: str, user_id: str, conversation_id: str, conversation: dict[str, Any], messages: list[dict[str, Any]]
) -> dict[str, Any]:
    user_messages = [item for item in messages if str(item.get("role") or "").lower() == "user"]
    assistant_messages = [item for item in messages if str(item.get("role") or "").lower() == "assistant"]
    timestamps = [value for item in messages if (value := _timestamp(item.get("timestamp") or item.get("updatedAt"))) is not None]
    modes = Counter(str(item.get("modeAtSend") or "").strip().lower() for item in messages if str(item.get("modeAtSend") or "").strip())
    updated_at = _timestamp(conversation.get("updatedAt")) or max(timestamps, default=datetime.now(timezone.utc))
    return {
        "event_id": f"conversation:{_stable_id(roster_id, conversation_id)}",
        "conversation_id": conversation_id,
        "user_id": user_id,
        "roster_id": roster_id,
        "first_active_at": min(timestamps, default=updated_at),
        "last_active_at": max(timestamps, default=updated_at),
        "updated_date": updated_at.date(),
        "user_message_count": len(user_messages),
        "assistant_message_count": len(assistant_messages),
        "followup_count": max(0, len(user_messages) - 1),
        "active_days": len({value.date() for value in timestamps}),
        "primary_mode": modes.most_common(1)[0][0] if modes else str(conversation.get("mode") or ""),
        "status": str(conversation.get("visibility") or "active"),
        "source_event_ts": updated_at,
        "materialized_at": datetime.now(timezone.utc),
    }


def project_citations(
    *, roster_id: str, user_id: str, conversation_id: str, messages: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for message in messages:
        if str(message.get("role") or "").lower() != "assistant":
            continue
        grounded = message.get("grounded") if isinstance(message.get("grounded"), dict) else {}
        citations = grounded.get("citations") if isinstance(grounded.get("citations"), list) else []
        request_id = str(grounded.get("requestId") or message.get("requestId") or "").strip()
        message_id = str(message.get("id") or message.get("messageId") or "").strip()
        answer_ts = _timestamp(message.get("timestamp") or message.get("updatedAt"))
        if answer_ts is None:
            continue
        for index, citation in enumerate(citations):
            if not isinstance(citation, dict):
                continue
            citation_id = str(citation.get("citation_id") or "").strip()
            rows.append(
                {
                    "event_id": f"citation:{_stable_id(roster_id, conversation_id, message_id, citation_id, str(index))}",
                    "answer_event_id": f"answer:{request_id}" if request_id else "",
                    "answer_ts": answer_ts,
                    "answer_date": answer_ts.date(),
                    "user_id": user_id,
                    "roster_id": roster_id,
                    "message_id": message_id,
                    "citation_order": index,
                    "source_type": str(citation.get("source_type") or ""),
                    "source_system": "",
                    "document_key": citation_id,
                    "display_title": str(citation.get("display_title") or citation.get("title") or "")[:500],
                    "page_number": int(citation["page"]) if str(citation.get("page") or "").isdigit() else None,
                    "access_status": str(citation.get("access_status") or ""),
                    "trust_tier": "",
                    "primary_product_key": "",
                    "source_event_ts": answer_ts,
                    "materialized_at": datetime.now(timezone.utc),
                }
            )
    return rows


USER_SCOPE_SCHEMA = [
    ("roster_id", "STRING"), ("user_id", "STRING"), ("area", "STRING"), ("area_key", "STRING"),
    ("workplace", "STRING"), ("role", "STRING"), ("department", "STRING"), ("mr_experience", "STRING"),
    ("is_active", "BOOL"), ("global_scope_enabled", "BOOL"), ("user_map_scope_enabled", "BOOL"),
    ("is_admin", "BOOL"), ("updated_at", "TIMESTAMP"),
]
CONVERSATION_SCHEMA = [
    ("event_id", "STRING"), ("conversation_id", "STRING"), ("user_id", "STRING"), ("roster_id", "STRING"),
    ("first_active_at", "TIMESTAMP"), ("last_active_at", "TIMESTAMP"), ("updated_date", "DATE"),
    ("user_message_count", "INT64"), ("assistant_message_count", "INT64"), ("followup_count", "INT64"),
    ("active_days", "INT64"), ("primary_mode", "STRING"), ("status", "STRING"),
    ("source_event_ts", "TIMESTAMP"), ("materialized_at", "TIMESTAMP"),
]
CITATION_SCHEMA = [
    ("event_id", "STRING"), ("answer_event_id", "STRING"), ("answer_ts", "TIMESTAMP"), ("answer_date", "DATE"),
    ("user_id", "STRING"), ("roster_id", "STRING"), ("message_id", "STRING"), ("citation_order", "INT64"),
    ("source_type", "STRING"), ("source_system", "STRING"), ("document_key", "STRING"), ("display_title", "STRING"),
    ("page_number", "INT64"), ("access_status", "STRING"), ("trust_tier", "STRING"),
    ("primary_product_key", "STRING"), ("source_event_ts", "TIMESTAMP"), ("materialized_at", "TIMESTAMP"),
]


def struct_array_parameter(
    name: str,
    schema: list[tuple[str, str]],
    rows: list[dict[str, Any]],
) -> bigquery.ArrayQueryParameter:
    row_type = bigquery.StructQueryParameterType(
        *(bigquery.ScalarQueryParameterType(kind, name=field) for field, kind in schema)
    )
    values = [
        bigquery.StructQueryParameter(
            None,
            *(bigquery.ScalarQueryParameter(field, kind, row.get(field)) for field, kind in schema),
        )
        for row in rows
    ]
    return bigquery.ArrayQueryParameter(name, row_type, values)


class FirestoreProjector:
    """Single bounded projector for roster identity, conversations, and citations."""

    def __init__(
        self,
        settings: Settings,
        *,
        firestore_client: Any | None = None,
        directory: UserDirectoryRepository | None = None,
    ) -> None:
        database = str(settings.monitor_firestore_database or "(default)").strip() or "(default)"
        self._settings = settings
        self._firestore = firestore_client or firestore.Client(project=settings.monitor_project_id, database=database)
        self._directory = directory or UserDirectoryRepository(settings, client=self._firestore)
        self._manager = UserManagementService(
            directory=self._directory,
            audit_retention_days=settings.monitor_admin_change_retention_days,
        )

    def resolve_chat_identities(self) -> int:
        users = {normalize_email(item["email"]): item for item in self._directory.list_users(include_inactive=True)}
        matched = 0
        roots = self._firestore.collection(self._settings.monitor_firestore_chat_collection)
        for document in roots.stream():
            payload = document.to_dict() or {}
            if payload.get("identityVerified") is not True:
                continue
            try:
                email = normalize_email(str(payload.get("userEmail") or ""))
            except ValueError:
                continue
            user = users.get(email)
            subject = str(payload.get("subject") or "").strip()
            if user is None or not subject:
                continue
            if user.get("chat_user_id") == document.id and user.get("user_id") == subject:
                matched += 1
                continue
            self._manager.bind_chat_identity(user["roster_id"], chat_user_id=document.id, user_id=subject)
            matched += 1
        return matched

    def user_scope_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for user in self._directory.list_users(include_inactive=True):
            # Scope flags describe the employee's governed analysis cohort.  The
            # current active flag is kept separately so disabling a user removes
            # them from current denominators without making already-produced
            # events impossible to materialize or rebuild.
            membership = membership_for(
                Department(str(user["department"])),
                is_active=True,
            )
            rows.append({
                "roster_id": user["roster_id"], "user_id": str(user.get("user_id") or "").strip() or None,
                "area": user.get("area"), "area_key": user.get("area_key"), "workplace": user.get("workplace"),
                "role": user.get("role"), "department": user.get("department"), "mr_experience": user.get("mr_experience"),
                "is_active": bool(user.get("is_active")), "global_scope_enabled": membership.global_enabled,
                "user_map_scope_enabled": membership.user_map_enabled,
                "is_admin": str(user.get("department")) == "管理者",
                "updated_at": user.get("updated_at") or datetime.now(timezone.utc),
            })
        return rows

    def changed_conversation_rows(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        conversation_rows: list[dict[str, Any]] = []
        citation_rows: list[dict[str, Any]] = []
        for user in self._directory.list_users(include_inactive=False):
            chat_user_id = str(user.get("chat_user_id") or "").strip()
            user_id = str(user.get("user_id") or "").strip()
            if not chat_user_id or not user_id:
                continue
            query = (
                self._firestore.collection(self._settings.monitor_firestore_chat_collection)
                .document(chat_user_id).collection("conversations")
                .where(filter=FieldFilter("updatedAt", ">=", _firestore_iso(window_start)))
                .where(filter=FieldFilter("updatedAt", "<", _firestore_iso(window_end)))
            )
            for conversation_doc in query.stream():
                conversation = conversation_doc.to_dict() or {}
                message_docs = list(conversation_doc.reference.collection("messages").order_by("timestamp").stream())
                messages = [{"id": item.id, **(item.to_dict() or {})} for item in message_docs]
                conversation_rows.append(project_conversation(
                    roster_id=user["roster_id"], user_id=user_id, conversation_id=conversation_doc.id,
                    conversation=conversation, messages=messages,
                ))
                citation_rows.extend(project_citations(
                    roster_id=user["roster_id"], user_id=user_id, conversation_id=conversation_doc.id,
                    messages=messages,
                ))
        return conversation_rows, citation_rows
