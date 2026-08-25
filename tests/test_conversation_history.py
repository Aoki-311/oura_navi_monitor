from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.repositories.conversation_history import ConversationHistoryRepository


class _Document:
    def __init__(self, document_id: str, payload: dict, *, messages=None) -> None:
        self.id = document_id
        self._payload = dict(payload)
        self._messages = list(messages or [])
        self.exists = True

    def to_dict(self):
        return dict(self._payload)

    def get(self):
        return self

    def collection(self, name: str):
        assert name == "messages"
        return _MessageQuery(self._messages)


class _MissingDocument(_Document):
    def __init__(self, document_id: str) -> None:
        super().__init__(document_id, {})
        self.exists = False


class _MessageQuery:
    def __init__(self, documents, *, start=0, limit_count=None) -> None:
        self._documents = list(documents)
        self._start = start
        self._limit = limit_count

    def order_by(self, *_args, **_kwargs):
        return self

    def start_after(self, document):
        index = next(
            index for index, item in enumerate(self._documents) if item.id == document.id
        )
        return _MessageQuery(self._documents, start=index + 1, limit_count=self._limit)

    def limit(self, count):
        return _MessageQuery(self._documents, start=self._start, limit_count=count)

    def stream(self):
        rows = self._documents[self._start :]
        return rows[: self._limit] if self._limit is not None else rows

    def document(self, document_id):
        return next(
            (item for item in self._documents if item.id == document_id),
            _MissingDocument(document_id),
        )


class _ConversationQuery:
    def __init__(self, documents, *, limit_count=None) -> None:
        self._documents = list(documents)
        self._limit = limit_count

    def order_by(self, *_args, **_kwargs):
        return self

    def limit(self, count):
        return _ConversationQuery(self._documents, limit_count=count)

    def stream(self):
        return self._documents[: self._limit] if self._limit is not None else self._documents

    def document(self, document_id):
        return next(
            (item for item in self._documents if item.id == document_id),
            _MissingDocument(document_id),
        )


class _RootDocument:
    def __init__(self, conversations) -> None:
        self._conversations = conversations

    def collection(self, name: str):
        assert name == "conversations"
        return _ConversationQuery(self._conversations)


class _RootCollection:
    def __init__(self, conversations) -> None:
        self._conversations = conversations

    def document(self, _document_id: str):
        return _RootDocument(self._conversations)


def _repository(conversations):
    repository = ConversationHistoryRepository.__new__(ConversationHistoryRepository)
    repository._timezone = ZoneInfo("Asia/Tokyo")
    repository._root = lambda: _RootCollection(conversations)
    return repository


def test_conversation_rows_with_invalid_counts_are_skipped_locally() -> None:
    repository = _repository(
        [
            _Document(
                "visible",
                {
                    "title": "確認",
                    "messageCount": 2,
                    "updatedAt": datetime(2026, 8, 24, tzinfo=timezone.utc),
                },
            ),
            _Document("hidden", {"visibility": "hidden", "messageCount": 9}),
            _Document("broken", {"messageCount": "not-a-count"}),
        ]
    )

    rows = repository.list_conversations(chat_user_id="chat-1", limit=200)

    assert [row["conversationId"] for row in rows] == ["visible"]
    assert rows[0]["updatedAtJst"] == "2026-08-24 09:00:00"


def test_message_cursor_is_server_bounded_and_unknown_roles_do_not_break_the_page() -> None:
    messages = [
        _Document("m1", {"role": "user", "timestamp": "2026-08-24T00:00:00Z", "content": "質問"}),
        _Document("m2", {"role": "system", "timestamp": "2026-08-24T00:00:01Z", "content": "内部"}),
        _Document("m3", {"role": "assistant", "timestamp": "2026-08-24T00:00:02Z", "content": "回答"}),
    ]
    repository = _repository(
        [_Document("conversation-1", {"visibility": "active"}, messages=messages)]
    )

    first = repository.list_messages(
        chat_user_id="chat-1",
        conversation_id="conversation-1",
        limit=2,
    )
    assert [row["messageId"] for row in first["messages"]] == ["m1"]
    assert first["page"]["nextCursor"]

    second = repository.list_messages(
        chat_user_id="chat-1",
        conversation_id="conversation-1",
        limit=2,
        cursor=first["page"]["nextCursor"],
    )
    assert [row["messageId"] for row in second["messages"]] == ["m3"]
    assert second["page"]["nextCursor"] == ""


def test_hidden_or_unknown_conversation_and_invalid_cursor_are_rejected() -> None:
    repository = _repository(
        [_Document("hidden", {"visibility": "hidden"}, messages=[])]
    )

    with pytest.raises(ValueError, match="conversation not found"):
        repository.list_messages(
            chat_user_id="chat-1", conversation_id="hidden"
        )
    with pytest.raises(ValueError, match="conversation not found"):
        repository.list_messages(
            chat_user_id="chat-1", conversation_id="missing"
        )

    visible = _repository(
        [_Document("visible", {"visibility": "active"}, messages=[])]
    )
    with pytest.raises(ValueError, match="invalid message cursor"):
        visible.list_messages(
            chat_user_id="chat-1",
            conversation_id="visible",
            cursor="not-base64!",
        )
