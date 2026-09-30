from __future__ import annotations

import re
import unicodedata
from functools import lru_cache


DEFAULT_EMAIL_KEYWORDS = (
    "招聘",
    "应聘",
    "申请",
    "简历",
    "校招",
    "面试",
    "笔试",
    "测评",
    "录用",
    "offer",
    "assessment",
    "interview",
    "application",
    "recruitment",
)


def normalize_search_text(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold()


def keyword_matches(
    subject: str | None,
    sender: str | None,
    body_text: str | None,
    keywords: list[str] | tuple[str, ...],
) -> bool:
    haystack = "\n".join(
        normalize_search_text(value)
        for value in (subject, sender, (body_text or "")[:30_000])
    )
    return any(
        normalized in haystack
        for keyword in keywords
        if (normalized := normalize_search_text(keyword).strip())
    )


@lru_cache(maxsize=32)
def _compiled_pattern(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


def regex_matches(
    subject: str | None,
    sender: str | None,
    body_text: str | None,
    pattern: str | None,
) -> bool:
    """Optional first-pass filter; an absent or invalid pattern passes everything."""
    if not pattern or not pattern.strip():
        return True
    haystack = "\n".join(
        value or "" for value in (subject, sender, (body_text or "")[:30_000])
    )
    try:
        return _compiled_pattern(pattern.strip()).search(haystack) is not None
    except re.error:
        return True
