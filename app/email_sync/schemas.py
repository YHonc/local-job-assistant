from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)

from app.database.models import ApplicationStatus, RecruitmentEventType

from .presets import EmailProviderType


def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _validate_email(value: str | None) -> str:
    if value is None:
        raise ValueError("email address must not be null")
    value = value.strip()
    if len(value) > 320 or value.count("@") != 1 or any(char.isspace() for char in value):
        raise ValueError("invalid email address")
    local, domain = value.rsplit("@", 1)
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("invalid email address")
    return value


def _validate_host(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or "://" in value or "/" in value or "@" in value:
        raise ValueError("IMAP host must be a hostname or IP address")
    return value


class EmailAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email_address: str
    provider_type: EmailProviderType
    imap_host: str | None = Field(default=None, max_length=255)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    use_ssl: bool = True
    username: str | None = Field(default=None, max_length=320)
    enabled: bool = True
    check_interval_minutes: int = Field(default=15, ge=1, le=1440)
    sync_lookback_days: int | None = Field(default=None, ge=1, le=3650)
    authorization_code: SecretStr | None = Field(default=None, min_length=1, max_length=1000)

    _email = field_validator("email_address")(_validate_email)
    _host = field_validator("imap_host")(_validate_host)

    @field_validator("username")
    @classmethod
    def trim_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def custom_requires_server(self):
        if self.provider_type == EmailProviderType.CUSTOM and (
            self.imap_host is None or self.imap_port is None
        ):
            raise ValueError("custom IMAP requires host and port")
        return self


class EmailAccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email_address: str | None = None
    provider_type: EmailProviderType | None = None
    imap_host: str | None = Field(default=None, max_length=255)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    use_ssl: bool | None = None
    username: str | None = Field(default=None, max_length=320)
    enabled: bool | None = None
    check_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    # 显式传 null 表示清除回看限制。
    sync_lookback_days: int | None = Field(default=None, ge=1, le=3650)
    authorization_code: SecretStr | None = Field(default=None, min_length=1, max_length=1000)
    clear_authorization_code: bool = False

    _email = field_validator("email_address")(_validate_email)
    _host = field_validator("imap_host")(_validate_host)

    @model_validator(mode="before")
    @classmethod
    def reject_null_identity_fields(cls, values):
        if isinstance(values, dict):
            for key in ("email_address", "provider_type", "enabled", "check_interval_minutes"):
                if key in values and values[key] is None:
                    raise ValueError(f"{key} must not be null")
        return values

    @model_validator(mode="after")
    def credential_action_is_unambiguous(self):
        if self.authorization_code is not None and self.clear_authorization_code:
            raise ValueError("authorization_code and clear_authorization_code are mutually exclusive")
        return self


class EmailAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email_address: str
    provider_type: str
    imap_host: str
    imap_port: int
    use_ssl: bool
    username: str
    enabled: bool
    check_interval_minutes: int
    sync_lookback_days: int | None
    uid_validity: int | None
    last_uid: int
    last_sync_at: datetime | None
    last_sync_status: str
    last_sync_error: str | None
    has_authorization_code: bool
    created_at: datetime
    updated_at: datetime

    @field_serializer("last_sync_at", "created_at", "updated_at")
    def serialize_datetimes(self, value: datetime | None) -> datetime | None:
        return _utc(value)


class EmailMessageOut(BaseModel):
    id: int
    email_account_id: int
    message_id: str | None
    uid_validity: int
    uid: int
    sender: str
    subject: str
    received_at: datetime | None
    body_preview: str | None
    pre_filtered: bool
    llm_processed: bool
    llm_classification: str | None
    llm_attempt_count: int = 0
    processing_error: str | None
    synced_at: datetime
    # Compact cleaning facts are included in list responses so the inbox can
    # render a useful row without fetching the full message detail endpoint.
    summary: str | None = None
    company_name: str | None = None
    job_title: str | None = None
    work_location: str | None = None
    cleaning_status: str | None = None
    cleaning_result_id: int | None = None
    archive_status: str | None = None
    job_entries: list["EmailJobEntryOut"] = Field(default_factory=list)

    @field_serializer("received_at", "synced_at")
    def serialize_datetimes(self, value: datetime | None) -> datetime | None:
        return _utc(value)


class EmailMessageDetail(EmailMessageOut):
    """Full local copy of a stored email, including the trimmed body."""

    body_text: str | None
    event_id: int | None
    event_ids: list[int] = Field(default_factory=list)
    cleaning: "EmailCleaningOut | None" = None
    events: list["RecruitmentEventOut"] = Field(default_factory=list)


class EmailMessageDeleteResult(BaseModel):
    deleted: bool
    email_id: int
    event_id: int | None
    notifications_deleted: int


Keyword = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]


class EmailFilterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keywords: list[Keyword] = Field(default_factory=list, max_length=100)
    regex: str | None = Field(default=None, max_length=500)

    @field_validator("regex")
    @classmethod
    def validate_regex(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError("regex is not a valid regular expression") from exc
        return value

    @field_validator("keywords")
    @classmethod
    def unique_keywords(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for keyword in value:
            normalized = keyword.casefold()
            if normalized not in seen:
                result.append(keyword)
                seen.add(normalized)
        return result


class ManualEmailMessageIn(BaseModel):
    """A user-supplied email that skips pre-filtering but not LLM review."""

    model_config = ConfigDict(extra="forbid")

    account_id: int
    sender: str = Field(min_length=1, max_length=500)
    subject: str = Field(min_length=1, max_length=1000)
    body_text: str | None = Field(default=None, max_length=100_000)
    message_id: str | None = Field(default=None, max_length=998)
    uid: int = Field(default=0, ge=0)


class SyncResult(BaseModel):
    account_id: int
    fetched: int
    created: int
    duplicates: int
    filtered_candidates: int
    events_created: int
    processing_errors: int
    skipped_by_regex: int = 0
    retry_paused: int = 0


class EmailClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_recruitment: bool
    event_type: RecruitmentEventType = RecruitmentEventType.OTHER
    company_name: str | None = Field(default=None, max_length=200)
    job_title: str | None = Field(default=None, max_length=300)
    event_time: datetime | None = None
    deadline: datetime | None = None
    location: str | None = Field(default=None, max_length=500)
    meeting_url: str | None = Field(default=None, max_length=1000)
    action_required: bool = False
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(default="No recruitment event detected.", min_length=1, max_length=5000)
    suggested_application_status: ApplicationStatus | None = None

    @field_validator("company_name", "job_title", "location", "meeting_url")
    @classmethod
    def trim_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("meeting_url")
    @classmethod
    def validate_meeting_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("meeting_url must be http/https")
        if parsed.username or parsed.password:
            raise ValueError("meeting_url must not contain user information")
        return value


class EmailJobExtraction(BaseModel):
    """One position/event returned by the email cleaning prompt.

    The provider output is allowed to use the concise keys (``company`` and
    ``job``) or the legacy event keys (``company_name`` and ``job_title``).
    Local validation converts both into one stable representation; unknown
    provider fields are ignored so adding optional extraction hints does not
    break an otherwise valid result.
    """

    model_config = ConfigDict(extra="ignore")

    company: str | None = Field(default=None, max_length=200)
    job: str | None = Field(default=None, max_length=300)
    work_location: str | None = Field(default=None, max_length=500)
    links: list[str] = Field(default_factory=list, max_length=50)
    field_evidence: dict[str, Any] | list[Any] | None = None
    event_type: RecruitmentEventType = RecruitmentEventType.OTHER
    event_round: str | None = Field(default=None, max_length=50)
    event_time: datetime | None = None
    deadline: datetime | None = None
    action_required: bool = False
    actions: list[str] = Field(default_factory=list, max_length=50)
    summary: str | None = Field(default=None, max_length=5000)
    body: str | None = Field(default=None, max_length=100_000)
    meeting_url: str | None = Field(default=None, max_length=1000)
    suggested_application_status: ApplicationStatus | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_keys(cls, values):
        if not isinstance(values, dict):
            return values
        values = dict(values)
        if values.get("company") is None:
            values["company"] = values.get("company_name")
        if values.get("job") is None:
            values["job"] = values.get("job_title") or values.get("title")
        # ``location`` is the legacy event/meeting venue.  It must not be
        # silently promoted to a job work location; new prompts use the
        # explicit ``work_location`` key for that fact.
        return values

    @field_validator("company", "job", "work_location", "summary", "body", "meeting_url")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("event_round")
    @classmethod
    def trim_event_round(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("links")
    @classmethod
    def clean_links(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if isinstance(item, str) and item.strip()][:50]

    @field_validator("actions")
    @classmethod
    def clean_actions(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if isinstance(item, str) and item.strip()][:50]


class EmailCleaningOutput(BaseModel):
    """Versioned structured output used by the email cleaning pipeline."""

    model_config = ConfigDict(extra="ignore")

    is_recruitment: bool = True
    body: str | None = Field(default=None, max_length=100_000)
    summary: str | None = Field(default=None, max_length=5000)
    company: str | None = Field(default=None, max_length=200)
    job: str | None = Field(default=None, max_length=300)
    work_location: str | None = Field(default=None, max_length=500)
    links: list[str] = Field(default_factory=list, max_length=50)
    field_evidence: dict[str, Any] | list[Any] | None = None
    event_time: datetime | None = None
    deadline: datetime | None = None
    action_required: bool = False
    actions: list[str] = Field(default_factory=list, max_length=50)
    event_type: RecruitmentEventType = RecruitmentEventType.OTHER
    event_round: str | None = Field(default=None, max_length=50)
    suggested_application_status: ApplicationStatus | None = None
    jobs: list[EmailJobExtraction] = Field(default_factory=list, max_length=100)
    events: list[EmailJobExtraction] = Field(default_factory=list, max_length=100)

    # Legacy aliases are accepted and normalized in the same pass.  Keeping
    # them optional lets old configured prompts continue to work while all
    # persisted rows expose the new concise names.
    company_name: str | None = Field(default=None, max_length=200)
    job_title: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=500)
    meeting_url: str | None = Field(default=None, max_length=1000)
    confidence: float = Field(default=0.0, ge=0, le=1)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_output(cls, values):
        if not isinstance(values, dict):
            return values
        values = dict(values)
        if values.get("company") is None:
            values["company"] = values.get("company_name")
        if values.get("job") is None:
            values["job"] = values.get("job_title")
        # Keep legacy ``location`` solely for the old event compatibility
        # field.  A meeting venue is not a job work location.
        jobs = values.get("jobs") or values.get("job_entries") or values.get("positions")
        events = values.get("events") or values.get("recruitment_events")
        if jobs is not None:
            values["jobs"] = jobs
        if events is not None:
            values["events"] = events
        return values

    @field_validator("body", "summary", "company", "job", "work_location", "company_name", "job_title", "location", "meeting_url")
    @classmethod
    def trim_optional_cleaning_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("links")
    @classmethod
    def clean_cleaning_links(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if isinstance(item, str) and item.strip()][:50]

    @field_validator("actions")
    @classmethod
    def clean_cleaning_actions(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if isinstance(item, str) and item.strip()][:50]


class EmailCleaningOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email_message_id: int
    status: str
    version: int
    cleaned_body: str | None = None
    summary: str | None = None
    company_name: str | None = None
    job_title: str | None = None
    work_location: str | None = None
    links: list[str] = Field(default_factory=list)
    field_evidence: dict[str, Any] | list[Any] | None = None
    event_time: datetime | None = None
    deadline: datetime | None = None
    action_required: bool = False
    actions: list[str] = Field(default_factory=list)
    error: str | None = None
    attempt_count: int = 0
    ai_processed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    job_entries: list["EmailJobEntryOut"] = Field(default_factory=list)
    archive_status: str | None = None

    @field_serializer("event_time", "deadline", "ai_processed_at", "created_at", "updated_at")
    def serialize_cleaning_datetimes(self, value: datetime | None) -> datetime | None:
        return _utc(value)


class EmailJobEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    identity_key: str
    cleaning_result_id: int | None = None
    source_email_id: int | None = None
    facts_source: str = "ai"
    version: int = 1
    company_id: int | None = None
    job_id: int | None = None
    application_id: int | None = None
    recruitment_event_id: int | None = None
    company_name: str | None = None
    job_title: str | None = None
    work_location: str | None = None
    links: list[str] = Field(default_factory=list)
    field_evidence: dict[str, Any] | list[Any] | None = None
    event_type: str
    event_round: str | None = None
    event_time: datetime | None = None
    deadline: datetime | None = None
    action_required: bool = False
    actions: list[str] = Field(default_factory=list)
    summary: str | None = None
    match_status: str
    match_reason: str | None = None
    active: bool = True
    manually_associated: bool = False
    candidates: list["JobAssociationCandidate"] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @field_validator("links", "actions", mode="before")
    @classmethod
    def empty_optional_lists(cls, value):
        # Revisions before the structured cleaning contract allowed SQL NULL
        # in these JSON columns; keep the legacy read API compatible.
        return value or []

    @field_serializer("event_time", "deadline", "created_at", "updated_at")
    def serialize_entry_datetimes(self, value: datetime | None) -> datetime | None:
        return _utc(value)


class EmailArchiveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cleaning_result_id: int
    status: str
    lease_until: datetime | None = None
    requested_at: datetime
    archived_at: datetime | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("lease_until", "requested_at", "archived_at", "created_at", "updated_at")
    def serialize_archive_datetimes(self, value: datetime | None) -> datetime | None:
        return _utc(value)


class EmailJobAssociationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: int | None = None
    job_id: int | None = None
    application_id: int | None = None
    dismiss: bool = False

    @model_validator(mode="after")
    def validate_association_action(self):
        if self.dismiss and any(
            value is not None
            for value in (self.company_id, self.job_id, self.application_id)
        ):
            raise ValueError("association IDs and dismiss are mutually exclusive")
        if not self.dismiss and all(
            value is None
            for value in (self.company_id, self.job_id, self.application_id)
        ):
            raise ValueError("one association ID is required")
        return self


class EmailFilterPreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender: str = Field(default="", max_length=500)
    subject: str = Field(default="", max_length=1000)
    body_text: str | None = Field(default=None, max_length=100_000)
    regex: str | None = Field(default=None, max_length=500)

    @field_validator("regex")
    @classmethod
    def validate_preview_regex(cls, value):
        if value:
            import re
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError("invalid regular expression") from exc
        return value


class EmailFilterPreviewOut(BaseModel):
    matched: bool
    regex_active: bool
    regex_match: bool
    keyword_match: bool
    reason: str


class AssociationCandidate(BaseModel):
    application_id: int
    job_id: int
    company_id: int
    company_name: str
    job_title: str
    application_status: str
    score: int
    reasons: list[str]


class JobAssociationCandidate(BaseModel):
    """Candidate scored directly against a Job (Application is optional)."""

    job_id: int
    company_id: int
    application_id: int | None = None
    company_name: str
    job_title: str
    application_status: str | None = None
    score: int
    reasons: list[str]


class RecruitmentEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int | None
    job_id: int | None
    application_id: int | None
    suggested_application_id: int | None
    source_email_id: int | None
    company_name: str | None
    job_title: str | None
    event_type: str
    event_time: datetime | None
    deadline: datetime | None
    location: str | None
    meeting_url: str | None
    action_required: bool
    confidence: float
    ai_summary: str
    suggested_application_status: str | None
    association_status: str
    created_at: datetime
    updated_at: datetime
    candidates: list[AssociationCandidate] = Field(default_factory=list)
    email_job_entry_id: int | None = None
    active: bool = True

    @field_serializer("event_time", "deadline", "created_at", "updated_at")
    def serialize_datetimes(self, value: datetime | None) -> datetime | None:
        return _utc(value)


class AssociationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_id: int | None = None
    dismiss: bool = False

    @model_validator(mode="after")
    def association_action_is_unambiguous(self):
        if self.application_id is not None and self.dismiss:
            raise ValueError("application_id and dismiss are mutually exclusive")
        return self


class ProcessMessageResult(BaseModel):
    message_id: int
    event_id: int | None
    classification: str


EmailProviderLiteral = Literal["qq", "163", "custom"]
