from __future__ import annotations

from types import SimpleNamespace
import httpx
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, object_session

from app.api.guards import require_credential_store, restore_secret
from app.credentials import CredentialStore, CredentialStoreError
from app.database.models import LLMConfiguration, LLMRoutingSettings, LLMProxySettings
from app.database.session import get_db

from .schemas import (
    LLMConfigCreate,
    LLMConfigOut,
    LLMConfigPatch,
    LLMConfigPut,
    LLMConfigSummary,
    LLMModelProbeIn,
    LLMModelProbeOut,
    LLMTestOut,
    RoutingOut,
    RoutingPut,
    ProxySettings,
)
from .proxy import ProxyConfigurationError, resolve_proxy_url, saved_proxy_url
from .service import effective_structured_mode
from .usage import UsageSeries, usage_series
from .health import router as health_router, health_summary, save_model_health


router = APIRouter(prefix="/api/llm", tags=["llm"])
router.include_router(health_router)
DEFAULT_CREDENTIAL_ID = "llm:default"


def _config_response(
    config: LLMConfiguration | None,
    store: CredentialStore,
) -> LLMConfigOut:
    if config is None:
        return LLMConfigOut(configured=False)

    has_api_key = False
    if config.credential_id:
        try:
            has_api_key = bool(store.get(config.credential_id))
        except CredentialStoreError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="credential store unavailable",
            ) from exc

    return LLMConfigOut(
        configured=True,
        provider=config.provider,
        model=config.model,
        api_base=config.api_base,
        use_proxy=config.use_proxy,
        temperature=config.temperature,
        timeout_seconds=config.timeout_seconds,
        has_api_key=has_api_key,
        structured_output_mode=effective_structured_mode(config),
        updated_at=config.updated_at,
    )


def _summary(config: LLMConfiguration, store: CredentialStore) -> LLMConfigSummary:
    values = _config_response(config, store).model_dump()
    values.update(
        id=config.id,
        display_name=config.display_name,
        enabled=config.enabled,
        call_count=config.call_count,
        success_count=config.success_count,
        failure_count=config.failure_count,
        average_duration_ms=(config.total_duration_ms / config.call_count if config.call_count else 0),
        total_prompt_tokens=config.total_prompt_tokens,
        total_completion_tokens=config.total_completion_tokens,
        last_used_at=config.last_used_at,
        created_at=config.created_at,
        health=health_summary(object_session(config), config),
    )
    return LLMConfigSummary(**values)


def _current_config(db: Session) -> LLMConfiguration | None:
    routing = db.get(LLMRoutingSettings, 1)
    if routing and routing.default_config_id:
        config = db.get(LLMConfiguration, routing.default_config_id)
        if config is not None and config.enabled:
            return config
    config = db.get(LLMConfiguration, 1)
    if config is not None and config.enabled:
        return config
    return (
        db.query(LLMConfiguration)
        .filter(LLMConfiguration.enabled.is_(True))
        .order_by(LLMConfiguration.id)
        .first()
    )


def _first_enabled_config(db: Session, *, exclude_id: int | None = None) -> LLMConfiguration | None:
    query = db.query(LLMConfiguration).filter(LLMConfiguration.enabled.is_(True))
    if exclude_id is not None:
        query = query.filter(LLMConfiguration.id != exclude_id)
    return query.order_by(LLMConfiguration.id).first()


@router.get("/configs", response_model=list[LLMConfigSummary])
def list_configs(request: Request, db: Session = Depends(get_db)) -> list[LLMConfigSummary]:
    store = require_credential_store(request)
    configs = db.query(LLMConfiguration).order_by(LLMConfiguration.id).all()
    return [_summary(config, store) for config in configs]


@router.get("/configs/{config_id}/usage", response_model=UsageSeries)
def get_usage_series(config_id: int, start: date | None = None, end: date | None = None,
                     db: Session = Depends(get_db)) -> UsageSeries:
    return usage_series(db, config_id, start, end)


@router.post("/configs", response_model=LLMConfigSummary, status_code=201)
def create_config(
    payload: LLMConfigCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> LLMConfigSummary:
    store = require_credential_store(request)
    _validate_proxy_choice(payload.provider, payload.use_proxy, db)
    config = LLMConfiguration(
        display_name=payload.display_name,
        enabled=payload.enabled,
        provider=payload.provider,
        model=payload.model,
        api_base=payload.api_base,
        use_proxy=payload.use_proxy,
        temperature=payload.temperature,
        timeout_seconds=payload.timeout_seconds,
        credential_id=None,
    )
    db.add(config)
    db.flush()
    if payload.health is not None:
        save_model_health(db, config, payload.health)
    old_secret = None
    if payload.api_key:
        config.credential_id = f"llm:{config.id}"
        try:
            store.set(config.credential_id, payload.api_key.get_secret_value())
        except CredentialStoreError as exc:
            db.rollback()
            raise HTTPException(503, "credential store unavailable") from exc
    try:
        db.commit()
        db.refresh(config)
    except Exception as exc:
        db.rollback()
        rollback_ok = True
        if config.credential_id:
            rollback_ok = restore_secret(store, config.credential_id, old_secret)
        detail = "unable to save LLM configuration"
        if not rollback_ok:
            detail += "; credential rollback failed"
        raise HTTPException(500, detail) from exc
    return _summary(config, store)


@router.get("/configs/{config_id}", response_model=LLMConfigSummary)
def get_config_by_id(
    config_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> LLMConfigSummary:
    config = db.get(LLMConfiguration, config_id)
    if config is None:
        raise HTTPException(404, "configuration not found")
    return _summary(config, require_credential_store(request))


@router.put("/configs/{config_id}", response_model=LLMConfigSummary)
def update_config(
    config_id: int,
    payload: LLMConfigPatch,
    request: Request,
    db: Session = Depends(get_db),
) -> LLMConfigSummary:
    config = db.get(LLMConfiguration, config_id)
    if config is None:
        raise HTTPException(404, "configuration not found")
    data = payload.model_dump(exclude_unset=True, exclude={"api_key", "clear_api_key", "health"})
    _validate_proxy_choice(data.get("provider") or config.provider, data.get("use_proxy", config.use_proxy), db)
    old_values = {key: getattr(config, key) for key in data}
    old_credential_id = config.credential_id
    store = require_credential_store(request)
    key_action = payload.api_key is not None or payload.clear_api_key
    credential_id = config.credential_id or f"llm:{config.id}"
    old_secret = None
    try:
        if key_action and config.credential_id:
            old_secret = store.get(credential_id)
        for key, value in data.items():
            setattr(config, key, value)
        if payload.health is not None:
            save_model_health(db, config, payload.health)
        if payload.api_key:
            config.credential_id = credential_id
            store.set(credential_id, payload.api_key.get_secret_value())
        elif payload.clear_api_key and config.credential_id:
            store.delete(config.credential_id)
            config.credential_id = None
        db.commit()
        db.refresh(config)
    except CredentialStoreError as exc:
        db.rollback()
        raise HTTPException(503, "credential store unavailable") from exc
    except Exception as exc:
        db.rollback()
        for key, value in old_values.items():
            setattr(config, key, value)
        config.credential_id = old_credential_id
        rollback_ok = restore_secret(store, credential_id, old_secret) if key_action else True
        detail = "unable to save LLM configuration"
        if not rollback_ok:
            detail += "; credential rollback failed"
        raise HTTPException(500, detail) from exc
    return _summary(config, store)


@router.delete("/configs/{config_id}", status_code=204)
def delete_config_by_id(
    config_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    config = db.get(LLMConfiguration, config_id)
    if config is None:
        raise HTTPException(404, "configuration not found")
    store = require_credential_store(request)
    credential_id = config.credential_id
    try:
        old_secret = store.get(credential_id) if credential_id else None
        if credential_id:
            store.delete(credential_id)
    except CredentialStoreError as exc:
        raise HTTPException(503, "credential store unavailable") from exc
    routing = db.get(LLMRoutingSettings, 1)
    was_default = routing is not None and routing.default_config_id == config.id
    was_email = routing is not None and routing.email_config_id == config.id
    db.delete(config)
    if routing is not None and (was_default or was_email):
        replacement = _first_enabled_config(db, exclude_id=config.id)
        if was_default:
            routing.default_config_id = replacement.id if replacement else None
        # An explicit email pin is cleared rather than silently swapped.
        if was_email:
            routing.email_config_id = None
        db.add(routing)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        rollback_ok = restore_secret(store, credential_id, old_secret) if credential_id else True
        detail = "unable to delete LLM configuration"
        if not rollback_ok:
            detail += "; credential rollback failed"
        raise HTTPException(500, detail) from exc


def _validate_proxy_choice(provider: str, use_proxy: bool, db: Session) -> str | None:
    try:
        return resolve_proxy_url(SimpleNamespace(provider=provider, use_proxy=use_proxy, proxy_url=saved_proxy_url(db)))
    except ProxyConfigurationError as exc:
        raise HTTPException(422, {"code": "llm_proxy_configuration_error", "message": str(exc)}) from exc


@router.get("/proxy", response_model=ProxySettings)
def get_proxy(db: Session = Depends(get_db)) -> ProxySettings:
    return ProxySettings(proxy_url=saved_proxy_url(db))


@router.put("/proxy", response_model=ProxySettings)
def put_proxy(payload: ProxySettings, db: Session = Depends(get_db)) -> ProxySettings:
    if not payload.proxy_url and db.query(LLMConfiguration).filter(LLMConfiguration.use_proxy.is_(True)).first():
        raise HTTPException(409, "请先关闭所有模型的代理开关，再清除统一代理地址")
    settings = db.get(LLMProxySettings, 1) or LLMProxySettings(id=1)
    settings.proxy_url = payload.proxy_url
    db.add(settings)
    db.commit()
    return payload


@router.get("/routing", response_model=RoutingOut)
def get_routing(db: Session = Depends(get_db)) -> LLMRoutingSettings:
    routing = db.get(LLMRoutingSettings, 1)
    if routing is not None:
        return routing
    default_config = (
        db.query(LLMConfiguration)
        .filter(LLMConfiguration.enabled.is_(True))
        .order_by(LLMConfiguration.id)
        .first()
    )
    routing = LLMRoutingSettings(
        id=1,
        default_config_id=default_config.id if default_config is not None else None,
    )
    db.add(routing)
    db.commit()
    db.refresh(routing)
    return routing


@router.put("/routing", response_model=RoutingOut)
def put_routing(payload: RoutingPut, db: Session = Depends(get_db)) -> LLMRoutingSettings:
    routing = db.get(LLMRoutingSettings, 1) or LLMRoutingSettings(id=1)
    values = payload.model_dump(exclude_unset=True)
    for field in ("default_config_id", "email_config_id"):
        if values.get(field) is not None:
            target = db.get(LLMConfiguration, values[field])
            if target is None or not target.enabled:
                raise HTTPException(422, f"{field.removesuffix('_id')} configuration must exist and be enabled")
    for key, value in values.items():
        setattr(routing, key, value)
    if "default_config_id" not in values and routing.default_config_id:
        target = db.get(LLMConfiguration, routing.default_config_id)
        if target is None or not target.enabled:
            replacement = _first_enabled_config(db)
            routing.default_config_id = replacement.id if replacement else None
    if "email_config_id" not in values and routing.email_config_id:
        target = db.get(LLMConfiguration, routing.email_config_id)
        if target is None or not target.enabled:
            routing.email_config_id = None
    db.add(routing)
    db.commit()
    db.refresh(routing)
    return routing


@router.get("/config", response_model=LLMConfigOut)
def get_config(
    request: Request,
    db: Session = Depends(get_db),
) -> LLMConfigOut:
    return _config_response(_current_config(db), require_credential_store(request))


@router.put("/config", response_model=LLMConfigOut)
def put_config(
    payload: LLMConfigPut,
    request: Request,
    db: Session = Depends(get_db),
) -> LLMConfigOut:
    store = require_credential_store(request)
    config = _current_config(db)
    use_proxy = payload.use_proxy if "use_proxy" in payload.model_fields_set or config is None else config.use_proxy
    _validate_proxy_choice(payload.provider, use_proxy, db)
    old_credential_id = config.credential_id if config else None

    credential_action: str | None = None
    if payload.api_key is not None:
        credential_action = "set"
    elif payload.clear_api_key:
        credential_action = "clear"

    action_credential_id = old_credential_id or DEFAULT_CREDENTIAL_ID
    old_secret: str | None = None
    if credential_action:
        try:
            old_secret = store.get(action_credential_id)
            if credential_action == "set":
                store.set(action_credential_id, payload.api_key.get_secret_value())
            else:
                store.delete(action_credential_id)
        except CredentialStoreError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="credential store unavailable",
            ) from exc

    if config is None:
        # 没有任何启用配置时才走到这里：复用既有的 id=1 行（哪怕已停用），
        # 避免与它主键冲突；旧接口的语义是保存"唯一"配置，因此同时重新启用。
        config = db.get(LLMConfiguration, 1) or LLMConfiguration(id=1)
        config.enabled = True
    config.provider = payload.provider
    config.model = payload.model
    config.api_base = payload.api_base
    config.use_proxy = use_proxy
    config.temperature = payload.temperature
    config.timeout_seconds = payload.timeout_seconds
    if credential_action == "set":
        config.credential_id = action_credential_id
    elif credential_action == "clear":
        config.credential_id = None

    try:
        db.add(config)
        db.commit()
        db.refresh(config)
    except Exception as exc:
        db.rollback()
        rollback_ok = True
        if credential_action:
            rollback_ok = restore_secret(store, action_credential_id, old_secret)
        detail = "unable to save LLM configuration"
        if not rollback_ok:
            detail += "; credential rollback failed"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail,
        ) from exc

    return _config_response(config, store)


@router.post("/test", response_model=LLMTestOut)
def test_config(
    request: Request,
    db: Session = Depends(get_db),
) -> LLMTestOut:
    config = _current_config(db)
    return _run_test(config, request, db)


def _run_test(config: LLMConfiguration | None, request: Request, db: Session) -> LLMTestOut:
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "llm_not_configured",
                "message": "LLM is not configured",
            },
        )

    require_credential_store(request)
    return request.app.state.model_health.test(config.id, completion=request.app.state.llm_completion)


@router.post("/configs/{config_id}/test", response_model=LLMTestOut)
def test_config_by_id(
    config_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> LLMTestOut:
    config = db.get(LLMConfiguration, config_id)
    if config is None or not config.enabled:
        raise HTTPException(404, "configuration not found")
    return _run_test(config, request, db)


def _probe_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _list_remote_models(provider: str, api_base: str, api_key: str | None, timeout: int, proxy_url: str | None = None) -> list[str]:
    """Fetch available model IDs from the provider's list endpoint.

    The provider value decides the wire format; everything OpenAI-compatible
    (the vast majority, including SenseNova) uses GET {base}/models.
    """
    headers = {"Accept": "application/json"}
    api_base = api_base.rstrip("/")
    if provider == "anthropic":
        api_base = api_base.removesuffix("/v1/messages").removesuffix("/v1")
    elif provider == "openai":
        api_base = api_base.removesuffix("/chat/completions")
    url = f"{api_base}/models"
    if provider == "anthropic":
        url = f"{api_base}/v1/models"
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    elif provider == "gemini":
        if api_key:
            headers["x-goog-api-key"] = api_key
    elif provider == "ollama" and not api_base.endswith("/v1"):
        url = f"{api_base}/api/tags"
    else:
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

    try:
        with httpx.Client(proxy=proxy_url, trust_env=False, timeout=timeout, follow_redirects=True) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("invalid model list")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            raise _probe_error(401, "probe_auth_failed", "服务拒绝了密钥，请核对 API Key") from exc
        if exc.response.status_code == 404:
            raise _probe_error(404, "probe_not_found", "该地址没有提供模型列表接口") from exc
        if exc.response.status_code == 429:
            raise _probe_error(429, "probe_rate_limited", "服务限流，请稍后重试") from exc
        raise _probe_error(502, "probe_http_error", f"服务返回错误（{exc.response.status_code}）") from exc
    except (httpx.RequestError, TimeoutError, OSError, ValueError) as exc:
        raise _probe_error(502, "probe_connection_failed", "无法连接服务，请核对 API 地址、本地代理与网络") from exc

    models: set[str] = set()
    for item in body.get("data") or body.get("models") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("id") or item.get("name") or item.get("model")
        if value:
            models.add(str(value).removeprefix("models/"))
    return sorted(models)


@router.post("/models-probe", response_model=LLMModelProbeOut)
def probe_models(
    payload: LLMModelProbeIn,
    request: Request,
    db: Session = Depends(get_db),
) -> LLMModelProbeOut:
    """List model IDs for the values currently typed in the config dialog.

    The API key is used for this single call only (in-memory) and never stored;
    when omitted, the saved credential of ``config_id`` is reused.
    """
    api_key = None
    config = db.get(LLMConfiguration, payload.config_id) if payload.config_id is not None else None
    if payload.config_id is not None and config is None:
        raise HTTPException(404, "configuration not found")
    provider = payload.provider or (config.provider if config else "openai")
    use_proxy = payload.use_proxy if payload.use_proxy is not None else bool(config and config.use_proxy)
    proxy_url = _validate_proxy_choice(provider, use_proxy, db)
    if payload.api_key is not None:
        api_key = payload.api_key.get_secret_value()
    elif payload.config_id is not None:
        config = db.get(LLMConfiguration, payload.config_id)
        if config is not None and config.credential_id:
            try:
                api_key = require_credential_store(request).get(config.credential_id) or None
            except CredentialStoreError as exc:
                raise HTTPException(503, "credential store unavailable") from exc
    models = _list_remote_models(
        provider,
        payload.api_base,
        api_key,
        payload.timeout_seconds,
        proxy_url=proxy_url,
    )
    return LLMModelProbeOut(models=models[:500])


@router.delete("/config", status_code=status.HTTP_204_NO_CONTENT)
def delete_config(
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    store = require_credential_store(request)
    config = _current_config(db)
    credential_id = (
        config.credential_id if config and config.credential_id else DEFAULT_CREDENTIAL_ID
    )

    try:
        old_secret = store.get(credential_id)
        store.delete(credential_id)
    except CredentialStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="credential store unavailable",
        ) from exc

    if config is None:
        return

    try:
        db.delete(config)
        routing = db.get(LLMRoutingSettings, 1)
        if routing and routing.default_config_id == config.id:
            replacement = _first_enabled_config(db, exclude_id=config.id)
            routing.default_config_id = replacement.id if replacement else None
        db.commit()
    except Exception as exc:
        db.rollback()
        rollback_ok = restore_secret(store, credential_id, old_secret)
        detail = "unable to delete LLM configuration"
        if not rollback_ok:
            detail += "; credential rollback failed"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail,
        ) from exc
