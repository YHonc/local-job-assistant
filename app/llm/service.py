from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ValidationError

from app.credentials import CredentialStore, CredentialStoreError
from sqlalchemy import select, update
from sqlalchemy.orm import Session as OrmSession
from app.database.models import LLMConfiguration, LLMRoutingSettings, LLMUsageEvent, now
from .proxy import ProxyConfigurationError, completion_client, resolve_proxy_url

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Base error for provider calls that fail without returning valid output."""

    code = "llm_provider_error"
    status_code = 502


class LLMConfigurationError(LLMError):
    """The configured provider or credential cannot be used."""

    code = "llm_configuration_error"
    status_code = 503


class LLMTimeoutError(LLMError):
    """The provider did not respond before the configured timeout."""

    code = "llm_timeout"
    status_code = 504


class LLMOutputError(LLMError):
    """The provider response failed structured-output validation."""

    code = "llm_invalid_output"
    status_code = 502


class LLMAuthenticationError(LLMError):
    code = "llm_auth_failed"


class LLMModelNotFoundError(LLMError):
    code = "llm_model_not_found"


class LLMRateLimitError(LLMError):
    code = "llm_rate_limited"
    status_code = 429


class LLMRequestRejectedError(LLMError):
    code = "llm_request_rejected"


class LLMConnectionError(LLMError):
    code = "llm_connection_failed"

class NoAvailableLLMError(LLMConfigurationError):
    code = "llm_no_available_model"

def select_config(db, config_id: int | None = None):
    """Resolve the configuration to use for one structured call.

    An explicit ``config_id`` always wins (and must be enabled). Otherwise the
    routing singleton decides: auto-select picks the enabled configuration with
    the fewest recorded calls (round-robin over time), and the fixed path uses
    ``default_config_id`` with a fallback to the first enabled configuration.
    """
    if config_id is not None:
        config = db.get(LLMConfiguration, config_id)
        if config is None or not config.enabled:
            raise NoAvailableLLMError("no enabled LLM configuration")
        return config

    routing = db.get(LLMRoutingSettings, 1)
    if routing and routing.auto_select_enabled:
        config = db.execute(
            select(LLMConfiguration)
            .where(LLMConfiguration.enabled.is_(True))
            .order_by(LLMConfiguration.call_count, LLMConfiguration.id)
        ).scalars().first()
    else:
        target = routing.default_config_id if routing and routing.default_config_id else None
        config = db.get(LLMConfiguration, target) if target is not None else None
        if config is None or not config.enabled:
            # 固定默认的模型被暂停/删除后，回退到最早的启用配置，
            # 与 routes 层 _current_config 的行为保持一致，避免邮件同步
            # 和默认提取路径在还有可用模型时直接失败。
            config = db.execute(
                select(LLMConfiguration)
                .where(LLMConfiguration.enabled.is_(True))
                .order_by(LLMConfiguration.id)
            ).scalars().first()
    if config is None:
        raise NoAvailableLLMError("no enabled LLM configuration")
    return config


def record_usage(
    db,
    config_id: int,
    success: bool,
    duration_ms: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    purpose: str = "business",
) -> None:
    """Update call counters on a separate session; usage stats must never break a call."""
    try:
        bind = db.get_bind()
        with OrmSession(bind=bind) as session:
            counter = "success_count" if success else "failure_count"
            recorded_at = now()
            duration_ms = max(0, int(duration_ms))
            prompt_tokens = max(0, int(prompt_tokens))
            completion_tokens = max(0, int(completion_tokens))
            result = session.execute(
                update(LLMConfiguration)
                .where(LLMConfiguration.id == config_id)
                .values(
                    call_count=LLMConfiguration.call_count + 1,
                    total_duration_ms=LLMConfiguration.total_duration_ms + max(0, int(duration_ms)),
                    total_prompt_tokens=LLMConfiguration.total_prompt_tokens + max(0, int(prompt_tokens)),
                    total_completion_tokens=LLMConfiguration.total_completion_tokens + max(0, int(completion_tokens)),
                    last_used_at=recorded_at,
                    **{counter: getattr(LLMConfiguration, counter) + 1},
                )
            )
            if result.rowcount:
                session.add(LLMUsageEvent(
                    config_id=config_id, recorded_at=recorded_at, success=success,
                    duration_ms=duration_ms, prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    purpose=purpose,
                ))
            session.commit()
    except Exception:
        logger.warning("failed to record usage for LLM configuration %s", config_id, exc_info=True)


SENSENOVA_HOSTS = {"token.sensenova.cn"}


def _api_host(config: Any) -> str | None:
    api_base = getattr(config, "api_base", None)
    if not api_base:
        return None
    try:
        return urlsplit(api_base).hostname
    except ValueError:
        return None


def structured_output_mode(config: Any) -> str:
    """Return the safe structured-output strategy for a configured endpoint."""

    # Messages-compatible gateways do not necessarily support Anthropic tools
    # or native structured outputs. Keep the schema in the prompt and validate
    # the response locally for this wire format.
    return "prompt_json" if _api_host(config) in SENSENOVA_HOSTS or config.provider.strip() == "anthropic" else "json_schema"


# Hosts whose gateway rejected the json_schema response_format remember the
# downgrade for the process lifetime, so later calls skip the doomed attempt.
_STRUCTURED_MODE_OVERRIDES: dict[str | None, str] = {}


def effective_structured_mode(config: Any) -> str:
    return _STRUCTURED_MODE_OVERRIDES.get(_api_host(config)) or structured_output_mode(config)


class LLMService:
    def __init__(
        self,
        config: Any,
        credential_store: CredentialStore,
        completion: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self.credential_store = credential_store
        self._custom_completion = completion is not None
        if completion is None:
            try:
                from litellm import completion as litellm_completion
            except Exception as exc:
                raise LLMConfigurationError("LLM backend unavailable") from exc
            completion = litellm_completion
        self.completion = completion
        # ``last_usage`` keeps its historical meaning: usage from the most
        # recently completed provider response.  ``request_usage`` is a
        # separate per-logical-request accumulator used by callers that need
        # to explain a json_schema -> prompt_json fallback without changing
        # existing usage consumers.
        self.last_usage: dict[str, int | None] | None = None
        self.request_usage: dict[str, int | bool | None] | None = None
        self.last_structured_mode = None

    @property
    def model_identifier(self) -> str:
        model = self.config.model.strip()
        provider = self.config.provider.strip()
        if provider in {"openai", "anthropic"}:
            return model if model.startswith(provider + "/") else f"{provider}/{model}"
        if "/" in model:
            return model
        if _api_host(self.config) in SENSENOVA_HOSTS:
            return f"openai/{model}"
        return f"{self.config.provider.strip()}/{model}"

    def structured_extract(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
        *,
        max_tokens: int | None = None,
    ) -> BaseModel:
        # A service instance may be reused by a caller. Clear per-request
        # state before credential lookup so an early configuration failure
        # cannot expose the previous request's telemetry.
        self.last_usage = None
        self.request_usage = None
        self.last_structured_mode = None
        api_key: str | None = None
        try:
            self._proxy_url = resolve_proxy_url(self.config)
        except ProxyConfigurationError as exc:
            raise LLMConfigurationError(str(exc)) from exc
        if self.config.credential_id:
            try:
                api_key = self.credential_store.get(self.config.credential_id)
            except CredentialStoreError as exc:
                raise LLMConfigurationError("credential store unavailable") from exc
            if not api_key:
                raise LLMConfigurationError("LLM credential is not configured")

        schema_name = re.sub(r"[^a-zA-Z0-9_-]", "_", schema.__name__) or "output"
        mode = effective_structured_mode(self.config)
        used_mode = mode
        # last_usage 在收到响应后立即记录；即使结构校验失败（LLMOutputError），
        # 该次调用的 token 也已消耗，调用方仍能拿到用量。
        self.request_usage = {
            "prompt_tokens": None,
            "completion_tokens": None,
            "provider_calls": 0,
            "usage_complete": False,
        }
        prompt_total = 0
        completion_total = 0
        prompt_known = True
        completion_known = True

        def record_request_usage(usage: dict[str, int | None] | None) -> None:
            nonlocal prompt_total, completion_total, prompt_known, completion_known
            if usage is None:
                prompt_known = False
                completion_known = False
                return
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            if prompt is None:
                prompt_known = False
            else:
                prompt_total += prompt
            if completion is None:
                completion_known = False
            else:
                completion_total += completion

        def update_request_usage() -> None:
            calls = int(self.request_usage.get("provider_calls", 0)) if self.request_usage else 0
            self.request_usage = {
                "prompt_tokens": prompt_total if prompt_known else None,
                "completion_tokens": completion_total if completion_known else None,
                "provider_calls": calls,
                "usage_complete": bool(calls and prompt_known and completion_known),
            }

        self._record_request_usage = record_request_usage
        self._update_request_usage = update_request_usage
        try:
            result = self._extract_once(
                mode, system_prompt, user_prompt, schema, schema_name, max_tokens, api_key
            )
        except LLMRequestRejectedError:
            if mode != "json_schema":
                raise
            # Some OpenAI-compatible gateways accept plain chat but reject the
            # json_schema response_format (observed on tokenrhythm.studio, which
            # answers 400 "This response_format type is unavailable now").
            # Retry once with the schema in the prompt — that works everywhere —
            # and remember the downgrade for this host so later calls skip the
            # doomed attempt entirely.
            used_mode = "prompt_json"
            result = self._extract_once(
                used_mode, system_prompt, user_prompt, schema, schema_name, max_tokens, api_key
            )
            _STRUCTURED_MODE_OVERRIDES[_api_host(self.config)] = used_mode
            logger.info(
                "structured output downgraded to prompt_json for host %s",
                _api_host(self.config),
            )
        self.last_structured_mode = used_mode
        update_request_usage()
        return result

    def _extract_once(
        self,
        mode: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
        schema_name: str,
        max_tokens: int | None,
        api_key: str | None,
    ) -> BaseModel:
        request_system_prompt = system_prompt
        if mode == "prompt_json":
            compact_schema = json.dumps(
                schema.model_json_schema(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            request_system_prompt = (
                f"{system_prompt}\nReturn exactly one JSON object with no Markdown or commentary. "
                f"The JSON must validate against this schema: {compact_schema}"
            )

        kwargs: dict[str, Any] = {
            "model": self.model_identifier,
            "messages": [
                {"role": "system", "content": request_system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.config.temperature,
            "timeout": self.config.timeout_seconds,
        }
        if mode == "json_schema":
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": schema.model_json_schema(),
                },
            }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        elif self.config.provider.strip() == "anthropic":
            kwargs["max_tokens"] = 4096
        if api_key:
            kwargs["api_key"] = api_key
        if self.config.api_base:
            base = self.config.api_base.rstrip("/")
            if self.config.provider.strip() == "anthropic":
                base = base.removesuffix("/v1/messages").removesuffix("/v1")
            elif self.config.provider.strip() == "openai":
                base = base.removesuffix("/chat/completions")
            kwargs["api_base"] = base

        if self.request_usage is not None:
            self.request_usage["provider_calls"] = int(self.request_usage.get("provider_calls", 0)) + 1
        try:
            if self._custom_completion:
                response = self.completion(**kwargs)
            else:
                with completion_client(self.config.provider.strip(), self._proxy_url, kwargs) as client:
                    if client is not None:
                        kwargs["client"] = client
                    response = self.completion(**kwargs)
        except Exception as exc:
            # A few SDKs attach a usage object to an exception (for example
            # when the gateway rejects only the response format). Preserve it
            # for the request accumulator when available; unknown usage stays
            # unknown instead of being fabricated as zero.
            self._record_request_usage(self._exception_usage(exc))
            self._update_request_usage()
            raise self._classified_provider_error(exc) from exc

        self.last_usage = self._response_usage(response)
        self._record_request_usage(self._nullable_response_usage(response))
        self._update_request_usage()
        try:
            payload = self._response_payload(response)
            return schema.model_validate(payload)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, ValidationError) as exc:
            raise LLMOutputError("invalid structured LLM output") from exc

    @staticmethod
    def _usage_object(response: Any) -> Any:
        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")
        return usage

    @staticmethod
    def _response_usage(response: Any) -> dict[str, int] | None:
        """Extract prompt/completion token counts from a provider response."""
        usage = LLMService._usage_object(response)
        if usage is None:
            return None

        def pick(name: str) -> int:
            value = getattr(usage, name, None)
            if value is None and isinstance(usage, dict):
                value = usage.get(name)
            try:
                return max(0, int(value)) if value is not None else 0
            except (OverflowError, TypeError, ValueError):
                return 0

        prompt = pick("prompt_tokens")
        completion = pick("completion_tokens")
        if prompt or completion:
            return {"prompt_tokens": prompt, "completion_tokens": completion}
        return None

    @staticmethod
    def _strict_token(value: Any) -> int | None:
        """Return only a finite, non-negative integer token count."""
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, str) and value.strip().isdigit():
            try:
                return int(value.strip())
            except (OverflowError, ValueError):
                return None
        return None

    @staticmethod
    def _nullable_usage(usage: Any) -> dict[str, int | None] | None:
        if usage is None:
            return None

        def pick(name: str) -> int | None:
            value = getattr(usage, name, None)
            if value is None and isinstance(usage, dict):
                value = usage.get(name)
            return LLMService._strict_token(value)

        return {
            "prompt_tokens": pick("prompt_tokens"),
            "completion_tokens": pick("completion_tokens"),
        }

    @staticmethod
    def _nullable_response_usage(response: Any) -> dict[str, int | None] | None:
        """Extract nullable token counts for request-level telemetry."""
        return LLMService._nullable_usage(LLMService._usage_object(response))

    @staticmethod
    def _exception_usage(error: BaseException) -> dict[str, int | None] | None:
        """Read usage attached to provider exceptions without exposing details."""
        candidates: list[Any] = [error]
        response = getattr(error, "response", None)
        if response is not None:
            candidates.append(response)
        body = getattr(error, "body", None)
        if body is not None:
            candidates.append(body)
        for candidate in candidates:
            usage = getattr(candidate, "usage", None)
            if usage is None and isinstance(candidate, dict):
                usage = candidate.get("usage")
            if usage is None:
                continue

            return LLMService._nullable_usage(usage)
        return None

    def extract_job(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> BaseModel:
        return self.structured_extract(system_prompt, user_prompt, schema)

    def classify_email(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> BaseModel:
        return self.structured_extract(system_prompt, user_prompt, schema)

    def extract_recruitment_event(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> BaseModel:
        return self.structured_extract(system_prompt, user_prompt, schema)

    extract = structured_extract

    @staticmethod
    def _classified_provider_error(exc: Exception) -> LLMError:
        error_name = exc.__class__.__name__.casefold()
        if isinstance(exc, TimeoutError) or "timeout" in error_name:
            return LLMTimeoutError("LLM request timed out")

        raw_status = None
        for value in (
            getattr(exc, "status_code", None),
            getattr(exc, "http_status", None),
            getattr(getattr(exc, "response", None), "status_code", None),
        ):
            try:
                raw_status = int(value)
                break
            except (TypeError, ValueError):
                continue

        if raw_status in {401, 403} or any(
            token in error_name for token in ("authentication", "permission", "unauthorized")
        ):
            return LLMAuthenticationError("LLM provider authentication failed")
        if raw_status == 404 or "notfound" in error_name or "not_found" in error_name:
            return LLMModelNotFoundError("LLM model or endpoint was not found")
        if raw_status == 429 or "ratelimit" in error_name or "rate_limit" in error_name:
            return LLMRateLimitError("LLM provider rate limit reached")
        if raw_status in {400, 409, 415, 422} or "badrequest" in error_name:
            return LLMRequestRejectedError(
                "LLM provider rejected the request; verify model and compatibility settings"
            )
        if any(token in error_name for token in ("connection", "network", "socket", "proxy")):
            return LLMConnectionError("LLM provider connection failed")
        return LLMError("LLM provider request failed")

    @staticmethod
    def _response_payload(response: Any) -> Any:
        choices = response.choices if hasattr(response, "choices") else response["choices"]
        choice = choices[0]
        message = choice.message if hasattr(choice, "message") else choice["message"]

        parsed = getattr(message, "parsed", None)
        if parsed is None and isinstance(message, dict):
            parsed = message.get("parsed")
        if parsed is not None:
            return parsed.model_dump() if isinstance(parsed, BaseModel) else parsed

        content = message.content if hasattr(message, "content") else message["content"]
        if isinstance(content, BaseModel):
            return content.model_dump()
        if isinstance(content, dict):
            return content.get("parsed", content)
        if isinstance(content, list):
            text_parts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") in {None, "text"}
            ]
            content = "".join(text_parts)
        if isinstance(content, str):
            text = content.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                text = "\n".join(lines).strip()
            return json.loads(text)
        raise TypeError("unsupported structured response content")
