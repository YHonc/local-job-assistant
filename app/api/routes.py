from fastapi import APIRouter

from .common import (
    PREFERENCE_VALUES,
    commit,
    require_application,
    require_company,
    require_job,
    validate_preference,
)
from .endpoints import (
    applications_router,
    companies_router,
    jobs_router,
    preferences_router,
)
from app.workbench import router as workbench_router
from app.imports.drafts import drafts_router
from app.imports.prompt_settings import router as prompt_router
from app.email_sync.progress import router as progress_router


router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Keep the resource order stable.  Within the jobs and applications routers,
# fixed bulk paths are registered before their dynamic ``/{id}`` paths.
router.include_router(companies_router)
router.include_router(jobs_router)
router.include_router(applications_router)
router.include_router(preferences_router)
router.include_router(workbench_router)
router.include_router(drafts_router)
router.include_router(prompt_router)
router.include_router(progress_router)


__all__ = [
    "PREFERENCE_VALUES",
    "commit",
    "health",
    "progress_router",
    "workbench_router",
    "drafts_router",
    "require_application",
    "require_company",
    "require_job",
    "router",
    "validate_preference",
]
