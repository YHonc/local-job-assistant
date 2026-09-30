from __future__ import annotations

import unicodedata
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database.models import Job

from .schemas import DuplicateCandidate


TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "referrer",
    "source",
}


# Both normalizers are pure string functions that dedupe and matching re-run for
# the same rows on every import or event lookup; caching turns the repeated NFKC
# passes (the hot loop cost) into dict lookups without changing any result.
# Invalid URLs raise before returning, so failures are never cached.
@lru_cache(maxsize=8192)
def normalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid http/https URL")
    if parsed.username or parsed.password:
        raise ValueError("URL user information is not supported")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid URL port") from exc

    scheme = parsed.scheme.casefold()
    host = parsed.hostname.casefold()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if (scheme, port) in {("http", 80), ("https", 443)}:
        port = None
    netloc = host if port is None else f"{host}:{port}"

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"

    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = key.casefold()
        if normalized_key.startswith("utm_") or normalized_key in TRACKING_PARAMETERS:
            continue
        query_items.append((key, value))
    query_items.sort(key=lambda item: (item[0].casefold(), item[1]))

    return urlunsplit((scheme, netloc, path, urlencode(query_items, doseq=True), ""))


@lru_cache(maxsize=8192)
def normalize_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(
        char
        for char in normalized
        if not unicodedata.category(char).startswith(("P", "Z")) and not char.isspace()
    )


def _same_company(company_name: str, aliases: list[str] | None, target: str) -> bool:
    return normalize_text(company_name) == target or any(
        normalize_text(alias) == target for alias in aliases or []
    )


def find_duplicates(
    db: Session,
    source_url: str,
    company_name: str,
    title: str,
) -> list[DuplicateCandidate]:
    target_url = normalize_url(source_url)
    target_company = normalize_text(company_name)
    target_title = normalize_text(title)
    candidates: list[DuplicateCandidate] = []

    statement = select(Job).options(joinedload(Job.company)).order_by(Job.id)
    for job in db.scalars(statement).all():
        reasons: list[str] = []
        if job.source_url:
            try:
                if normalize_url(job.source_url) == target_url:
                    reasons.append("source_url")
            except ValueError:
                pass
        if _same_company(job.company.name, job.company.aliases, target_company) and (
            normalize_text(job.title) == target_title
        ):
            reasons.append("company_title")
        if reasons:
            candidates.append(
                DuplicateCandidate(
                    job_id=job.id,
                    company_name=job.company.name,
                    title=job.title,
                    source_url=job.source_url,
                    reasons=reasons,
                )
            )
    return candidates
