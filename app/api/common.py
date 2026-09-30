"""Helpers shared by the resource API routers.

This module contains the small amount of HTTP/database glue used by the
companies, jobs, applications, and preferences endpoints.  It deliberately
does not import the aggregate router so each resource router can be imported
independently.
"""

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.database.models import Application, Company, Job


PREFERENCE_VALUES = {
    "theme": {"light", "dark"},
    "density": {"comfortable", "compact"},
    "poetry_preset": {
        "random",
        "shuidiao",
        "dielianhua",
        "qingpingle",
        "yulinling",
        "jiangchengzi",
        "nian",
    },
}


def commit(db: Session, resource: str) -> None:
    """Commit a resource mutation and translate write conflicts to HTTP 409."""

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"{resource} conflicts with an existing resource",
        ) from exc
    except StaleDataError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"{resource} changed during this update; reload and retry",
        ) from exc


def require_company(db: Session, company_id: int) -> Company:
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    return company


def require_job(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


def require_application(db: Session, application_id: int) -> Application:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")
    return application


def validate_preference(key: str, value: str | None = None) -> str:
    key = key.strip()
    if not key or len(key) > 100:
        raise HTTPException(
            status_code=422,
            detail="setting key must be 1-100 non-whitespace characters",
        )
    if key not in PREFERENCE_VALUES:
        raise HTTPException(status_code=422, detail="unsupported application preference")
    if value is not None and value not in PREFERENCE_VALUES[key]:
        raise HTTPException(status_code=422, detail="invalid application preference value")
    return key
