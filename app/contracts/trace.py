from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class TraceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TraceMessage(TraceModel):
    messageId: str
    timestampJst: str
    role: str
    roleLabel: str
    content: str
    mode: str
    feedback: str
    status: str


class TracePage(TraceModel):
    nextCursor: str


class TraceMessagesResponse(TraceModel):
    status: Literal["ready", "identity_unmatched"]
    messages: list[TraceMessage]
    page: TracePage
