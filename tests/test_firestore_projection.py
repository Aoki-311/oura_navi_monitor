from datetime import datetime, timezone

from app.jobs.project_firestore import (
    CONVERSATION_SCHEMA,
    FirestoreProjector,
    project_citations,
    project_conversation,
    struct_array_parameter,
)
from app.settings import Settings


class _Directory:
    def __init__(self, users: list[dict]) -> None:
        self._users = users

    def list_users(self, *, include_inactive: bool = True) -> list[dict]:
        assert include_inactive is True
        return self._users


def test_conversation_projection_counts_followups_without_question_text() -> None:
    messages = [
        {"role": "user", "timestamp": "2026-08-22T01:00:00Z", "content": "秘密"},
        {"role": "assistant", "timestamp": "2026-08-22T01:01:00Z", "modeAtSend": "internal"},
        {"role": "user", "timestamp": "2026-08-23T01:00:00Z", "content": "秘密2"},
    ]
    row = project_conversation(
        roster_id="roster_1",
        user_key="user_1",
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
        user_key="user_1",
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
        user_key="user_1",
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
        "user_key": "user_1",
        "name": "停止済み利用者",
        "email": "inactive@example.com",
        "area": "関西",
        "area_key": "関西",
        "workplace": "大阪",
        "role": "本社MR",
        "department": "DM専任",
        "mr_experience": "8年",
        "is_active": False,
        "identity_keys": [
            {
                "user_key": "user_1",
                "valid_from": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "valid_to": None,
            }
        ],
        "updated_at": datetime(2026, 8, 24, tzinfo=timezone.utc),
    }
    projector = FirestoreProjector.__new__(FirestoreProjector)
    projector._directory = _Directory([user])

    row = projector.user_scope_rows()[0]

    assert row["is_active"] is False
    assert row["global_scope_enabled"] is True
    assert row["user_map_scope_enabled"] is True
