from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Application, ApplicationStatus
from app.database.session import get_db

from ..common import commit, require_application, require_job
from ..schemas import ApplicationBulkStatus, ApplicationIn, ApplicationOut, ApplicationUpdate


router = APIRouter()


@router.post("/applications", response_model=ApplicationOut, status_code=201)
def application_create(payload: ApplicationIn, db: Session = Depends(get_db)) -> Application:
    require_job(db, payload.job_id)
    application = Application(**payload.model_dump())
    db.add(application)
    commit(db, "application for this job")
    db.refresh(application)
    return application


@router.get("/applications", response_model=list[ApplicationOut])
def application_list(
    status: ApplicationStatus | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[Application]:
    statement = select(Application).order_by(Application.id)
    if status is not None:
        statement = statement.where(Application.status == status.value)
    if offset:
        statement = statement.offset(offset)
    if limit is not None:
        statement = statement.limit(limit)
    return list(db.scalars(statement).all())


@router.patch("/applications/bulk-status", response_model=list[ApplicationOut])
def application_bulk_status(
    payload: ApplicationBulkStatus, db: Session = Depends(get_db)
) -> list[Application]:
    """Atomically apply one status and timestamp to every requested application."""
    applications = list(db.scalars(
        select(Application).where(Application.id.in_(payload.application_ids))
    ).all())
    if len(applications) != len(payload.application_ids):
        raise HTTPException(status_code=404, detail="one or more applications not found")
    updated_at = datetime.now(timezone.utc)
    for application in applications:
        application.status = payload.status.value
        application.status_updated_at = updated_at
    commit(db, "applications")
    by_id = {application.id: application for application in applications}
    return [by_id[application_id] for application_id in payload.application_ids]


@router.get("/applications/{application_id}", response_model=ApplicationOut)
def application_get(application_id: int, db: Session = Depends(get_db)) -> Application:
    return require_application(db, application_id)


@router.patch("/applications/{application_id}", response_model=ApplicationOut)
def application_patch(
    application_id: int, payload: ApplicationUpdate, db: Session = Depends(get_db)
) -> Application:
    application = require_application(db, application_id)
    values = payload.model_dump(exclude_unset=True)
    if "status" in values and values["status"].value != application.status:
        application.status = values.pop("status").value
        application.status_updated_at = datetime.now(timezone.utc)
    else:
        values.pop("status", None)
    for key, value in values.items():
        setattr(application, key, value)
    commit(db, "application")
    db.refresh(application)
    return application


@router.delete("/applications/{application_id}", status_code=204)
def application_delete(application_id: int, db: Session = Depends(get_db)) -> None:
    db.delete(require_application(db, application_id))
    commit(db, "application")
