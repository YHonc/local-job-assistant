from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.schemas import ApplicationOut, JobOut
from app.database.models import ApplicationStatus
from app.job_categories import JobCategory

from .schemas import DuplicateCandidate, validate_http_url

class ImportDraftFields(BaseModel):
    """Partial fields explicitly entered or accepted by the user."""

    model_config = ConfigDict(extra="forbid")

    company_name: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=300)
    employment_type: str | None = Field(default=None, max_length=100)
    job_category: JobCategory | None = None
    published_at: date | None = None
    description: str | None = Field(default=None, max_length=50_000)
    source_website: str | None = Field(default=None, max_length=200)

    @field_validator(
        "company_name",
        "title",
        "location",
        "employment_type",
        "description",
        "source_website",
    )
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class SuggestedJobFields(ImportDraftFields):
    """Model output, kept separate until the user explicitly accepts fields."""

    confidence: float = Field(ge=0, le=1)
    warnings: list[str] = Field(default_factory=list, max_length=20)


class CreateImportDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: str | None = Field(default=None, max_length=1000)
    source_website: str | None = Field(default=None, max_length=200)
    original_title: str | None = Field(default=None, max_length=500)
    captured_text: str = Field(default="", max_length=100_000)
    user_fields: ImportDraftFields = Field(default_factory=ImportDraftFields)
    idempotency_key: str = Field(min_length=8, max_length=255)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str | None) -> str | None:
        return validate_http_url(value) if value is not None else None

    @field_validator("source_website", "original_title")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("idempotency_key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("idempotency_key must not be blank")
        return value


class PatchImportDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    source_url: str | None = Field(default=None, max_length=1000)
    source_website: str | None = Field(default=None, max_length=200)
    original_title: str | None = Field(default=None, max_length=500)
    captured_text: str | None = Field(default=None, max_length=100_000)
    user_fields: ImportDraftFields | None = None

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str | None) -> str | None:
        return validate_http_url(value) if value is not None else None

    @field_validator("source_website", "original_title")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("captured_text")
    @classmethod
    def preserve_capture(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("captured_text must not be null")
        return value


class DraftVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    model_config_id: int | None = Field(default=None, gt=0)


AcceptedDraftField = Literal[
    "company_name",
    "title",
    "location",
    "employment_type",
    "job_category",
    "published_at",
    "description",
    "source_website",
]


class AcceptDraftSuggestionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    fields: list[AcceptedDraftField] = Field(min_length=1, max_length=8)


class ConfirmImportDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    application_status: ApplicationStatus | None = None
    notes: str | None = Field(default=None, max_length=10_000)
    allow_duplicate: bool = False

    @model_validator(mode="after")
    def notes_require_application(self) -> "ConfirmImportDraftRequest":
        if self.notes and self.application_status is None:
            raise ValueError("notes require an application_status")
        return self


class ImportDraftOut(BaseModel):
    id: int
    source_url: str | None
    source_website: str | None
    original_title: str | None
    captured_text: str
    user_fields: dict
    suggested_fields: dict | None
    effective_fields: dict
    status: Literal["draft", "extracted", "confirmed"]
    version: int
    idempotency_key: str
    confirmed_job_id: int | None
    created_at: datetime
    updated_at: datetime
    error: str | None


class ImportDraftListOut(BaseModel):
    items: list[ImportDraftOut]
    total: int
    limit: int
    offset: int


class ImportDraftConfirmOut(BaseModel):
    draft: ImportDraftOut
    job: JobOut
    application: ApplicationOut | None
    duplicates: list[DuplicateCandidate] = Field(default_factory=list)


class ImportDraftExtractOut(BaseModel):
    draft: ImportDraftOut
    duplicates: list[DuplicateCandidate] = Field(default_factory=list)
    model_config_id: int
