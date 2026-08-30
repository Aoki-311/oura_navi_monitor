from datetime import datetime, timezone
from types import SimpleNamespace

from google.api_core.exceptions import DeadlineExceeded

from app.jobs.project_firestore import (
    CONVERSATION_SCHEMA,
    FirestoreChatReader,
    FirestoreProjector,
    ProjectionDataError,
    project_citations,
    project_conversation,
    struct_array_parameter,
)
from app.settings import Settings
from app.services.user_management import UserManagementService


class _Directory:
    def __init__(self, users: list[dict]) -> None:
        self._users = users

    def list_users(self, *, include_inactive: bool = True) -> list[dict]:
        assert include_inactive is True
        return self._users

    def get_user(self, roster_id: str) -> dict | None:
        return next((item for item in self._users if item["roster_id"] == roster_id), None)

    def put_user(self, user: dict) -> dict:
        for index, item in enumerate(self._users):
            if item["roster_id"] == user["roster_id"]:
                self._users[index] = dict(user)
                return dict(user)
        raise AssertionError("unknown roster user")

    def bind_user_identity(
        self,
        roster_id: str,
        *,
        chat_user_id: str,
        user_id: str,
        bound_at,
        change: dict,
    ) -> dict:
        del change
        current = self.get_user(roster_id)
        if current is None:
            raise ValueError("user not found")
        current.update({
            "chat_user_id": chat_user_id,
            "user_id": user_id,
            "identity_bound_at": bound_at,
        })
        return dict(current)


class _RootDocument:
    def __init__(self, document_id: str, payload: dict) -> None:
        self.id = document_id
        self._payload = payload

    def to_dict(self) -> dict:
        return dict(self._payload)


class _RootCollection:
    def __init__(self, documents: list[_RootDocument]) -> None:
        self._documents = documents

    def stream(self, **_kwargs) -> list[_RootDocument]:
        return self._documents


class _FirestoreRoots:
    def __init__(self, documents: list[_RootDocument]) -> None:
        self._documents = documents

    def collection(self, name: str) -> _RootCollection:
        assert name == "chat_users"
        return _RootCollection(self._documents)


def test_conversation_projection_counts_followups_without_question_text() -> None:
    messages = [
        {"role": "user", "timestamp": "2026-08-22T01:00:00Z", "content": "秘密"},
        {"role": "assistant", "timestamp": "2026-08-22T01:01:00Z", "modeAtSend": "internal"},
        {"role": "user", "timestamp": "2026-08-23T01:00:00Z", "content": "秘密2"},
    ]
    row = project_conversation(
        roster_id="roster_1",
        user_id="subject_1",
        conversation_id="conv_1",
        conversation={"updatedAt": "2026-08-23T01:00:00Z", "visibility": "active"},
        messages=messages,
        timezone_name="Asia/Tokyo",
    )
    assert row["user_message_count"] == 2
    assert row["followup_count"] == 1
    assert row["active_days"] == 2
    assert "content" not in row


def test_citation_projection_reads_one_canonical_persisted_field_and_no_url() -> None:
    rows = project_citations(
        roster_id="roster_1",
        user_id="subject_1",
        conversation_id="conv_1",
        timezone_name="Asia/Tokyo",
        messages=[
            {
                "id": "message_1",
                "role": "assistant",
                "timestamp": datetime(2026, 8, 23, tzinfo=timezone.utc),
                "grounded": {
                    "requestId": "request_1",
                    "citationIndex": [{"citation_id": "ignored_second_owner"}],
                    "citations": [
                        {
                            "citation_id": "doc_1",
                            "display_title": "取扱説明書",
                            "source_type": "sharepoint",
                            "open_url": "https://example.invalid/secret?token=x",
                            "page": 3,
                            "access_status": "openable",
                        }
                    ],
                },
            }
        ],
    )
    assert len(rows) == 1
    assert rows[0]["document_key"] == "doc_1"
    assert rows[0]["answer_event_id"] == "answer:request_1"
    assert not any("url" in key.lower() for key in rows[0])


def test_firestore_projection_rows_are_typed_for_the_atomic_publisher() -> None:
    row = project_conversation(
        roster_id="roster_1",
        user_id="subject_1",
        conversation_id="conv_1",
        conversation={"updatedAt": "2026-08-24T01:00:00Z"},
        messages=[],
        timezone_name="Asia/Tokyo",
    )

    parameter = struct_array_parameter("conversation_rows", CONVERSATION_SCHEMA, [row])
    assert parameter.name == "conversation_rows"
    assert len(parameter.values) == 1


def test_conversation_projection_refuses_to_fabricate_activity_timestamp() -> None:
    try:
        project_conversation(
            roster_id="roster_1",
            user_id="subject_1",
            conversation_id="conv_1",
            conversation={},
            messages=[{"role": "user", "timestamp": "not-a-timestamp"}],
            timezone_name="Asia/Tokyo",
        )
    except ProjectionDataError as exc:
        assert exc.code == "missing_conversation_timestamp"
    else:
        raise AssertionError("missing source timestamps must not become processing time")


def test_firestore_projection_partitions_and_active_days_use_monitor_timezone() -> None:
    row = project_conversation(
        roster_id="roster_1",
        user_id="subject_1",
        conversation_id="conv_1",
        conversation={"updatedAt": "2026-08-20T15:30:00Z"},
        messages=[
            {"role": "user", "timestamp": "2026-08-20T14:59:00Z"},
            {"role": "assistant", "timestamp": "2026-08-20T15:01:00Z"},
        ],
        timezone_name="Asia/Tokyo",
    )

    assert row["updated_date"].isoformat() == "2026-08-21"
    assert row["active_days"] == 2


def test_inactive_user_keeps_structural_scope_for_historical_fact_rebuilds() -> None:
    user = {
        "roster_id": "roster_1",
        "user_id": "subject_1",
        "name": "停止済み利用者",
        "email": "inactive@example.com",
        "area": "関西",
        "area_key": "関西",
        "workplace": "大阪",
        "role": "本社MR",
        "department": "DM専任",
        "mr_experience": "8年",
        "is_active": False,
        "updated_at": datetime(2026, 8, 24, tzinfo=timezone.utc),
    }
    projector = FirestoreProjector.__new__(FirestoreProjector)
    projector._directory = _Directory([user])

    row = projector.user_scope_rows()[0]

    assert row["is_active"] is False
    assert row["user_id"] == "subject_1"
    assert row["global_scope_enabled"] is True
    assert row["user_map_scope_enabled"] is True
    assert "valid_from" not in row
    assert "valid_to" not in row


def test_unmatched_roster_user_remains_in_scope_with_no_event_identity() -> None:
    user = {
        "roster_id": "roster_2",
        "user_id": "",
        "name": "未利用者",
        "email": "unused@example.com",
        "area": "関西",
        "area_key": "関西",
        "workplace": "大阪",
        "role": "本社MR",
        "department": "DM専任",
        "mr_experience": "8年",
        "is_active": True,
        "updated_at": datetime(2026, 8, 24, tzinfo=timezone.utc),
    }
    projector = FirestoreProjector.__new__(FirestoreProjector)
    projector._directory = _Directory([user])

    row = projector.user_scope_rows()[0]

    assert row["roster_id"] == "roster_2"
    assert row["user_id"] is None


def test_only_verified_firestore_root_binds_subject_to_roster_email() -> None:
    users = [
        {
            "roster_id": "roster_1",
            "email": "matched@example.com",
            "user_id": "",
            "chat_user_id": "",
            "is_active": True,
        },
        {
            "roster_id": "roster_2",
            "email": "unverified@example.com",
            "user_id": "",
            "chat_user_id": "",
            "is_active": True,
        },
    ]
    directory = _Directory(users)
    projector = FirestoreProjector.__new__(FirestoreProjector)
    projector._settings = SimpleNamespace(
        monitor_firestore_chat_collection="chat_users",
        monitor_firestore_read_timeout_seconds=120,
    )
    projector._firestore = _FirestoreRoots(
        [
            _RootDocument(
                "chat-subject-1",
                {
                    "identityVerified": True,
                    "userEmail": "MATCHED@example.com",
                    "subject": "subject-1",
                },
            ),
            _RootDocument(
                "chat-subject-2",
                {
                    "identityVerified": False,
                    "userEmail": "unverified@example.com",
                    "subject": "subject-2",
                },
            ),
        ]
    )
    projector._directory = directory
    projector._manager = UserManagementService(directory=directory)

    assert projector.resolve_chat_identities() == 1
    assert users[0]["user_id"] == "subject-1"
    assert users[0]["chat_user_id"] == "chat-subject-1"
    assert users[1]["user_id"] == ""


def test_identity_resolution_isolates_invalid_and_duplicate_roster_rows() -> None:
    valid = {
        "roster_id": "roster_valid",
        "email": "valid@example.com",
        "user_id": "",
        "chat_user_id": "",
        "is_active": True,
    }
    users = [
        valid,
        {
            "roster_id": "roster_invalid",
            "email": "invalid-email",
            "user_id": "",
            "chat_user_id": "",
            "is_active": True,
        },
        {
            "roster_id": "roster_duplicate_a",
            "email": "duplicate@example.com",
            "user_id": "",
            "chat_user_id": "",
            "is_active": True,
        },
        {
            "roster_id": "roster_duplicate_b",
            "email": "DUPLICATE@example.com",
            "user_id": "",
            "chat_user_id": "",
            "is_active": True,
        },
    ]
    directory = _Directory(users)
    projector = FirestoreProjector.__new__(FirestoreProjector)
    projector._settings = SimpleNamespace(
        monitor_firestore_chat_collection="chat_users",
        monitor_firestore_read_timeout_seconds=120,
    )
    projector._firestore = _FirestoreRoots(
        [
            _RootDocument(
                "chat-valid",
                {
                    "identityVerified": True,
                    "userEmail": "valid@example.com",
                    "subject": "subject-valid",
                },
            ),
            _RootDocument(
                "chat-duplicate",
                {
                    "identityVerified": True,
                    "userEmail": "duplicate@example.com",
                    "subject": "subject-duplicate",
                },
            ),
        ]
    )
    projector._directory = directory
    projector._manager = UserManagementService(directory=directory)

    assert projector.resolve_chat_identities() == 1
    assert valid["chat_user_id"] == "chat-valid"
    assert users[2]["chat_user_id"] == ""
    assert users[3]["chat_user_id"] == ""


def test_scope_projection_skips_one_structurally_invalid_row_without_losing_valid_rows() -> None:
    valid = {
        "roster_id": "roster_valid",
        "user_id": "subject_valid",
        "name": "有効利用者",
        "email": "valid@example.com",
        "area": "関西",
        "area_key": "関西",
        "workplace": "大阪",
        "role": "本社MR",
        "department": "DM専任",
        "mr_experience": "8年",
        "is_active": True,
    }
    invalid = {
        **valid,
        "roster_id": "roster_invalid",
        "user_id": "subject_invalid",
        "email": "invalid@example.com",
        "area_key": "",
    }
    projector = FirestoreProjector.__new__(FirestoreProjector)
    projector._directory = _Directory([invalid, valid])

    rows = projector.user_scope_rows()

    assert [row["roster_id"] for row in rows] == ["roster_valid"]


def test_scope_projection_isolates_normalized_duplicate_email_and_identity_rows() -> None:
    base = {
        "user_id": "",
        "chat_user_id": "",
        "name": "利用者",
        "area": "関西",
        "area_key": "関西",
        "workplace": "大阪",
        "role": "本社MR",
        "department": "DM専任",
        "mr_experience": "8年",
        "is_active": True,
    }
    users = [
        {**base, "roster_id": "email_a", "email": " Same@Example.com "},
        {**base, "roster_id": "email_b", "email": "same@example.COM"},
        {
            **base,
            "roster_id": "identity_a",
            "email": "identity-a@example.com",
            "user_id": "shared-subject",
        },
        {
            **base,
            "roster_id": "identity_b",
            "email": "identity-b@example.com",
            "user_id": "shared-subject",
        },
        {**base, "roster_id": "safe", "email": "safe@example.com"},
    ]
    projector = FirestoreProjector.__new__(FirestoreProjector)
    projector._directory = _Directory(users)

    rows = projector.user_scope_rows()

    assert [row["roster_id"] for row in rows] == ["safe"]


def test_incremental_conversation_projection_isolates_collection_identity_conflicts() -> None:
    base = {
        "user_id": "shared-subject",
        "chat_user_id": "shared-chat-root",
        "name": "利用者",
        "area": "関西",
        "area_key": "関西",
        "workplace": "大阪",
        "role": "本社MR",
        "department": "DM専任",
        "mr_experience": "8年",
        "is_active": True,
    }
    users = [
        {**base, "roster_id": "active", "email": "active@example.com"},
        {
            **base,
            "roster_id": "inactive_duplicate",
            "email": "inactive@example.com",
            "is_active": False,
        },
    ]
    projector = FirestoreProjector.__new__(FirestoreProjector)
    projector._directory = _Directory(users)

    class _NoFirestoreRead:
        def collection(self, _name):
            raise AssertionError("ambiguous identities must not reach Firestore")

    projector._firestore = _NoFirestoreRead()
    projector._settings = Settings()

    conversations, citations, issues = projector.changed_conversation_rows(
        window_start=datetime(2026, 8, 23, tzinfo=timezone.utc),
        window_end=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert conversations == []
    assert citations == []
    assert issues == {"roster_duplicate_identity": 1}


class _Reference:
    def __init__(self, path: str) -> None:
        self.path = path


class _GroupDocument(_RootDocument):
    def __init__(self, document_id: str, path: str, payload: dict) -> None:
        super().__init__(document_id, payload)
        self.reference = _Reference(path)


class _GroupQuery:
    def __init__(
        self,
        documents: list[_GroupDocument],
        *,
        offset: int = 0,
        limit_value: int | None = None,
    ) -> None:
        self._documents = documents
        self._offset = offset
        self._limit = limit_value

    def order_by(self, _field_path) -> "_GroupQuery":
        return self

    def limit(self, value: int) -> "_GroupQuery":
        return _GroupQuery(
            self._documents,
            offset=self._offset,
            limit_value=value,
        )

    def start_after(self, document: _GroupDocument) -> "_GroupQuery":
        offset = self._documents.index(document) + 1
        return _GroupQuery(
            self._documents,
            offset=offset,
            limit_value=self._limit,
        )

    def stream(self, **_kwargs) -> list[_GroupDocument]:
        end = (
            self._offset + self._limit
            if self._limit is not None
            else len(self._documents)
        )
        return self._documents[self._offset:end]


class _FullSnapshotFirestore(_FirestoreRoots):
    def __init__(
        self,
        roots: list[_RootDocument],
        conversations: list[_GroupDocument],
        messages: list[_GroupDocument],
    ) -> None:
        super().__init__(roots)
        self._groups = {"conversations": conversations, "messages": messages}
        self.collection_group_calls: list[str] = []

    def collection_group(self, name: str) -> _GroupQuery:
        self.collection_group_calls.append(name)
        return _GroupQuery(self._groups[name])


def test_full_chat_reader_uses_two_collection_group_streams_not_per_conversation_reads() -> None:
    client = _FullSnapshotFirestore(
        [_RootDocument("root_1", {"identityVerified": True})],
        [
            _GroupDocument(
                "conv_1",
                "chat_users/root_1/conversations/conv_1",
                {"updatedAt": "2026-08-24T00:00:00Z"},
            )
        ],
        [
            _GroupDocument(
                "message_doc_id",
                "chat_users/root_1/conversations/conv_1/messages/message_doc_id",
                {"id": "untrusted_payload_id", "role": "user"},
            )
        ],
    )
    reader = FirestoreChatReader(
        client,
        root_collection="chat_users",
        read_timeout_seconds=120,
        read_page_size=250,
    )

    snapshot = reader.full_snapshot()

    assert client.collection_group_calls == ["conversations", "messages"]
    assert len(snapshot.conversations) == 1
    assert snapshot.conversations[0].messages[0]["id"] == "message_doc_id"
    assert snapshot.issues == {}


def test_full_chat_reader_pages_by_document_cursor_without_losing_rows() -> None:
    client = _FullSnapshotFirestore(
        [_RootDocument("root_1", {"identityVerified": True})],
        [
            _GroupDocument(
                f"conv_{index}",
                f"chat_users/root_1/conversations/conv_{index}",
                {"updatedAt": "2026-08-24T00:00:00Z"},
            )
            for index in range(3)
        ],
        [
            _GroupDocument(
                f"message_{index}",
                f"chat_users/root_1/conversations/conv_{index}/messages/message_{index}",
                {"role": "user"},
            )
            for index in range(3)
        ],
    )
    reader = FirestoreChatReader(
        client,
        root_collection="chat_users",
        read_timeout_seconds=120,
        read_page_size=1,
    )

    snapshot = reader.full_snapshot()

    assert len(snapshot.conversations) == 3
    assert sum(len(item.messages) for item in snapshot.conversations) == 3
    # One final empty page proves the cursor reached the end instead of silently
    # treating a full page as a complete collection.
    assert client.collection_group_calls.count("conversations") == 4
    assert client.collection_group_calls.count("messages") == 4


class _InterruptedGroupQuery(_GroupQuery):
    def __init__(self, documents, state, **kwargs) -> None:
        super().__init__(documents, **kwargs)
        self._state = state

    def order_by(self, _field_path) -> "_InterruptedGroupQuery":
        return self

    def limit(self, value: int) -> "_InterruptedGroupQuery":
        return _InterruptedGroupQuery(
            self._documents,
            self._state,
            offset=self._offset,
            limit_value=value,
        )

    def start_after(self, document: _GroupDocument) -> "_InterruptedGroupQuery":
        return _InterruptedGroupQuery(
            self._documents,
            self._state,
            offset=self._documents.index(document) + 1,
            limit_value=self._limit,
        )

    def stream(self, **_kwargs):
        rows = super().stream()
        if not self._state["interrupted"] and rows:
            self._state["interrupted"] = True
            yield rows[0]
            raise DeadlineExceeded("simulated page deadline")
        yield from rows


class _InterruptedClient:
    def __init__(self, documents: list[_GroupDocument]) -> None:
        self._documents = documents
        self.state = {"interrupted": False}

    def collection_group(self, name: str) -> _InterruptedGroupQuery:
        assert name == "messages"
        return _InterruptedGroupQuery(self._documents, self.state)


def test_paged_reader_resumes_after_confirmed_prefix_when_rpc_deadline_interrupts() -> None:
    documents = [
        _GroupDocument(
            f"message_{index}",
            f"chat_users/root_1/conversations/conv_1/messages/message_{index}",
            {"role": "user"},
        )
        for index in range(3)
    ]
    client = _InterruptedClient(documents)
    reader = FirestoreChatReader(
        client,
        root_collection="chat_users",
        read_timeout_seconds=1,
        read_page_size=10,
    )
    progress = []

    rows = list(
        reader._paged_collection_group(
            "messages",
            progress=lambda stage, count: progress.append((stage, count)),
        )
    )

    assert [row.id for row in rows] == ["message_0", "message_1", "message_2"]
    assert ("firestore_messages_resume", 1) in progress
