from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database.models import Application, Company, Job
from app.imports.duplicate import normalize_text, normalize_url

from .schemas import AssociationCandidate, JobAssociationCandidate


def safe_normalize_url(value: str | None) -> str | None:
    try:
        return normalize_url(value) if value else None
    except ValueError:
        return None


def _text_score(candidate: str | None, target: str | None) -> tuple[int, str | None]:
    candidate_normalized = normalize_text(candidate)
    target_normalized = normalize_text(target)
    if not target_normalized:
        return 0, None
    if candidate_normalized == target_normalized:
        return 4, "exact"
    if candidate_normalized and (
        candidate_normalized in target_normalized or target_normalized in candidate_normalized
    ):
        return 2, "partial"
    return 0, None


def find_application_candidates(
    db: Session,
    company_name: str | None,
    job_title: str | None,
) -> list[AssociationCandidate]:
    if not normalize_text(company_name) and not normalize_text(job_title):
        return []

    statement = (
        select(Application)
        .options(joinedload(Application.job).joinedload(Job.company))
        .order_by(Application.id)
    )
    candidates: list[AssociationCandidate] = []
    for application in db.scalars(statement).unique().all():
        company = application.job.company
        company_scores = [_text_score(company.name, company_name)] + [
            _text_score(alias, company_name) for alias in company.aliases or []
        ]
        company_score, company_kind = max(company_scores, key=lambda item: item[0])
        title_score, title_kind = _text_score(application.job.title, job_title)

        if normalize_text(company_name) and company_score == 0:
            continue
        if normalize_text(job_title) and title_score == 0:
            continue
        score = company_score + title_score
        if score == 0:
            continue
        reasons = []
        if company_kind:
            reasons.append(f"company_{company_kind}")
        if title_kind:
            reasons.append(f"title_{title_kind}")
        candidates.append(
            AssociationCandidate(
                application_id=application.id,
                job_id=application.job.id,
                company_id=company.id,
                company_name=company.name,
                job_title=application.job.title,
                application_status=application.status,
                score=score,
                reasons=reasons,
            )
        )
    return sorted(candidates, key=lambda item: (-item.score, item.application_id))


def suggested_application_id(candidates: list[AssociationCandidate]) -> int | None:
    if not candidates or candidates[0].score < 4:
        return None
    if len(candidates) > 1 and candidates[1].score == candidates[0].score:
        return None
    return candidates[0].application_id


def find_job_candidates(
    db: Session,
    company_name: str | None,
    job_title: str | None,
    work_location: str | None = None,
    links: list[str] | None = None,
) -> list[JobAssociationCandidate]:
    """Score every existing Job using company/alias and title text.

    This intentionally has the same transparent scoring as the legacy
    Application matcher, while allowing jobs without an Application to be
    considered.  Automatic binding is decided by ``reliable_job_match``;
    callers can expose all candidates when a human needs to resolve a tie.
    """

    company_target = normalize_text(company_name)
    title_target = normalize_text(job_title)
    if not company_target and not title_target:
        return []

    statement = (
        select(Job)
        .options(joinedload(Job.company), joinedload(Job.application))
        .order_by(Job.id)
    )
    candidates: list[JobAssociationCandidate] = []
    for job in db.scalars(statement).unique().all():
        company = job.company
        company_scores = [_text_score(company.name, company_name)] + [
            _text_score(alias, company_name) for alias in company.aliases or []
        ]
        company_score, company_kind = max(company_scores, key=lambda item: item[0])
        title_score, title_kind = _text_score(job.title, job_title)
        url_match = bool(safe_normalize_url(job.source_url) and safe_normalize_url(job.source_url) in {
            safe_normalize_url(link) for link in links or []
        })
        if company_target and company_score == 0:
            continue
        if title_target and title_score == 0 and not url_match:
            continue
        score = company_score + title_score
        if work_location and job.location and normalize_text(work_location) != normalize_text(job.location):
            score = min(score, 6)
        if url_match:
            score = 12
        if score == 0:
            continue
        reasons: list[str] = []
        if url_match:
            reasons.append("source_url_exact")
        if company_kind:
            reasons.append(f"company_{company_kind}")
        if title_kind:
            reasons.append(f"title_{title_kind}")
        candidates.append(
            JobAssociationCandidate(
                job_id=job.id,
                company_id=company.id,
                application_id=job.application.id if job.application else None,
                company_name=company.name,
                job_title=job.title,
                application_status=job.application.status if job.application else None,
                score=score,
                reasons=reasons,
            )
        )
    return sorted(candidates, key=lambda item: (-item.score, item.job_id))


def reliable_job_match(candidates: list[JobAssociationCandidate]) -> JobAssociationCandidate | None:
    """Return a unique exact company+title candidate, otherwise ``None``."""

    if not candidates or candidates[0].score < 8:
        return None
    if len(candidates) > 1 and candidates[1].score == candidates[0].score:
        return None
    return candidates[0]
