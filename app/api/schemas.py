from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from app.database.models import ApplicationStatus
from app.job_categories import JobCategory


def _trim(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class CompanyIn(BaseModel):
    name: str = Field(max_length=200)
    aliases: list[str] | None = None
    notes: str | None = None
    _name = field_validator("name")(_trim)

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        result: list[str] = []
        for alias in value:
            alias = _trim(alias)
            if alias not in result:
                result.append(alias)
        return result


class CompanyPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    aliases: list[str] | None = None
    notes: str | None = None
    _name = field_validator("name")(_trim)
    @model_validator(mode="before")
    @classmethod
    def reject_null_name(cls, values):
        if isinstance(values, dict) and "name" in values and values["name"] is None:
            raise ValueError("name must not be null")
        return values

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        result: list[str] = []
        for alias in value:
            alias = _trim(alias)
            if alias not in result:
                result.append(alias)
        return result


class CompanyOut(CompanyIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class JobIn(BaseModel):
    company_id: int
    title: str = Field(max_length=300)
    location: str | None = None
    employment_type: str | None = None
    job_category: JobCategory | None = None
    description: str | None = None
    source_url: str | None = None
    source_website: str | None = None
    original_title: str | None = None
    published_at: date | None = None
    structured_data: dict[str, Any] | list[Any] | None = None
    _title = field_validator("title")(_trim)


class JobPatch(BaseModel):
    company_id: int | None = None
    title: str | None = Field(default=None, max_length=300)
    location: str | None = None
    employment_type: str | None = None
    job_category: JobCategory | None = None
    description: str | None = None
    source_url: str | None = None
    source_website: str | None = None
    original_title: str | None = None
    published_at: date | None = None
    structured_data: dict[str, Any] | list[Any] | None = None
    _title = field_validator("title")(_trim)

    @model_validator(mode="before")
    @classmethod
    def reject_null_required(cls, values):
        if isinstance(values, dict):
            for key in ("title", "company_id"):
                if key in values and values[key] is None:
                    raise ValueError(f"{key} must not be null")
        return values


class JobBulkDelete(BaseModel):
    job_ids: list[int] = Field(min_length=1, max_length=200)

    @field_validator("job_ids")
    @classmethod
    def unique_positive_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("job_ids must be positive")
        if len(set(value)) != len(value):
            raise ValueError("job_ids must be unique")
        return value


class JobBulkDeleteOut(BaseModel):
    deleted_count: int
    deleted_job_ids: list[int]


class JobOut(JobIn):
    model_config = ConfigDict(from_attributes=True)
    job_category: JobCategory
    id: int
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class ApplicationIn(BaseModel):
    job_id: int
    status: ApplicationStatus = ApplicationStatus.收藏
    notes: str | None = None


class ApplicationUpdate(BaseModel):
    status: ApplicationStatus | None = None
    notes: str | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_status(cls, values):
        if isinstance(values, dict) and "status" in values and values["status"] is None:
            raise ValueError("status must not be null")
        return values


class ApplicationBulkStatus(BaseModel):
    application_ids: list[int] = Field(min_length=1, max_length=200)
    status: ApplicationStatus

    @field_validator("application_ids")
    @classmethod
    def unique_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("application_ids must be positive")
        if len(set(value)) != len(value):
            raise ValueError("application_ids must be unique")
        return value


class ApplicationOut(ApplicationIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status_updated_at: datetime

    @field_serializer("status_updated_at")
    def serialize_status_updated_at(self, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class SettingIn(BaseModel):
    """Non-sensitive application settings only; credentials must not be stored here."""
    value: str | None = None

class PreferencesUpdate(BaseModel):
    theme: Literal["light", "dark"]
    density: Literal["comfortable", "compact"]
    poetry_preset: Literal["random", "shuidiao", "dielianhua", "qingpingle", "yulinling", "jiangchengzi", "nian"] | None = None


WorkbenchOrganizeStatus = Literal["pending", "organized", "archived"]
WorkbenchCategoryReviewStatus = Literal["pending", "confirmed"]
WorkbenchDisplayField = Literal[
    "category", "tags", "application", "organization", "location", "salary"
]


def _normalize_tags(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    if len(value) > 50:
        raise ValueError("tags must contain at most 50 items")
    result: list[str] = []
    seen: set[str] = set()
    for tag in value:
        cleaned = _trim(tag)
        if len(cleaned) > 64:
            raise ValueError("each tag must contain at most 64 characters")
        folded = cleaned.casefold()
        if folded not in seen:
            result.append(cleaned)
            seen.add(folded)
    return result


class WorkbenchStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkbenchCompanyCreate(WorkbenchStrictModel):
    name: str = Field(max_length=200)
    aliases: list[str] | None = None
    notes: str | None = None
    _name = field_validator("name")(_trim)

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        return CompanyIn.normalize_aliases(value)


class WorkbenchJobCreateFields(WorkbenchStrictModel):
    title: str = Field(max_length=300)
    location: str | None = None
    employment_type: str | None = None
    job_category: JobCategory | None = None
    category_review_status: WorkbenchCategoryReviewStatus = "pending"
    tags: list[str] = Field(default_factory=list)
    organize_status: WorkbenchOrganizeStatus = "pending"
    salary_text: str | None = Field(default=None, max_length=1000)
    notes: str | None = None
    description: str | None = None
    source_url: str | None = None
    source_website: str | None = None
    original_title: str | None = None
    published_at: date | None = None
    structured_data: dict[str, Any] | list[Any] | None = None
    _title = field_validator("title")(_trim)
    _tags = field_validator("tags")(_normalize_tags)

    @model_validator(mode="after")
    def explicit_category_is_confirmed(self):
        if self.job_category is not None and "category_review_status" not in self.model_fields_set:
            self.category_review_status = "confirmed"
        return self


class WorkbenchJobPatchFields(WorkbenchStrictModel):
    company_id: int | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, max_length=300)
    location: str | None = None
    employment_type: str | None = None
    job_category: JobCategory | None = None
    category_review_status: WorkbenchCategoryReviewStatus | None = None
    tags: list[str] | None = None
    organize_status: WorkbenchOrganizeStatus | None = None
    salary_text: str | None = Field(default=None, max_length=1000)
    notes: str | None = None
    description: str | None = None
    source_url: str | None = None
    source_website: str | None = None
    original_title: str | None = None
    published_at: date | None = None
    structured_data: dict[str, Any] | list[Any] | None = None
    _title = field_validator("title")(_trim)
    _tags = field_validator("tags")(_normalize_tags)

    @model_validator(mode="before")
    @classmethod
    def reject_null_required(cls, values):
        if isinstance(values, dict):
            for key in ("title", "company_id"):
                if key in values and values[key] is None:
                    raise ValueError(f"{key} must not be null")
        return values


class WorkbenchApplicationCreate(WorkbenchStrictModel):
    status: ApplicationStatus = ApplicationStatus.收藏
    notes: str | None = None


class WorkbenchApplicationPatch(WorkbenchStrictModel):
    status: ApplicationStatus | None = None
    notes: str | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_status(cls, values):
        if isinstance(values, dict) and "status" in values and values["status"] is None:
            raise ValueError("status must not be null")
        return values


class WorkbenchJobCreate(WorkbenchStrictModel):
    company_id: int | None = Field(default=None, gt=0)
    company: WorkbenchCompanyCreate | None = None
    job: WorkbenchJobCreateFields
    application: WorkbenchApplicationCreate | None = None
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: str) -> str:
        return _trim(value)

    @model_validator(mode="after")
    def require_company_target(self):
        if (self.company_id is None) == (self.company is None):
            raise ValueError("provide exactly one of company_id or company")
        return self


class WorkbenchJobUpdate(WorkbenchStrictModel):
    expected_job_version: int = Field(ge=1)
    job: WorkbenchJobPatchFields | None = None
    expected_application_version: int | None = Field(default=None, ge=1)
    application: WorkbenchApplicationPatch | None = None

    @model_validator(mode="after")
    def require_mutation(self):
        if self.job is None and self.application is None:
            raise ValueError("at least one of job or application is required")
        if self.application is not None and self.expected_application_version is None:
            raise ValueError("expected_application_version is required with application")
        if self.job is not None and not self.job.model_fields_set:
            raise ValueError("job patch must contain at least one field")
        if self.application is not None and not self.application.model_fields_set:
            raise ValueError("application patch must contain at least one field")
        return self


class WorkbenchBulkJobVersion(WorkbenchStrictModel):
    job_id: int = Field(gt=0)
    expected_version: int = Field(ge=1)


class WorkbenchBulkOrganize(WorkbenchStrictModel):
    items: list[WorkbenchBulkJobVersion] = Field(min_length=1, max_length=200)
    job_category: JobCategory | None = None
    category_review_status: WorkbenchCategoryReviewStatus | None = None
    tags_to_add: list[str] | None = None
    organize_status: WorkbenchOrganizeStatus | None = None

    @field_validator("items")
    @classmethod
    def unique_jobs(cls, value: list[WorkbenchBulkJobVersion]) -> list[WorkbenchBulkJobVersion]:
        ids = [item.job_id for item in value]
        if len(set(ids)) != len(ids):
            raise ValueError("job ids must be unique")
        return value

    @field_validator("tags_to_add")
    @classmethod
    def normalize_added_tags(cls, value: list[str] | None) -> list[str] | None:
        return _normalize_tags(value)

    @model_validator(mode="after")
    def require_changes(self):
        if all(
            value is None
            for value in (
                self.job_category,
                self.category_review_status,
                self.tags_to_add,
                self.organize_status,
            )
        ):
            raise ValueError("at least one organization change is required")
        if self.job_category is not None and self.category_review_status is None:
            self.category_review_status = "confirmed"
        return self


class WorkbenchCompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    aliases: list[str] | None
    notes: str | None


class WorkbenchApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    job_id: int
    status: ApplicationStatus
    notes: str | None
    status_updated_at: datetime
    version: int
    legacy_status: str | None = None

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value):
        from app.application_statuses import current_status
        return current_status(value)

    @field_serializer("status_updated_at")
    def serialize_datetime(self, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class WorkbenchJobItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    title: str
    location: str | None
    employment_type: str | None
    job_category: str
    category_review_status: WorkbenchCategoryReviewStatus
    tags: list[str]
    organize_status: WorkbenchOrganizeStatus
    salary_text: str | None
    notes: str | None
    description: str | None
    source_url: str | None
    source_website: str | None
    original_title: str | None
    published_at: date | None
    structured_data: dict[str, Any] | list[Any] | None
    created_at: datetime
    updated_at: datetime
    version: int
    company: WorkbenchCompanyOut
    application: WorkbenchApplicationOut | None
    direction: str = ""

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class WorkbenchJobListOut(BaseModel):
    items: list[WorkbenchJobItemOut]
    total: int
    facets: dict[str, list[dict[str, str | int]]]
    counts: dict[str, int]


class WorkbenchJobCreateOut(BaseModel):
    item: WorkbenchJobItemOut
    idempotent_replay: bool


class WorkbenchBulkOrganizeOut(BaseModel):
    updated_count: int
    items: list[dict[str, int]]


class WorkbenchPreferencesUpdate(WorkbenchStrictModel):
    theme_mode: Literal["light", "dark", "system"] | None = None
    seed_color: str | None = Field(default=None, max_length=7)
    density: Literal["comfortable", "compact"] | None = None
    reduce_motion: bool | None = None
    visible_job_fields: list[WorkbenchDisplayField] | None = None
    dismissed_page_hints: dict[Literal["pending", "follow", "mail"], bool] | None = None
    poetry_preset: Literal["random", "shuidiao", "dielianhua", "qingpingle", "yulinling", "jiangchengzi", "nian"] | None = None

    @field_validator("seed_color")
    @classmethod
    def validate_seed_color(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if len(value) != 7 or not value.startswith("#") or any(ch not in "0123456789abcdefABCDEF" for ch in value[1:]):
            raise ValueError("seed_color must be a six-digit hex color")
        return value.lower()

    @field_validator("visible_job_fields")
    @classmethod
    def dedupe_display_fields(cls, value: list[WorkbenchDisplayField] | None) -> list[WorkbenchDisplayField] | None:
        return list(dict.fromkeys(value)) if value is not None else value


class WorkbenchPreferencesOut(BaseModel):
    theme_mode: Literal["light", "dark", "system"]
    seed_color: str
    density: Literal["comfortable", "compact"]
    reduce_motion: bool
    visible_job_fields: list[WorkbenchDisplayField]
    dismissed_page_hints: dict[Literal["pending", "follow", "mail"], bool]
    poetry_preset: Literal["random", "shuidiao", "dielianhua", "qingpingle", "yulinling", "jiangchengzi", "nian"]


class SettingOut(SettingIn):
    model_config = ConfigDict(from_attributes=True)
    key: str
    updated_at: datetime

    @field_serializer("updated_at")
    def serialize_updated_at(self, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
