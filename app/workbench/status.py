from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.application_statuses import CURRENT_STATUSES, STATUS_ALIASES, current_status
from app.api.schemas import WorkbenchJobItemOut
from app.database.models import Application, Job, Setting
from app.email_sync.progress_models import ApplicationStatusHistory
from app.database.session import get_db

router = APIRouter()


class DeliveryPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on_detail: bool = True
    on_apply: bool = True


def delivery_preferences(db):
    setting = db.get(Setting, "delivery_behavior")
    try:
        return DeliveryPreferences.model_validate_json(setting.value) if setting else DeliveryPreferences()
    except (ValueError, TypeError):
        return DeliveryPreferences()


@router.get("/statuses")
def statuses():
    return {"values": CURRENT_STATUSES, "aliases": STATUS_ALIASES}


@router.get("/delivery-preferences", response_model=DeliveryPreferences)
def get_delivery_preferences(db: Session = Depends(get_db)):
    return delivery_preferences(db)


@router.put("/delivery-preferences", response_model=DeliveryPreferences)
def put_delivery_preferences(payload: DeliveryPreferences, db: Session = Depends(get_db)):
    db.merge(Setting(key="delivery_behavior", value=payload.model_dump_json()))
    db.commit()
    return payload


class StatusTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: int = Field(gt=0)
    expected_application_version: int | None = Field(ge=1)


class StatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[StatusTarget] = Field(min_length=1, max_length=200)
    status: str

    @field_validator("status")
    @classmethod
    def valid_status(cls, value):
        if value not in CURRENT_STATUSES:
            raise ValueError("select a current application status")
        return value

    @field_validator("items")
    @classmethod
    def unique_targets(cls, value):
        if len({item.job_id for item in value}) != len(value):
            raise ValueError("job ids must be unique")
        return value


class StatusAction(StatusTarget):
    action: Literal["detail", "apply"]


def commit(db):
    try:
        db.commit()
    except (StaleDataError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(409, "投递记录已变化，请刷新后重试") from exc


def change_status(db, job, status, source):
    application = job.application
    previous = application.status if application else None
    if application and previous == status:
        return None
    if application is None:
        application = Application(job=job, status=status)
        db.add(application)
    else:
        application.status = status
    application.status_updated_at = datetime.now(timezone.utc)
    old_source = db.info.get("application_status_source", "manual")
    db.info["application_status_source"] = source
    try:
        db.flush()
    except (StaleDataError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(409, "投递记录已变化，请刷新后重试") from exc
    finally:
        db.info["application_status_source"] = old_source
    if previous is None:
        change = ApplicationStatusHistory(application_id=application.id, before_status="",
                                         after_status=status, source=source, result_version=application.version)
        db.add(change)
    else:
        change = db.scalar(select(ApplicationStatusHistory).where(
            ApplicationStatusHistory.application_id == application.id,
            ApplicationStatusHistory.result_version == application.version,
        ).order_by(ApplicationStatusHistory.id.desc()))
    return change


def response(db, jobs, changes):
    commit(db)
    return {"items": [WorkbenchJobItemOut.model_validate(job) for job in jobs],
            "changes": [{"id": item.id, "application_id": item.application_id} for item in changes if item]}


@router.post("/jobs/status")
def update_status(payload: StatusUpdate, db: Session = Depends(get_db)):
    targets = {item.job_id: item.expected_application_version for item in payload.items}
    jobs = list(db.scalars(select(Job).options(joinedload(Job.company), joinedload(Job.application)).where(Job.id.in_(targets))))
    if len(jobs) != len(targets):
        raise HTTPException(404, "one or more jobs not found")
    for job in jobs:
        if (job.application.version if job.application else None) != targets[job.id]:
            raise HTTPException(409, "投递记录已变化，请刷新后重试")
    changes = [change_status(db, job, payload.status, "manual") for job in jobs]
    return response(db, jobs, changes)


@router.post("/jobs/status-action")
def status_action(payload: StatusAction, db: Session = Depends(get_db)):
    job = db.scalar(select(Job).options(joinedload(Job.company), joinedload(Job.application)).where(Job.id == payload.job_id))
    if job is None:
        raise HTTPException(404, "job not found")
    prefs = delivery_preferences(db)
    permitted = prefs.on_detail if payload.action == "detail" else prefs.on_apply
    current_version = job.application.version if job.application else None
    changes = []
    if permitted and current_status(job.application.status if job.application else None) == "待投递" and current_version == payload.expected_application_version:
        changes.append(change_status(db, job, "已投递", payload.action))
    return response(db, [job], changes)


@router.post("/status-changes/{change_id}/undo")
def undo_status(change_id: int, db: Session = Depends(get_db)):
    change = db.get(ApplicationStatusHistory, change_id)
    if change is None:
        raise HTTPException(404, "status change not found")
    application = db.get(Application, change.application_id)
    if not application or application.version != change.result_version or application.status != change.after_status:
        raise HTTPException(409, "状态已有后续修改，无法撤销")
    job = db.scalar(select(Job).options(joinedload(Job.company), joinedload(Job.application)).where(Job.id == application.job_id))
    # Keep an explicitly created application record; undo its progress only.
    reversal = change_status(db, job, change.before_status or "待投递", "undo")
    return response(db, [job], [reversal])


@router.get("/jobs/{job_id}/status-history")
def status_history(job_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(ApplicationStatusHistory).join(Application).where(Application.job_id == job_id).order_by(ApplicationStatusHistory.id.desc()).limit(100))
    return [{"id": row.id, "previous_status": row.before_status or None, "status": row.after_status,
             "source": row.source, "created_at": row.created_at.replace(tzinfo=timezone.utc)} for row in rows]
