from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from app.api.schemas import ApplicationOut, JobOut
from app.database.models import ApplicationStatus
from app.job_categories import JobCategory


WarningText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]


def validate_http_url(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("url must be http/https")
    if parsed.username or parsed.password:
        raise ValueError("url must not contain user information")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("url has an invalid port") from exc
    return value


def trim_optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class PageCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(max_length=1000)
    title: str = Field(max_length=500)
    text: str = Field(default="", max_length=100_000)
    selected_text: str = Field(default="", max_length=20_000)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=20)
    model_config_id: int | None = Field(default=None, gt=0)

    _validate_url = field_validator("url")(validate_http_url)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, str]) -> dict[str, str]:
        if any(len(key) > 100 or len(item) > 1000 for key, item in value.items()):
            raise ValueError("metadata key/value too long")
        return value


class ExtractedJobFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    location: str | None = Field(default=None, max_length=300)
    employment_type: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=50_000)
    source_website: str | None = Field(default=None, max_length=200)
    published_at: date | None = None
    confidence: float = Field(ge=0, le=1)
    warnings: list[WarningText] = Field(default_factory=list, max_length=20)

    @field_validator("company_name", "title")
    @classmethod
    def trim_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    _trim_optional_fields = field_validator(
        "location",
        "employment_type",
        "description",
        "source_website",
    )(trim_optional)


class JobImportDraft(ExtractedJobFields):
    job_category: JobCategory | None = None
    source_url: str = Field(max_length=1000)
    original_title: str = Field(max_length=500)

    _validate_source_url = field_validator("source_url")(validate_http_url)


class DuplicateCandidate(BaseModel):
    job_id: int
    company_name: str
    title: str
    source_url: str | None
    reasons: list[Literal["source_url", "company_title"]]


class ExtractTelemetryModel(BaseModel):
    """Safe, user-visible identity of the selected local configuration."""

    config_id: int = Field(gt=0)
    display_name: str = Field(max_length=200)
    provider: str = Field(max_length=100)
    model: str = Field(max_length=200)


class ExtractTelemetry(BaseModel):
    """Optional telemetry for one logical extraction request.

    Token values remain nullable because compatible providers may omit one or
    both usage fields. ``usage_complete`` records whether both totals are
    known after all provider calls made by this request.
    """

    model: ExtractTelemetryModel
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    provider_calls: int | None = Field(default=None, ge=0)
    usage_complete: bool | None = None


class ExtractResponse(BaseModel):
    draft: JobImportDraft
    duplicates: list[DuplicateCandidate]
    model_config_id: int
    # Optional for compatibility with old extraction responses and fixtures.
    telemetry: ExtractTelemetry | None = None


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft: JobImportDraft
    application_status: ApplicationStatus
    notes: str | None = Field(default=None, max_length=10_000)
    allow_duplicate: bool = False


class ConfirmResponse(BaseModel):
    job: JobOut
    application: ApplicationOut
