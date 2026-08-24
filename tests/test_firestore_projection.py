from datetime import datetime, timezone
from types import SimpleNamespace

from app.jobs.project_firestore import (
    CONVERSATION_SCHEMA,
    FirestoreProjector,
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


class _RootDocument:
    def __init__(self, document_id: str, payload: dict) -> None:
        self.id = document_id
        self._payload = payload

    def to_dict(self) -> dict:
        return dict(self._payload)


class _RootCollection:
    def __init__(self, documents: list[_RootDocument]) -> None:
        self._documents = documents

    def stream(self) -> list[_RootDocument]:
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
    )

    parameter = struct_array_parameter("conversation_rows", CONVERSATION_SCHEMA, [row])
    assert parameter.name == "conversation_rows"
    assert len(parameter.values) == 1


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
    projector._settings = SimpleNamespace(monitor_firestore_chat_collection="chat_users")
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
