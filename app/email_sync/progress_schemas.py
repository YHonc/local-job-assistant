from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from app.database.models import RecruitmentEventType


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ManualEmailFactsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=128)
    company_name: str = Field(min_length=1, max_length=200)
    job_title: str = Field(min_length=1, max_length=300)
    work_location: str | None = Field(default=None, max_length=500)
    links: list[str] = Field(default_factory=list, max_length=50)
    event_type: RecruitmentEventType
    event_round: str | None = Field(default=None, max_length=50)
    event_time: datetime | None = None
    deadline: datetime | None = None
    action_required: bool = False
    actions: list[str] = Field(default_factory=list, max_length=50)
    summary: str | None = Field(default=None, max_length=5000)
    field_evidence: dict[str, Any] | list[Any]

    @field_validator("company_name", "job_title")
    @classmethod
    def require_trimmed_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("field is required")
        return value

    @field_validator("work_location", "event_round", "summary")
    @classmethod
    def trim_optional_text(cls, value):
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("field_evidence")
    @classmethod
    def require_evidence(cls, value):
        if not value:
            raise ValueError("field_evidence must include human-checked source evidence")
        return value

    @field_validator("links", "actions")
    @classmethod
    def trim_lists(cls, value):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]


class ManualEmailFactsUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_entry_version: int = Field(ge=1)
    company_name: str = Field(min_length=1, max_length=200)
    job_title: str = Field(min_length=1, max_length=300)
    work_location: str | None = Field(default=None, max_length=500)
    links: list[str] = Field(default_factory=list, max_length=50)
    event_type: RecruitmentEventType
    event_round: str | None = Field(default=None, max_length=50)
    event_time: datetime | None = None
    deadline: datetime | None = None
    action_required: bool = False
    actions: list[str] = Field(default_factory=list, max_length=50)
    summary: str | None = Field(default=None, max_length=5000)
    field_evidence: dict[str, Any] | list[Any]

    @field_validator("company_name", "job_title")
    @classmethod
    def require_trimmed_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("field is required")
        return value

    @field_validator("work_location", "event_round", "summary")
    @classmethod
    def trim_optional_text(cls, value):
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("field_evidence")
    @classmethod
    def require_evidence(cls, value):
        if not value:
            raise ValueError("field_evidence must include human-checked source evidence")
        return value


class ProgressSuggestionCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_entry_version: int = Field(ge=1)


class ProgressSuggestionAcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_suggestion_version: int = Field(ge=1)
    expected_application_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ProgressSuggestionRejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_suggestion_version: int = Field(ge=1)


class ProgressSuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email_job_entry_id: int
    source_email_id: int
    cleaning_result_id: int | None = None
    cleaning_version: int | None = None
    entry_version: int
    company_id: int | None = None
    job_id: int | None = None
    application_id: int | None = None
    application_version: int | None = None
    expected_application_status: str | None = None
    target_status: str | None = None
    event_type: str
    event_round: str | None = None
    evidence: dict[str, Any] | list[Any] | None = None
    reason: str
    status: str
    version: int
    result_application_status: str | None = None
    result_application_version: int | None = None
    accepted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("accepted_at", "created_at", "updated_at")
    def serialize_dates(self, value: datetime | None) -> datetime | None:
        return _utc(value)
