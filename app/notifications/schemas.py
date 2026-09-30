from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_serializer,
    field_validator,
    model_validator,
)

from app.database.models import RecruitmentEventType


IMPORTANT_EVENT_TYPES = [
    RecruitmentEventType.ASSESSMENT,
    RecruitmentEventType.WRITTEN_TEST,
    RecruitmentEventType.INTERVIEW,
    RecruitmentEventType.INTERVIEW_CHANGED,
    RecruitmentEventType.OFFER,
]


class NotificationPolicyPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    desktop_enabled: bool = True
    push_enabled: bool = False
    push_provider: Literal["ntfy", "bark", "custom"] | None = None
    push_url: SecretStr | None = Field(default=None, min_length=1, max_length=2000)
    clear_push_url: bool = False
    important_event_types: list[RecruitmentEventType] = Field(
        default_factory=lambda: list(IMPORTANT_EVENT_TYPES),
        min_length=1,
        max_length=len(RecruitmentEventType),
    )
    notify_actionable_deadlines: bool = True

    @field_validator("important_event_types")
    @classmethod
    def unique_event_types(
        cls,
        value: list[RecruitmentEventType],
    ) -> list[RecruitmentEventType]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_push_action(self):
        if self.push_url is not None and self.clear_push_url:
            raise ValueError("push_url and clear_push_url are mutually exclusive")
        if self.push_enabled and self.clear_push_url:
            raise ValueError("push cannot stay enabled while clearing its URL")
        if self.push_enabled and self.push_provider is None:
            raise ValueError("push_provider is required when push is enabled")
        if self.push_url is not None:
            value = self.push_url.get_secret_value()
            if "://" not in value or any(char.isspace() for char in value):
                raise ValueError("push_url must be an Apprise URL")
            scheme = value.split("://", 1)[0].casefold()
            if self.push_provider == "ntfy" and scheme not in {"ntfy", "ntfys"}:
                raise ValueError("ntfy provider requires an ntfy:// or ntfys:// URL")
            if self.push_provider == "bark" and scheme not in {"bark"}:
                raise ValueError("bark provider requires a bark:// URL")
        return self


class NotificationPolicyCreate(NotificationPolicyPut):
    name: str = Field(default="新策略", max_length=200)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class NotificationPolicyOut(BaseModel):
    configured: bool
    desktop_enabled: bool
    push_enabled: bool
    push_provider: str | None
    has_push_url: bool
    important_event_types: list[str]
    notify_actionable_deadlines: bool
    updated_at: datetime | None

    @field_serializer("updated_at")
    def serialize_updated_at(self, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class NotificationPolicySummary(NotificationPolicyOut):
    id: int
    name: str
    enabled: bool
    created_at: datetime | None = None

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int | None
    provider: str
    title: str
    content: str
    status: str
    success: bool
    error: str | None
    attempt_count: int
    next_retry_at: datetime | None
    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("next_retry_at", "sent_at", "created_at", "updated_at")
    def serialize_datetimes(self, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class TestNotificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["desktop", "push"]
