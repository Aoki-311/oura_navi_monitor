from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from google.cloud import bigquery, firestore
from google.api_core.exceptions import (
    DeadlineExceeded,
    InternalServerError,
    ServiceUnavailable,
    TooManyRequests,
)
from google.api_core.retry import Retry
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.field_path import FieldPath

from app.contracts.admin import normalize_email
from app.domain.analytics_roster_projection import (
    UserScopeProjection,
    project_user_scope,
)
from app.domain.roster_records import read_canonical_roster_collection
from app.repositories.user_directory import UserDirectoryRepository
from app.services.user_management import UserManagementService
from app.settings import Settings


LOGGER = logging.getLogger(__name__)


class ProjectionDataError(ValueError):
    """A source document cannot be projected without inventing analytics data."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProjectionReadError(RuntimeError):
    """A bounded canonical Firestore read could not be completed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ChatRootRecord:
    root_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ChatConversationRecord:
    root_id: str
    conversation_id: str
    conversation: dict[str, Any]
    messages: list[dict[str, Any]]


@dataclass
class FullChatSnapshot:
    roots: list[ChatRootRecord] = field(default_factory=list)
    conversations: list[ChatConversationRecord] = field(default_factory=list)
    issues: Counter[str] = field(default_factory=Counter)


ProgressCallback = Callable[[str, int], None]
NO_INTERNAL_RETRY = Retry(predicate=lambda _exc: False, deadline=0)


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


def _local_date(value: datetime, *, timezone_name: str):
    return value.astimezone(ZoneInfo(timezone_name)).date()


def _stable_id(*parts: str) -> str:
    seed = "|".join(str(part or "").strip() for part in parts)
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def project_conversation(
    *, roster_id: str, user_id: str, conversation_id: str,
    conversation: dict[str, Any], messages: list[dict[str, Any]],
    timezone_name: str,
) -> dict[str, Any]:
    user_messages = [item for item in messages if str(item.get("role") or "").strip().lower() == "user"]
    assistant_messages = [item for item in messages if str(item.get("role") or "").strip().lower() == "assistant"]
    timestamps = [value for item in messages if (value := _timestamp(item.get("timestamp") or item.get("updatedAt"))) is not None]
    modes = Counter(str(item.get("modeAtSend") or "").strip().lower() for item in messages if str(item.get("modeAtSend") or "").strip())
    conversation_updated_at = _timestamp(conversation.get("updatedAt"))
    if not timestamps and conversation_updated_at is None:
        raise ProjectionDataError("missing_conversation_timestamp")
    first_active_at = min(timestamps) if timestamps else conversation_updated_at
    last_active_at = max(timestamps) if timestamps else conversation_updated_at
    if first_active_at is None or last_active_at is None:
        raise ProjectionDataError("missing_conversation_timestamp")
    return {
        "event_id": f"conversation:{_stable_id(roster_id, conversation_id)}",
        "conversation_id": conversation_id,
        "user_id": user_id,
        "roster_id": roster_id,
        "first_active_at": first_active_at,
        "last_active_at": last_active_at,
        "updated_date": _local_date(last_active_at, timezone_name=timezone_name),
        "user_message_count": len(user_messages),
        "assistant_message_count": len(assistant_messages),
        "followup_count": max(0, len(user_messages) - 1),
        "active_days": len({_local_date(value, timezone_name=timezone_name) for value in timestamps}),
        "primary_mode": modes.most_common(1)[0][0] if modes else str(conversation.get("mode") or ""),
        "status": str(conversation.get("visibility") or "active"),
        "source_event_ts": last_active_at,
        "materialized_at": datetime.now(timezone.utc),
    }


def project_citations(
    *, roster_id: str, user_id: str, conversation_id: str,
    messages: Iterable[dict[str, Any]], timezone_name: str,
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
                    "answer_date": _local_date(answer_ts, timezone_name=timezone_name),
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
    ("snapshot_run_id", "STRING"), ("snapshot_created_at", "TIMESTAMP"),
    ("roster_id", "STRING"), ("user_id", "STRING"), ("name", "STRING"), ("email", "STRING"),
    ("area", "STRING"), ("area_key", "STRING"),
    ("workplace", "STRING"), ("role", "STRING"), ("department", "STRING"), ("mr_experience", "STRING"),
    ("label_ids_json", "STRING"), ("labels_json", "STRING"),
    ("is_active", "BOOL"), ("global_scope_enabled", "BOOL"), ("user_map_scope_enabled", "BOOL"),
    ("is_admin", "BOOL"), ("updated_at", "TIMESTAMP"),
    ("roster_isolated_count", "INT64"), ("roster_issue_counts_json", "STRING"),
    ("roster_diagnostic_fingerprint", "STRING"),
    ("global_label_catalog_status", "STRING"), ("global_label_catalog_issues_json", "STRING"),
    ("user_map_label_catalog_status", "STRING"), ("user_map_label_catalog_issues_json", "STRING"),
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


def _conversation_owner(path: str, *, root_collection: str) -> tuple[str, str] | None:
    parts = str(path or "").split("/")
    if (
        len(parts) == 4
        and parts[0] == root_collection
        and parts[2] == "conversations"
        and all(parts)
    ):
        return parts[1], parts[3]
    return None


def _message_owner(path: str, *, root_collection: str) -> tuple[str, str] | None:
    parts = str(path or "").split("/")
    if (
        len(parts) == 6
        and parts[0] == root_collection
        and parts[2] == "conversations"
        and parts[4] == "messages"
        and all(parts)
    ):
        return parts[1], parts[3]
    return None


class FirestoreChatReader:
    """The only reader for a complete persisted chat snapshot.

    A full rebuild uses one root stream, one conversations collection-group
    stream, and one messages collection-group stream.  It never performs one
    messages RPC per conversation.
    """

    def __init__(
        self,
        client: Any,
        *,
        root_collection: str,
        read_timeout_seconds: int,
        read_page_size: int = 1000,
    ) -> None:
        self._client = client
        self._root_collection = str(root_collection or "").strip()
        self._read_timeout_seconds = max(1, int(read_timeout_seconds))
        self._read_page_size = max(1, int(read_page_size))

    def _paged_collection_group(
        self,
        name: str,
        *,
        progress: ProgressCallback | None,
    ):
        cursor = None
        total = 0
        empty_failures = 0
        while True:
            query = (
                self._client.collection_group(name)
                .order_by(FieldPath.document_id())
                .limit(self._read_page_size)
            )
            if cursor is not None:
                query = query.start_after(cursor)
            page_count = 0
            interrupted = None
            try:
                for document in query.stream(
                    retry=NO_INTERNAL_RETRY,
                    timeout=self._read_timeout_seconds,
                ):
                    page_count += 1
                    total += 1
                    cursor = document
                    yield document
                    if progress is not None and total % 250 == 0:
                        progress(f"firestore_{name}_read", total)
            except (
                DeadlineExceeded,
                InternalServerError,
                ServiceUnavailable,
                TooManyRequests,
            ) as exc:
                interrupted = exc

            if page_count > 0:
                empty_failures = 0
                if progress is not None and total % 250:
                    progress(f"firestore_{name}_read", total)
                if interrupted is not None:
                    if progress is not None:
                        progress(f"firestore_{name}_resume", total)
                    continue
            elif interrupted is not None:
                empty_failures += 1
                if progress is not None:
                    progress(f"firestore_{name}_retry", empty_failures)
                if empty_failures >= 3:
                    raise ProjectionReadError(
                        f"firestore_{name}_read_failed"
                    ) from interrupted
                time.sleep(empty_failures)
                continue

            if page_count == 0 or page_count < self._read_page_size:
                return

    def full_snapshot(
        self,
        *,
        progress: ProgressCallback | None = None,
    ) -> FullChatSnapshot:
        snapshot = FullChatSnapshot()

        for document in self._client.collection(self._root_collection).stream(
            retry=NO_INTERNAL_RETRY,
            timeout=self._read_timeout_seconds,
        ):
            snapshot.roots.append(
                ChatRootRecord(root_id=document.id, payload=document.to_dict() or {})
            )
        if progress is not None:
            progress("firestore_roots_read", len(snapshot.roots))

        conversations: dict[tuple[str, str], dict[str, Any]] = {}
        for document in self._paged_collection_group(
            "conversations", progress=progress
        ):
            owner = _conversation_owner(
                document.reference.path, root_collection=self._root_collection
            )
            if owner is None:
                snapshot.issues["unexpected_conversation_path"] += 1
                continue
            if owner in conversations:
                snapshot.issues["duplicate_conversation_path"] += 1
                continue
            conversations[owner] = document.to_dict() or {}
        if progress is not None:
            progress("firestore_conversations_read", len(conversations))

        messages: dict[tuple[str, str], list[dict[str, Any]]] = {}
        message_count = 0
        for document in self._paged_collection_group("messages", progress=progress):
            owner = _message_owner(
                document.reference.path, root_collection=self._root_collection
            )
            if owner is None:
                snapshot.issues["unexpected_message_path"] += 1
                continue
            payload = document.to_dict() or {}
            messages.setdefault(owner, []).append({**payload, "id": document.id})
            message_count += 1

        for owner, conversation in conversations.items():
            conversation_messages = messages.pop(owner, [])
            snapshot.conversations.append(
                ChatConversationRecord(
                    root_id=owner[0],
                    conversation_id=owner[1],
                    conversation=conversation,
                    messages=conversation_messages,
                )
            )
        snapshot.issues["orphan_message_conversation"] += sum(
            len(items) for items in messages.values()
        )
        if snapshot.issues["orphan_message_conversation"] == 0:
            del snapshot.issues["orphan_message_conversation"]
        return snapshot


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
        self._chat_reader = FirestoreChatReader(
            self._firestore,
            root_collection=settings.monitor_firestore_chat_collection,
            read_timeout_seconds=settings.monitor_firestore_read_timeout_seconds,
            read_page_size=settings.monitor_firestore_read_page_size,
        )
        self._directory = directory or UserDirectoryRepository(settings, client=self._firestore)
        self._manager = UserManagementService(
            directory=self._directory,
            audit_retention_days=settings.monitor_admin_change_retention_days,
        )

    def resolve_chat_identities(self) -> int:
        records = read_canonical_roster_collection(
            self._directory.list_users(include_inactive=True)
        )
        projection_time = datetime.now(timezone.utc)
        for record in records.analytics_records:
            if not record.value.get("updated_at"):
                record.value["updated_at"] = projection_time
        issue_counts: Counter[str] = Counter()
        users: dict[str, dict[str, Any]] = {}
        for record in records:
            if not record.identity_eligible:
                issue_counts.update(record.issues)
                continue
            users[record.value["email"]] = record.value
        if issue_counts:
            LOGGER.warning(
                "identity roster rows isolated: %s",
                dict(sorted(issue_counts.items())),
            )
        matched = 0
        roots = self._firestore.collection(self._settings.monitor_firestore_chat_collection)
        for document in roots.stream(
            retry=NO_INTERNAL_RETRY,
            timeout=self._settings.monitor_firestore_read_timeout_seconds,
        ):
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

    def user_scope_rows(self) -> UserScopeProjection:
        return project_user_scope(self._directory)

    def changed_conversation_rows(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
        conversation_rows: list[dict[str, Any]] = []
        citation_rows: list[dict[str, Any]] = []
        issues: Counter[str] = Counter()
        # Read the full collection before filtering active rows so an inactive
        # duplicate identity cannot make the active row look unambiguous.  This
        # is the same collection-level authority used by scope projection,
        # identity binding, trace, and historical rebuild.
        records = read_canonical_roster_collection(
            self._directory.list_users(include_inactive=True)
        )
        for record in records:
            if record.value.get("is_active") is not True:
                continue
            if not record.identity_eligible or not record.projection_eligible:
                for issue in record.issues:
                    issues[f"roster_{issue}"] += 1
                continue
            user = record.value
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
            for conversation_doc in query.stream(
                retry=NO_INTERNAL_RETRY,
                timeout=self._settings.monitor_firestore_read_timeout_seconds
            ):
                conversation = conversation_doc.to_dict() or {}
                message_docs = list(
                    conversation_doc.reference.collection("messages")
                    .order_by("timestamp")
                    .stream(
                        retry=NO_INTERNAL_RETRY,
                        timeout=self._settings.monitor_firestore_read_timeout_seconds,
                    )
                )
                messages = [{**(item.to_dict() or {}), "id": item.id} for item in message_docs]
                try:
                    conversation_rows.append(project_conversation(
                        roster_id=user["roster_id"], user_id=user_id, conversation_id=conversation_doc.id,
                        conversation=conversation, messages=messages,
                        timezone_name=self._settings.monitor_timezone,
                    ))
                except ProjectionDataError as exc:
                    issues[exc.code] += 1
                    continue
                citation_rows.extend(project_citations(
                    roster_id=user["roster_id"], user_id=user_id, conversation_id=conversation_doc.id,
                    messages=messages, timezone_name=self._settings.monitor_timezone,
                ))
        return conversation_rows, citation_rows, dict(sorted(issues.items()))
