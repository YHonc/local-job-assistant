from contextlib import asynccontextmanager
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
from app.api.guards import origin_allowed
from app.api.routes import router
from app.core.config import settings
from app.credentials import (
    CredentialStore,
    CredentialStoreError,
    KeyringCredentialStore,
    UnavailableCredentialStore,
)
from app.database import Base, get_db, make_engine, make_session_factory
from app.imports import router as imports_router
from app.llm.routes import router as llm_router
from app.email_sync import router as email_router
from app.email_sync.scheduler import LocalScheduler
from app.email_sync.service import EmailSyncCoordinator, EmailSyncService
from app.notifications import NotificationService, router as notification_router
from app.job_categories import JOB_CATEGORY_VALUES
from app.application_statuses import CURRENT_STATUSES, STATUS_ALIASES
from app.job_directions import DIRECTIONS
from app.llm.health import ModelHealthCoordinator


def _default_credential_store() -> CredentialStore:
    try:
        return KeyringCredentialStore()
    except CredentialStoreError:
        return UnavailableCredentialStore()


def create_app(
    database_url: str | None = None,
    create_tables: bool = False,
    credential_store: CredentialStore | None = None,
    llm_completion: Callable[..., Any] | None = None,
    email_provider_factory: Callable[..., Any] | None = None,
    notification_adapter: Any | None = None,
    enable_scheduler: bool | None = None,
) -> FastAPI:
    engine = make_engine(database_url or settings.database_url)
    factory = make_session_factory(engine)
    store = credential_store if credential_store is not None else _default_credential_store()
    notification_service = NotificationService(store, adapter=notification_adapter)
    email_sync_service = EmailSyncService(
        store,
        completion=llm_completion,
        provider_factory=email_provider_factory,
        notification_service=notification_service,
    )
    email_sync_coordinator = EmailSyncCoordinator(email_sync_service)
    model_health = ModelHealthCoordinator(factory, store, llm_completion)
    scheduler_enabled = (not create_tables) if enable_scheduler is None else enable_scheduler
    local_scheduler = (
        LocalScheduler(factory, email_sync_coordinator, notification_service, model_health)
        if scheduler_enabled
        else None
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if local_scheduler is not None:
            local_scheduler.start()
        try:
            yield
        finally:
            if local_scheduler is not None:
                local_scheduler.shutdown()
            engine.dispose()

    app = FastAPI(title="Local Job Assistant", lifespan=lifespan)

    @app.middleware("http")
    async def enforce_origin_policy(request: Request, call_next):
        # 与 guards.origin_allowed 相同的策略，统一覆盖所有变更请求；
        # 此前只有 imports/llm-test/models-probe 各自挂了 guard，其余
        # 增删改接口没有防御。GET 无副作用，保持可读。
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and not origin_allowed(origin):
                return JSONResponse(status_code=403, content={"detail": "origin not allowed"})
        return await call_next(request)

    app.include_router(router)
    app.include_router(llm_router)
    app.include_router(imports_router)
    app.include_router(email_router)
    app.include_router(notification_router)
    base = Path(__file__).parent
    app.mount("/static", StaticFiles(directory=base / "static"), name="static")
    templates = Jinja2Templates(directory=base / "templates")
    templates.env.globals["application_statuses"] = CURRENT_STATUSES

    @app.get("/", include_in_schema=False)
    def home(request: Request):
        # Preserve bookmarked edit links from the original Tracker.
        destination = "/legacy/tracker" if "edit" in request.query_params else "/workbench"
        if request.url.query:
            destination += "?" + request.url.query
        return RedirectResponse(destination, status_code=307, headers={"Cache-Control": "no-store"})

    @app.get("/legacy/tracker", response_class=HTMLResponse)
    def tracker(request: Request):
        return templates.TemplateResponse(request=request, name="tracker.html")

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail_page(request: Request, job_id: int):
        return templates.TemplateResponse(
            request=request,
            name="job_detail.html",
            context={"job_id": job_id},
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return templates.TemplateResponse(request=request, name="settings.html")

    @app.get("/events", response_class=HTMLResponse)
    def events_page(request: Request):
        return templates.TemplateResponse(request=request, name="events.html")

    @app.get("/notifications", response_class=HTMLResponse)
    def notifications_page(request: Request):
        return templates.TemplateResponse(request=request, name="notifications.html")

    @app.get("/workbench", response_class=HTMLResponse)
    def workbench_page(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="workbench.html",
            context={"job_categories": JOB_CATEGORY_VALUES, "application_statuses": CURRENT_STATUSES, "status_aliases": STATUS_ALIASES, "job_directions": [{"name": name, "category": category} for name, category, _ in DIRECTIONS]},
        )

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override_get_db
    if create_tables:
        Base.metadata.create_all(engine)
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.dispose = engine.dispose
    app.state.credential_store = store
    app.state.llm_completion = llm_completion
    app.state.model_health = model_health
    app.state.notification_service = notification_service
    app.state.email_sync_coordinator = email_sync_coordinator
    app.state.local_scheduler = local_scheduler
    return app

app = create_app()
