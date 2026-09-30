from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database.models import Application, ApplicationStatus, Company, Job
from app.database.session import get_db
from app.job_categories import JobCategory, resolve_job_category

from ..common import commit, require_company, require_job
from ..schemas import JobBulkDelete, JobBulkDeleteOut, JobIn, JobOut, JobPatch


router = APIRouter()


@router.post("/jobs", response_model=JobOut, status_code=201)
def job_create(payload: JobIn, db: Session = Depends(get_db)) -> Job:
    require_company(db, payload.company_id)
    values = payload.model_dump()
    values["job_category"] = resolve_job_category(
        values.get("job_category"),
        title=values["title"],
        description=values.get("description"),
        original_title=values.get("original_title"),
    )
    if payload.job_category is not None:
        values["category_review_status"] = "confirmed"
    job = Job(**values)
    db.add(job)
    commit(db, "job")
    db.refresh(job)
    return job


@router.get("/jobs", response_model=list[JobOut])
def job_list(
    q: str | None = Query(default=None),
    company_id: int | None = Query(default=None),
    employment_type: str | None = Query(default=None),
    job_category: JobCategory | None = Query(default=None),
    application_status: ApplicationStatus | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[Job]:
    statement = select(Job).join(Company)
    if company_id is not None:
        statement = statement.where(Job.company_id == company_id)
    if employment_type is not None:
        statement = statement.where(Job.employment_type == employment_type)
    if job_category is not None:
        statement = statement.where(Job.job_category == job_category.value)
    if q:
        term = f"%{q}%"
        aliases = func.json_each(Company.aliases).table_valued("value").alias("company_alias")
        alias_match = select(1).select_from(aliases).where(aliases.c.value.ilike(term)).correlate(Company).exists()
        statement = statement.where(or_(
            Job.title.ilike(term), Job.description.ilike(term), Job.location.ilike(term),
            Job.job_category.ilike(term),
            Job.source_url.ilike(term), Job.source_website.ilike(term), Company.name.ilike(term), alias_match,
        ))
    if application_status is not None:
        statement = statement.join(Application).where(Application.status == application_status.value)
    statement = statement.order_by(Job.id)
    # limit/offset are optional; omitting limit keeps the original full listing.
    if offset:
        statement = statement.offset(offset)
    if limit is not None:
        statement = statement.limit(limit)
    return list(db.scalars(statement).all())


@router.post("/jobs/bulk-delete", response_model=JobBulkDeleteOut)
def job_bulk_delete(payload: JobBulkDelete, db: Session = Depends(get_db)) -> dict[str, int | list[int]]:
    """Delete all requested jobs atomically, including their applications."""
    jobs = list(db.scalars(select(Job).where(Job.id.in_(payload.job_ids))).all())
    if len(jobs) != len(payload.job_ids):
        raise HTTPException(status_code=404, detail="one or more jobs not found")
    for job in jobs:
        db.delete(job)
    commit(db, "jobs")
    return {"deleted_count": len(jobs), "deleted_job_ids": payload.job_ids}


@router.get("/jobs/{job_id}", response_model=JobOut)
def job_get(job_id: int, db: Session = Depends(get_db)) -> Job:
    return require_job(db, job_id)


@router.patch("/jobs/{job_id}", response_model=JobOut)
def job_patch(job_id: int, payload: JobPatch, db: Session = Depends(get_db)) -> Job:
    job = require_job(db, job_id)
    values = payload.model_dump(exclude_unset=True)
    if "company_id" in values:
        require_company(db, values["company_id"])
    if "job_category" in values:
        explicit_category = values["job_category"] is not None
        values["job_category"] = resolve_job_category(
            values["job_category"],
            title=values.get("title", job.title),
            description=values.get("description", job.description),
            original_title=values.get("original_title", job.original_title),
        )
        job.category_review_status = "confirmed" if explicit_category else "pending"
    for key, value in values.items():
        setattr(job, key, value)
    commit(db, "job")
    db.refresh(job)
    return job


@router.delete("/jobs/{job_id}", status_code=204)
def job_delete(job_id: int, db: Session = Depends(get_db)) -> None:
    db.delete(require_job(db, job_id))
    commit(db, "job")
