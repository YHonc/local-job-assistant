"""Resource routers used by :mod:`app.api.routes`."""

from .applications import router as applications_router
from .companies import router as companies_router
from .jobs import router as jobs_router
from .preferences import router as preferences_router

__all__ = [
    "applications_router",
    "companies_router",
    "jobs_router",
    "preferences_router",
]
