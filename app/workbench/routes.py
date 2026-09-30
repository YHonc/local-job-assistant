from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, contains_eager, joinedload
from sqlalchemy.orm.exc import StaleDataError

from app.api.schemas import (
    ApplicationStatus,
    WorkbenchApplicationOut,
    WorkbenchBulkOrganize,
    WorkbenchBulkOrganizeOut,
    WorkbenchCategoryReviewStatus,
    WorkbenchJobCreate,
    WorkbenchJobCreateOut,
    WorkbenchJobItemOut,
    WorkbenchJobListOut,
    WorkbenchJobUpdate,
    WorkbenchPreferencesOut,
    WorkbenchPreferencesUpdate,
)
from app.database.models import (
    Application,
    Company,
    Job,
    Setting,
    WorkbenchIdempotencyRecord,
)
from app.database.session import get_db
from app.job_categories import JOB_CATEGORY_VALUES, JobCategory, resolve_job_category
from app.application_statuses import STATUS_ALIASES, LEGACY_REVIEW_STATUSES
from app.job_directions import VALUES as DIRECTION_VALUES, direction_expression


router = APIRouter(prefix="/workbench", tags=["workbench"])
from .status import router as status_router
router.include_router(status_router)

_ACTIVE_ORGANIZE_STATUSES = {"pending", "organized", "archived", "active", "all"}
_SORTS = {
    "created_at_desc": (Job.created_at.desc(), Job.id.desc()),
    "created_at_asc": (Job.created_at.asc(), Job.id.asc()),
    "title_asc": (Job.title.collate("NOCASE").asc(), Job.id.asc()),
    "location_asc": (Job.location.collate("NOCASE").asc(), Job.id.asc()),
    "employment_type_asc": (Job.employment_type.collate("NOCASE").asc(), Job.id.asc()),
    "published_at_desc": (Job.published_at.desc().nulls_last(), Job.id.desc()),
    "application_status_asc": (
        func.coalesce(Application.status, "").collate("NOCASE").asc(),
        Job.id.asc(),
    ),
}
_DEFAULT_VISIBLE_FIELDS = ["category", "tags", "application", "organization", "location", "salary"]
_DEFAULT_PAGE_HINTS = {"pending": False, "follow": False, "mail": False}
_PREFERENCE_KEYS = {
    "theme_mode",
    "seed_color",
    "density",
    "reduce_motion",
    "visible_job_fields",
    "dismissed_page_hints",
    "poetry_preset",
}


def _http_conflict(detail: str = "workbench record changed; reload and retry") -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _flush_or_conflict(db: Session) -> None:
    try:
        db.flush()
    except StaleDataError as exc:
        db.rollback()
        raise _http_conflict() from exc
    except IntegrityError as exc:
        db.rollback()
        raise _http_conflict("workbench data conflicts with an existing record") from exc


def _commit_or_conflict(db: Session) -> None:
    try:
        db.commit()
    except StaleDataError as exc:
        db.rollback()
        raise _http_conflict() from exc
    except IntegrityError as exc:
        db.rollback()
        raise _http_conflict("workbench data conflicts with an existing record") from exc


def _item_for_job(db: Session, job_id: int) -> WorkbenchJobItemOut:
    job = db.scalar(
        select(Job)
        .options(joinedload(Job.company), joinedload(Job.application))
        .where(Job.id == job_id)
    )
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return WorkbenchJobItemOut.model_validate(job)


def _query_conditions(
    *,
    q: str | None,
    company_id: int | None,
    locations: list[str],
    employment_types: list[str],
    job_category: str | None,
    category_review_status: str | None,
    organize_status: str,
    application_status: ApplicationStatus | None,
    legacy_status: bool = False,
    direction: str | None = None,
    needs_review: bool = False,
    exclude: set[str] | None = None,
) -> list:
    excluded = exclude or set()
    conditions = []
    if direction and "direction" not in excluded:
        conditions.append(direction_expression(Job) == direction)
    if q and "q" not in excluded:
        term = f"%{q.strip()}%"
        conditions.append(
            or_(
                Job.title.ilike(term),
                Job.location.ilike(term),
                Job.employment_type.ilike(term),
                Job.job_category.ilike(term),
                Job.tags.cast(String).ilike(term),
                Job.salary_text.ilike(term),
                Job.notes.ilike(term),
                Job.description.ilike(term),
                Job.source_url.ilike(term),
                Job.source_website.ilike(term),
                Job.original_title.ilike(term),
                Company.name.ilike(term),
                cast(Company.aliases, String).ilike(term),
            )
        )
    if company_id is not None and "company_id" not in excluded:
        conditions.append(Job.company_id == company_id)
    if locations and "locations" not in excluded:
        conditions.append(Job.location.in_(locations))
    if employment_types and "employment_types" not in excluded:
        conditions.append(Job.employment_type.in_(employment_types))
    if job_category is not None and "job_category" not in excluded:
        conditions.append(Job.job_category == job_category)
    if category_review_status is not None and "category_review_status" not in excluded:
        conditions.append(Job.category_review_status == category_review_status)
    if needs_review and "needs_review" not in excluded:
        conditions.append(
            Job.organize_status != "archived",
        )
        conditions.append(
            or_(
                Job.organize_status == "pending",
                Job.category_review_status == "pending",
            )
        )
    elif "organize_status" not in excluded:
        if organize_status == "active":
            conditions.append(Job.organize_status != "archived")
        elif organize_status != "all":
            conditions.append(Job.organize_status == organize_status)
    if application_status is not None and "application_status" not in excluded:
        value = application_status.value
        equivalents = [value] + [key for key, mapped in STATUS_ALIASES.items() if mapped == value]
        status_condition = Application.status.in_(equivalents)
        conditions.append(or_(status_condition, Application.id.is_(None)) if value == "待投递" else status_condition)
    if legacy_status:
        conditions.append(Application.status.in_(LEGACY_REVIEW_STATUSES))
    return conditions


def _facet_rows(db: Session, field, conditions: list) -> list[dict[str, str | int]]:
    rows = db.execute(
        select(field, func.count(Job.id))
        .select_from(Job)
        .join(Company, Company.id == Job.company_id)
        .outerjoin(Application, Application.job_id == Job.id)
        .where(*conditions)
        .group_by(field)
        .order_by(field)
    ).all()
    return [
        {"value": value or "", "count": int(count)}
        for value, count in rows
    ]


@router.get("/jobs", response_model=WorkbenchJobListOut)
def workbench_job_list(
    q: str | None = Query(default=None, max_length=300),
    company_id: int | None = Query(default=None, gt=0),
    locations: list[str] = Query(default=[]),
    employment_types: list[str] = Query(default=[]),
    job_category: str | None = None,
    category_review_status: WorkbenchCategoryReviewStatus | None = None,
    organize_status: str = "active",
    needs_review: bool = False,
    application_status: ApplicationStatus | None = None,
    legacy_status: bool = False,
    direction: str | None = None,
    sort: str = "created_at_desc",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> WorkbenchJobListOut:
    if organize_status not in _ACTIVE_ORGANIZE_STATUSES:
        raise HTTPException(status_code=422, detail="invalid organize_status")
    if direction and direction not in DIRECTION_VALUES:
        raise HTTPException(422, "invalid direction")
    if sort not in _SORTS:
        raise HTTPException(status_code=422, detail="invalid sort")

    common = {
        "q": q,
        "company_id": company_id,
        "locations": locations,
        "employment_types": employment_types,
        "job_category": job_category,
        "category_review_status": category_review_status,
        "organize_status": organize_status,
        "application_status": application_status,
        "legacy_status": legacy_status,
        "direction": direction,
        "needs_review": needs_review,
    }
    conditions = _query_conditions(**common)
    total = int(
        db.scalar(
            select(func.count(Job.id))
            .select_from(Job)
            .join(Company, Company.id == Job.company_id)
            .outerjoin(Application, Application.job_id == Job.id)
            .where(*conditions)
        )
        or 0
    )
    rows = db.execute(
        select(Job, Company, Application)
        .join(Company, Company.id == Job.company_id)
        .outerjoin(Application, Application.job_id == Job.id)
        .options(contains_eager(Job.company), contains_eager(Job.application))
        .where(*conditions)
        .order_by(*_SORTS[sort])
        .limit(limit)
        .offset(offset)
    ).all()
    items = [
        WorkbenchJobItemOut.model_validate(job)
        for job, _company, _application in rows
    ]

    category_conditions = _query_conditions(**common, exclude={"job_category", "direction"})
    raw_categories = _facet_rows(db, Job.job_category, category_conditions)
    category_counts = {str(row["value"]): int(row["count"]) for row in raw_categories}
    categories = [
        {"value": name, "count": category_counts.pop(name, 0)}
        for name in JOB_CATEGORY_VALUES
    ]
    categories.extend(
        {"value": name, "count": count}
        for name, count in sorted(category_counts.items())
    )

    counts = {
        "legacy_status": int(db.scalar(select(func.count(Application.id)).where(Application.status.in_(LEGACY_REVIEW_STATUSES))) or 0),
        "active": int(
            db.scalar(select(func.count(Job.id)).where(Job.organize_status != "archived")) or 0
        ),
        "pending": int(
            db.scalar(
                select(func.count(Job.id)).where(
                    Job.organize_status != "archived",
                    or_(
                        Job.organize_status == "pending",
                        Job.category_review_status == "pending",
                    ),
                )
            )
            or 0
        ),
    }
    facets = {
        "directions": _facet_rows(db, direction_expression(Job), _query_conditions(**common, exclude={"direction"})),
        "categories": categories,
        "locations": _facet_rows(
            db, Job.location, _query_conditions(**common, exclude={"locations"})
        ),
        "employment_types": _facet_rows(
            db,
            Job.employment_type,
            _query_conditions(**common, exclude={"employment_types"}),
        ),
        "application_statuses": _facet_rows(
            db,
            Application.status,
            _query_conditions(**common, exclude={"application_status"}),
        ),
        "organize_statuses": _facet_rows(
            db,
            Job.organize_status,
            _query_conditions(**common, exclude={"organize_status"}),
        ),
    }
    return WorkbenchJobListOut(items=items, total=total, facets=facets, counts=counts)


def _company_for_create(db: Session, payload: WorkbenchJobCreate) -> Company:
    if payload.company_id is not None:
        company = db.get(Company, payload.company_id)
        if company is None:
            raise HTTPException(status_code=404, detail="company not found")
        return company

    assert payload.company is not None
    desired = payload.company.name.casefold()
    companies = db.scalars(select(Company).order_by(Company.id)).all()
    matches = [
        company for company in companies
        if company.name.casefold() == desired or any(
            alias.casefold() == desired for alias in (company.aliases or [])
        )
    ]
    if matches:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "company_selection_required",
                "candidates": [
                    {"id": company.id, "name": company.name}
                    for company in matches
                ],
            },
        )
    company = Company(
        name=payload.company.name,
        aliases=payload.company.aliases or [],
        notes=payload.company.notes,
    )
    db.add(company)
    db.flush()
    return company


def _request_hash(payload: WorkbenchJobCreate) -> str:
    content = payload.model_dump(mode="json", exclude={"idempotency_key"})
    encoded = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _replay_response(
    db: Session, record: WorkbenchIdempotencyRecord, request_hash: str
) -> WorkbenchJobCreateOut:
    if record.request_hash != request_hash:
        raise _http_conflict("idempotency key was already used with a different request")
    return WorkbenchJobCreateOut(
        item=_item_for_job(db, record.job_id), idempotent_replay=True
    )


@router.post("/jobs", response_model=WorkbenchJobCreateOut, status_code=201)
def workbench_job_create(
    payload: WorkbenchJobCreate, db: Session = Depends(get_db)
) -> WorkbenchJobCreateOut:
    request_hash = _request_hash(payload)
    record = db.scalar(
        select(WorkbenchIdempotencyRecord).where(
            WorkbenchIdempotencyRecord.idempotency_key == payload.idempotency_key
        )
    )
    if record is not None:
        return _replay_response(db, record, request_hash)

    try:
        company = _company_for_create(db, payload)
        fields = payload.job.model_dump()
        fields["job_category"] = resolve_job_category(
            fields.get("job_category"),
            title=fields["title"],
            description=fields.get("description"),
            original_title=fields.get("original_title"),
        )
        job = Job(company_id=company.id, **fields)
        db.add(job)
        db.flush()
        if payload.application is not None:
            application = Application(
                job_id=job.id,
                status=payload.application.status.value,
                notes=payload.application.notes,
            )
            db.add(application)
        db.add(
            WorkbenchIdempotencyRecord(
                idempotency_key=payload.idempotency_key,
                request_hash=request_hash,
                job_id=job.id,
            )
        )
        db.flush()
        db.commit()
        return WorkbenchJobCreateOut(item=_item_for_job(db, job.id), idempotent_replay=False)
    except IntegrityError as exc:
        db.rollback()
        # Two identical requests may race. The unique key, not a process-local
        # lock, chooses the winner; the loser reads and validates its record.
        record = db.scalar(
            select(WorkbenchIdempotencyRecord).where(
                WorkbenchIdempotencyRecord.idempotency_key == payload.idempotency_key
            )
        )
        if record is not None:
            return _replay_response(db, record, request_hash)
        raise _http_conflict("company or job conflicts with an existing record") from exc
    except StaleDataError as exc:
        db.rollback()
        raise _http_conflict() from exc


@router.patch("/jobs/{job_id}", response_model=WorkbenchJobItemOut)
def workbench_job_update(
    job_id: int, payload: WorkbenchJobUpdate, db: Session = Depends(get_db)
) -> WorkbenchJobItemOut:
    job = db.scalar(
        select(Job)
        .options(joinedload(Job.company), joinedload(Job.application))
        .where(Job.id == job_id)
    )
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.version != payload.expected_job_version:
        raise _http_conflict("job version is stale")
    if payload.job is not None:
        values = payload.job.model_dump(exclude_unset=True)
        if "company_id" in values:
            company = db.get(Company, values.pop("company_id"))
            if company is None:
                raise HTTPException(status_code=404, detail="company not found")
            job.company = company
        if "job_category" in values:
            explicit_category = values["job_category"] is not None
            values["job_category"] = resolve_job_category(
                values["job_category"],
                title=values.get("title", job.title),
                description=values.get("description", job.description),
                original_title=values.get("original_title", job.original_title),
            )
            if "category_review_status" not in values:
                values["category_review_status"] = (
                    "confirmed" if explicit_category else "pending"
                )
        for key, value in values.items():
            if key == "job_category" and value is not None:
                value = value.value if isinstance(value, JobCategory) else value
            setattr(job, key, value)

    if payload.application is not None:
        application = job.application
        if application is None:
            raise HTTPException(status_code=409, detail="application does not exist")
        if application.version != payload.expected_application_version:
            raise _http_conflict("application version is stale")
        values = payload.application.model_dump(exclude_unset=True)
        if "status" in values and values["status"].value != application.status:
            application.status = values.pop("status").value
            from datetime import datetime, timezone

            application.status_updated_at = datetime.now(timezone.utc)
        else:
            values.pop("status", None)
        for key, value in values.items():
            setattr(application, key, value)

    _commit_or_conflict(db)
    return _item_for_job(db, job_id)


@router.get("/jobs/{job_id}", response_model=WorkbenchJobItemOut)
def workbench_job_get(job_id: int, db: Session = Depends(get_db)) -> WorkbenchJobItemOut:
    return _item_for_job(db, job_id)


@router.post("/jobs/bulk-organize", response_model=WorkbenchBulkOrganizeOut)
def workbench_bulk_organize(
    payload: WorkbenchBulkOrganize, db: Session = Depends(get_db)
) -> WorkbenchBulkOrganizeOut:
    expected = {item.job_id: item.expected_version for item in payload.items}
    jobs = list(db.scalars(select(Job).where(Job.id.in_(expected))).all())
    if len(jobs) != len(expected):
        raise HTTPException(status_code=404, detail="one or more jobs not found")
    stale_ids = [job.id for job in jobs if job.version != expected[job.id]]
    if stale_ids:
        raise _http_conflict(f"stale job versions: {stale_ids}")

    added_tags = payload.tags_to_add or []
    merged_tags_by_id: dict[int, list[str]] = {}
    if added_tags:
        for job in jobs:
            merged = list(job.tags or [])
            known = {tag.casefold() for tag in merged}
            for tag in added_tags:
                if tag.casefold() not in known:
                    merged.append(tag)
                    known.add(tag.casefold())
            if len(merged) > 50:
                raise HTTPException(status_code=422, detail="merged tags exceed the 50 item limit")
            merged_tags_by_id[job.id] = merged
    for job in jobs:
        if payload.job_category is not None:
            job.job_category = payload.job_category.value
            job.category_review_status = payload.category_review_status or "confirmed"
        elif payload.category_review_status is not None:
            job.category_review_status = payload.category_review_status
        if payload.organize_status is not None:
            job.organize_status = payload.organize_status
        if added_tags:
            job.tags = merged_tags_by_id[job.id]

    try:
        db.flush()
        updated = [{"job_id": job.id, "version": job.version} for job in jobs]
        db.commit()
    except StaleDataError as exc:
        db.rollback()
        raise _http_conflict("one or more jobs changed during bulk organization") from exc
    except IntegrityError as exc:
        db.rollback()
        raise _http_conflict("bulk organization conflicts with existing data") from exc
    return WorkbenchBulkOrganizeOut(updated_count=len(updated), items=updated)


def _setting_map(db: Session) -> dict[str, str | None]:
    return {
        row.key: row.value
        for row in db.scalars(select(Setting).where(Setting.key.in_(_PREFERENCE_KEYS | {"theme"})))
    }


def _decode_json(value: str | None, default):
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _preferences_out(db: Session) -> WorkbenchPreferencesOut:
    values = _setting_map(db)
    theme_mode = values.get("theme_mode") or values.get("theme") or "light"
    if theme_mode not in {"light", "dark", "system"}:
        theme_mode = "light"
    density = values.get("density") or "comfortable"
    if density not in {"comfortable", "compact"}:
        density = "comfortable"
    seed_color = values.get("seed_color") or "#3659b8"
    try:
        visible_fields = _decode_json(values.get("visible_job_fields"), _DEFAULT_VISIBLE_FIELDS)
        allowed = set(_DEFAULT_VISIBLE_FIELDS)
        if not isinstance(visible_fields, list):
            visible_fields = _DEFAULT_VISIBLE_FIELDS
        visible_fields = list(dict.fromkeys(field for field in visible_fields if field in allowed))
    except TypeError:
        visible_fields = _DEFAULT_VISIBLE_FIELDS
    hints = _decode_json(values.get("dismissed_page_hints"), _DEFAULT_PAGE_HINTS.copy())
    if not isinstance(hints, dict):
        hints = _DEFAULT_PAGE_HINTS.copy()
    hints = {
        key: hints.get(key, False) if isinstance(hints.get(key, False), bool) else False
        for key in _DEFAULT_PAGE_HINTS
    }
    reduce_motion = (values.get("reduce_motion") or "false").lower() == "true"
    poetry = values.get("poetry_preset") or "random"
    if poetry not in {"random", "shuidiao", "dielianhua", "qingpingle", "yulinling", "jiangchengzi", "nian"}:
        poetry = "random"
    return WorkbenchPreferencesOut(
        theme_mode=theme_mode,
        seed_color=seed_color,
        density=density,
        reduce_motion=reduce_motion,
        visible_job_fields=visible_fields,
        dismissed_page_hints=hints,
        poetry_preset=poetry,
    )


def _save_setting(db: Session, key: str, value: str) -> None:
    setting = db.get(Setting, key)
    if setting is None:
        db.add(Setting(key=key, value=value))
    else:
        setting.value = value


@router.get("/preferences", response_model=WorkbenchPreferencesOut)
def workbench_preferences_get(db: Session = Depends(get_db)) -> WorkbenchPreferencesOut:
    return _preferences_out(db)


@router.put("/preferences", response_model=WorkbenchPreferencesOut)
def workbench_preferences_put(
    payload: WorkbenchPreferencesUpdate, db: Session = Depends(get_db)
) -> WorkbenchPreferencesOut:
    values = payload.model_dump(exclude_unset=True)
    if "theme_mode" in values:
        theme_mode = values["theme_mode"]
        _save_setting(db, "theme_mode", theme_mode)
        # Keep older clients' light/dark preference in sync. "system" maps to
        # the saved legacy choice while remaining system-aware in this API.
        if theme_mode != "system":
            _save_setting(db, "theme", theme_mode)
        elif db.get(Setting, "theme") is None:
            _save_setting(db, "theme", "light")
    if "density" in values:
        _save_setting(db, "density", values["density"])
    if "seed_color" in values:
        _save_setting(db, "seed_color", values["seed_color"] or "")
    if "reduce_motion" in values:
        _save_setting(db, "reduce_motion", "true" if values["reduce_motion"] else "false")
    if "visible_job_fields" in values:
        _save_setting(
            db,
            "visible_job_fields",
            json.dumps(values["visible_job_fields"] or [], ensure_ascii=False),
        )
    if "dismissed_page_hints" in values:
        current = _preferences_out(db).dismissed_page_hints
        current.update(values["dismissed_page_hints"] or {})
        _save_setting(
            db, "dismissed_page_hints", json.dumps(current, ensure_ascii=False)
        )
    if "poetry_preset" in values and values["poetry_preset"] is not None:
        _save_setting(db, "poetry_preset", values["poetry_preset"])
    _commit_or_conflict(db)
    return _preferences_out(db)
