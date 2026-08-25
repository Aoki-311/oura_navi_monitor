from __future__ import annotations

import re
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.analysis_scopes import Department


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
LABEL_COLORS = frozenset(
    {"#23d28f", "#386dff", "#ffb340", "#ff5b74", "#7c5cff", "#27d9d2", "#5f6285"}
)


def normalize_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if not _EMAIL_RE.fullmatch(email):
        raise ValueError("invalid email")
    return email


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    email: str
    area: str = Field(min_length=1, max_length=80)
    workplace: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=80)
    department: Department
    mr_experience: str = Field(default="-", max_length=80)
    label_ids: list[str] = Field(default_factory=list, max_length=30)
    is_active: bool = True

    _email = field_validator("email")(normalize_email)


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = None
    area: str | None = Field(default=None, min_length=1, max_length=80)
    workplace: str | None = Field(default=None, min_length=1, max_length=80)
    role: str | None = Field(default=None, min_length=1, max_length=80)
    department: Department | None = None
    mr_experience: str | None = Field(default=None, max_length=80)
    label_ids: list[str] | None = Field(default=None, max_length=30)
    is_active: bool | None = None
    expected_updated_at: str = Field(default="", max_length=80)

    @field_validator("email")
    @classmethod
    def _normalize_optional_email(cls, value: str | None) -> str | None:
        return normalize_email(value) if value is not None else None


class LabelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=40)
    color: str

    @field_validator("color")
    @classmethod
    def _fixed_color(cls, value: str) -> str:
        color = str(value or "").strip().lower()
        if color not in LABEL_COLORS:
            raise ValueError("unsupported label color")
        return color


class LabelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=40)
    color: str | None = None
    is_active: bool | None = None
    expected_updated_at: str = Field(default="", max_length=80)

    @field_validator("color")
    @classmethod
    def _fixed_optional_color(cls, value: str | None) -> str | None:
        if value is None:
            return None
        color = str(value).strip().lower()
        if color not in LABEL_COLORS:
            raise ValueError("unsupported label color")
        return color


class LabelDelete(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_updated_at: str = Field(default="", max_length=80)


class UserView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rosterId: str
    name: str
    email: str
    area: str
    areaKey: str
    workplace: str
    role: str
    department: Department
    mrExperience: str
    labelIds: list[str]
    isActive: bool
    identityBound: bool
    updatedAt: str | None
    updatedBy: str


class UserListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    users: list[UserView]


class LabelView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    labelId: str
    name: str
    color: str
    usageCount: int
    isActive: bool
    updatedAt: str | None
    updatedBy: str


class LabelListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    labels: list[LabelView]


class ManagementMetadataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    areas: list[str]
    workplaces: list[str]
    roles: list[str]
    departments: list[Department]
    labelColors: list[str]
