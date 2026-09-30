"""Request-local transports; never mutate environment or LiteLLM global clients.

Uses HTTPX's upstream proxy implementation:
https://github.com/encode/httpx/blob/0.28.1/docs/advanced/proxies.md
LiteLLM accepts an OpenAI SDK client or HTTPHandler depending on the protocol:
https://github.com/BerriAI/litellm/tree/main/litellm/llms/custom_httpx
"""
from contextlib import contextmanager
from urllib.parse import urlsplit

import httpx
from sqlalchemy import inspect

from app.database.models import LLMProxySettings


PROXY_PROVIDERS = {"openai", "anthropic", "gemini"}


class ProxyConfigurationError(ValueError):
    pass


def normalize_proxy_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https", "socks5", "socks5h"}
            and parsed.hostname and parsed.port and 1 <= parsed.port <= 65535
            and parsed.username is None and parsed.password is None
            and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
            and not any(char.isspace() or ord(char) < 32 for char in value)
            and "\\" not in value
        )
    except ValueError:
        valid = False
    if not valid:
        raise ProxyConfigurationError("代理地址须为 http/https/socks5/socks5h://主机:端口，且不含账号、密码或路径")
    return value.rstrip("/")


def saved_proxy_url(db) -> str | None:
    settings = db.get(LLMProxySettings, 1) if db is not None else None
    return settings.proxy_url if settings else None


def resolve_proxy_url(config, db=None) -> str | None:
    if not getattr(config, "use_proxy", False):
        return None
    if config.provider.strip() not in PROXY_PROVIDERS:
        raise ProxyConfigurationError("此 Provider 暂不支持代理；请改用服务支持的 OpenAI、Anthropic 或 Gemini API 格式")
    # Health checks carry a snapshot after closing their database session.
    if hasattr(config, "proxy_url"):
        value = config.proxy_url
    else:
        state = inspect(config, raiseerr=False)
        db = db if db is not None else state.session if state is not None else None
        value = saved_proxy_url(db)
    value = normalize_proxy_url(value)
    if not value:
        raise ProxyConfigurationError("请先在模型设置中保存统一的本地代理地址")
    return value


@contextmanager
def completion_client(provider: str, proxy_url: str | None, kwargs: dict):
    """Own and close the client for one synchronous completion, including errors."""
    if provider not in PROXY_PROVIDERS:
        # Preserve the SDK path for other LiteLLM providers. Proxy opt-in is
        # rejected above rather than silently ignored by an unsupported SDK.
        yield None
        return
    with httpx.Client(proxy=proxy_url, trust_env=False, timeout=kwargs["timeout"]) as http:
        if provider == "openai":
            from openai import OpenAI

            with OpenAI(api_key=kwargs.get("api_key") or "not-required",
                        base_url=kwargs.get("api_base") or "https://api.openai.com/v1",
                        timeout=kwargs["timeout"], max_retries=0, http_client=http) as client:
                yield client
        else:
            from litellm.llms.custom_httpx.http_handler import HTTPHandler

            yield HTTPHandler(client=http, timeout=kwargs["timeout"])
