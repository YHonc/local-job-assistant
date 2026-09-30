from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_serializer,
    field_validator,
    model_validator,
)

from .proxy import normalize_proxy_url


def _trim_nonblank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class LLMConfigPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(max_length=100)
    model: str = Field(max_length=200)
    api_base: str | None = Field(default=None, max_length=1000)
    temperature: float = Field(default=0.2, ge=0, le=2)
    timeout_seconds: int = Field(default=60, ge=5, le=300)
    api_key: SecretStr | None = None
    clear_api_key: bool = False
    use_proxy: bool = False

    _trim_identifiers = field_validator("provider", "model")(_trim_nonblank)

    @field_validator("api_base")
    @classmethod
    def validate_api_base(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("api_base must be an http/https URL")
        if parsed.username or parsed.password:
            raise ValueError("api_base must not contain user information")
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("api_base has an invalid port") from exc
        return value.rstrip("/")

    @field_validator("api_key")
    @classmethod
    def reject_blank_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("api_key must not be blank")
        return value

    @model_validator(mode="after")
    def validate_key_action(self) -> "LLMConfigPut":
        if self.api_key is not None and self.clear_api_key:
            raise ValueError("api_key and clear_api_key are mutually exclusive")
        return self


class LLMConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    configured: bool
    provider: str | None = None
    model: str | None = None
    api_base: str | None = None
    temperature: float | None = None
    timeout_seconds: int | None = None
    has_api_key: bool = False
    use_proxy: bool = False
    structured_output_mode: Literal["json_schema", "prompt_json"] | None = None
    updated_at: datetime | None = None

    @field_serializer("updated_at")
    def serialize_updated_at(self, value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)


class LLMTestOut(BaseModel):
    ok: bool
    provider: str
    model: str
    structured_output_mode: Literal["json_schema", "prompt_json"]
    elapsed_ms: int = Field(ge=0)

class ModelHealthSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["inherit", "custom", "off"] = "inherit"
    interval_minutes: int = Field(default=30, ge=1, le=1440)


class LLMConfigCreate(LLMConfigPut):
    display_name: str = Field(default="新配置", max_length=200)
    enabled: bool = True
    health: ModelHealthSettings | None = None

    _trim_display_name = field_validator("display_name")(_trim_nonblank)

class LLMConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=200)
    provider: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    api_base: str | None = Field(default=None, max_length=1000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    timeout_seconds: int | None = Field(default=None, ge=5, le=300)
    enabled: bool | None = None
    use_proxy: bool = False
    api_key: SecretStr | None = None
    clear_api_key: bool = False
    health: ModelHealthSettings | None = None

    @field_validator("provider", "model", "display_name")
    @classmethod
    def trim_identifiers(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _trim_nonblank(value)

    @field_validator("api_base")
    @classmethod
    def validate_api_base(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("api_base must be an http/https URL")
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("api_base has an invalid port") from exc
        return value.rstrip("/")

    @field_validator("api_key")
    @classmethod
    def reject_blank_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("api_key must not be blank")
        return value

    @model_validator(mode="after")
    def validate_key_action(self) -> "LLMConfigPatch":
        if self.api_key is not None and self.clear_api_key:
            raise ValueError("api_key and clear_api_key are mutually exclusive")
        return self

class LLMConfigSummary(LLMConfigOut):
    id: int
    display_name: str
    enabled: bool
    call_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    average_duration_ms: float = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    last_used_at: datetime | None = None
    created_at: datetime | None = None
    health: dict = Field(default_factory=dict)

    @field_serializer("last_used_at", "created_at")
    def serialize_datetimes(self, value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)

class RoutingOut(BaseModel):
    auto_select_enabled: bool
    default_config_id: int | None
    email_config_id: int | None
    max_parallel_calls: int


class ProxySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    proxy_url: str | None = Field(default=None, max_length=1000)

    _normalize_url = field_validator("proxy_url")(normalize_proxy_url)

class RoutingPut(BaseModel):
    auto_select_enabled: bool | None = None
    default_config_id: int | None = None
    email_config_id: int | None = None
    max_parallel_calls: int = Field(default=1, ge=1, le=8)


class LLMModelProbeIn(BaseModel):
    """Ad-hoc model-list lookup using dialog values; the key is never stored."""

    model_config = ConfigDict(extra="forbid")

    provider: str | None = Field(default=None, max_length=100)
    api_base: str = Field(min_length=1, max_length=1000)
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=2000)
    config_id: int | None = None
    use_proxy: bool | None = None
    timeout_seconds: int = Field(default=15, ge=3, le=30)

    @field_validator("provider")
    @classmethod
    def trim_provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("api_base")
    @classmethod
    def validate_api_base(cls, value: str) -> str:
        value = value.strip()
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("api_base must be an http/https URL")
        if parsed.username or parsed.password:
            raise ValueError("api_base must not contain user information")
        return value.rstrip("/")


class LLMModelProbeOut(BaseModel):
    models: list[str] = Field(default_factory=list)
