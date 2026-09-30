"""Single-process, bounded and staggered connection checks, shared by UI and scheduler."""
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import perf_counter
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import LLMConfiguration, LLMHealthState, Setting
from app.database.session import get_db
from .schemas import LLMTestOut, ModelHealthSettings
from .service import LLMError, LLMService, effective_structured_mode, record_usage

router = APIRouter()


class HealthSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    interval_minutes: int = Field(default=30, ge=1, le=1440)


class ConnectionProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"]


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def settings_for(db):
    setting = db.get(Setting, "llm_health_settings")
    try:
        return HealthSettings.model_validate_json(setting.value) if setting else HealthSettings()
    except (ValueError, TypeError):
        return HealthSettings()


def interval_for(settings, health):
    return health.interval_minutes if health.mode == "custom" and health.interval_minutes else settings.interval_minutes


def health_summary(db, config):
    health = db.get(LLMHealthState, config.id)
    settings = settings_for(db)
    if health is None:
        return {"mode": "inherit", "interval_minutes": settings.interval_minutes, "status": "never", "next_test_at": None}
    active = settings.enabled and config.enabled and health.mode != "off"
    return {"mode": health.mode, "interval_minutes": interval_for(settings, health), "status": health.status,
            "error": health.error, "elapsed_ms": health.elapsed_ms, "last_test_at": utc(health.last_test_at),
            "next_test_at": utc(health.next_test_at) if active else None, "failures": health.failures}


def save_model_health(db, config, values: ModelHealthSettings):
    health = db.get(LLMHealthState, config.id)
    if health is None:
        health = LLMHealthState(config_id=config.id)
        db.add(health)
    health.mode = values.mode
    health.interval_minutes = values.interval_minutes
    health.next_test_at = datetime.now(timezone.utc) + timedelta(minutes=interval_for(settings_for(db), health))


@router.get("/health-settings", response_model=HealthSettings)
def get_settings(db: Session = Depends(get_db)):
    return settings_for(db)


@router.put("/health-settings", response_model=HealthSettings)
def put_settings(payload: HealthSettings, request: Request, db: Session = Depends(get_db)):
    db.merge(Setting(key="llm_health_settings", value=payload.model_dump_json()))
    db.commit()
    request.app.state.model_health.reset_schedule()
    return payload


class ModelHealthCoordinator:
    def __init__(self, factory, store, completion=None):
        self.factory, self.store, self.completion = factory, store, completion
        self._guard = Lock()
        self._running = set()

    def reset_schedule(self):
        """Restart from now, never replay missed ticks or mark interrupted calls successful."""
        with self.factory() as db:
            settings = settings_for(db)
            for index, config in enumerate(db.scalars(select(LLMConfiguration).order_by(LLMConfiguration.id))):
                health = db.get(LLMHealthState, config.id)
                if health is None:
                    if not settings.enabled:
                        continue
                    health = LLMHealthState(config_id=config.id, mode="inherit", failures=0, status="never")
                    db.add(health)
                with self._guard:
                    running = config.id in self._running
                if health.status == "running" and not running:
                    health.status, health.error = "interrupted", "服务重启，上一轮测试未完成"
                active = settings.enabled and config.enabled and health.mode != "off"
                health.next_test_at = datetime.now(timezone.utc) + timedelta(minutes=interval_for(settings, health), seconds=index * 15) if active else None
            db.commit()

    def run_once(self):
        with self.factory() as db:
            settings = settings_for(db)
            if not settings.enabled:
                return
            current = datetime.now(timezone.utc)
            due = []
            for config in db.scalars(select(LLMConfiguration).where(LLMConfiguration.enabled.is_(True)).order_by(LLMConfiguration.id)):
                health = db.get(LLMHealthState, config.id)
                if health is None:
                    health = LLMHealthState(config_id=config.id, mode="inherit", next_test_at=current + timedelta(minutes=settings.interval_minutes))
                    db.add(health)
                elif health.mode != "off" and (health.next_test_at is None or utc(health.next_test_at) <= current):
                    due.append((utc(health.next_test_at) or current, config.id))
            db.commit()
        # One due configuration per 15-second tick; all provider calls are bounded.
        for _, config_id in sorted(due):
            try:
                self.test(config_id, automatic=True)
            except HTTPException:
                pass  # Redacted failure is persisted by test().
            break

    def test(self, config_id, automatic=False, completion=None):
        with self._guard:
            if config_id in self._running:
                raise HTTPException(409, "该模型正在测试，请等待当前测试完成")
            self._running.add(config_id)
        try:
            return self._test(config_id, automatic, completion)
        finally:
            with self._guard:
                self._running.discard(config_id)

    def _test(self, config_id, automatic, completion=None):
        with self.factory() as db:
            config = db.get(LLMConfiguration, config_id)
            if not config or not config.enabled:
                raise HTTPException(404, "configuration not found")
            settings = settings_for(db)
            health = db.get(LLMHealthState, config_id)
            if health is None:
                health = LLMHealthState(config_id=config_id, mode="inherit", failures=0)
                db.add(health)
            if automatic and (not settings.enabled or health.mode == "off"):
                return None
            from .proxy import saved_proxy_url
            snapshot = SimpleNamespace(**{key: getattr(config, key) for key in ("provider", "model", "api_base", "temperature", "timeout_seconds", "credential_id", "use_proxy")})
            snapshot.proxy_url = saved_proxy_url(db)
            snapshot.timeout_seconds = min(snapshot.timeout_seconds, 30)
            health.status, health.error = "running", None
            db.commit()
        started = perf_counter()
        service, error = None, None
        try:
            service = LLMService(snapshot, self.store, completion or self.completion)
            provider_call = service.completion

            def bounded_call(**kwargs):
                remaining = snapshot.timeout_seconds - (perf_counter() - started)
                if remaining <= 0:
                    raise TimeoutError("connection test timeout")
                kwargs.update(timeout=remaining, num_retries=0)
                return provider_call(**kwargs)

            service.completion = bounded_call
            service.structured_extract("You are a connection test. Return only the requested structured result.",
                                       'Return a JSON object whose status field is exactly "ok".', ConnectionProbe, max_tokens=64)
        except LLMError as exc:
            error = (exc.status_code, {"code": exc.code, "message": str(exc)})
        except Exception:
            error = (502, {"code": "health_test_failed", "message": "模型测试未完成，请检查连接设置后重试"})
        elapsed = max(0, round((perf_counter() - started) * 1000))
        usage = (service.request_usage or service.last_usage or {}) if service else {}
        with self.factory() as db:
            record_usage(db, config_id, not error, elapsed, prompt_tokens=usage.get("prompt_tokens") or 0,
                         completion_tokens=usage.get("completion_tokens") or 0, purpose="test")
            config = db.get(LLMConfiguration, config_id)
            health = db.get(LLMHealthState, config_id)
            if health is not None and config is not None:
                settings = settings_for(db)
                health.status = "failed" if error else "passed"
                health.error = error[1]["message"][:500] if error else None
                health.elapsed_ms = elapsed
                health.last_test_at = datetime.now(timezone.utc)
                health.failures = health.failures + 1 if error else 0
                delay = min(1440, interval_for(settings, health) * (2 ** min(health.failures, 5)))
                health.next_test_at = health.last_test_at + timedelta(minutes=delay) if settings.enabled and config.enabled and health.mode != "off" else None
                db.commit()
        if error:
            raise HTTPException(error[0], error[1])
        return LLMTestOut(ok=True, provider=snapshot.provider, model=snapshot.model,
                          structured_output_mode=service.last_structured_mode or effective_structured_mode(snapshot), elapsed_ms=elapsed)
