"""Read-only time series; SQLite timestamps are stored in UTC."""

from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.database.models import LLMConfiguration, LLMUsageEvent

USAGE_TIMEZONE = timezone(timedelta(hours=8))


class UsageTotals(BaseModel):
    call_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    average_duration_ms: float | None = None
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0


class UsagePoint(UsageTotals):
    timestamp: datetime
    available: bool


class UsageSeries(BaseModel):
    start: date
    end: date
    timezone: str = "UTC+08:00"
    granularity: Literal["hour", "day"]
    recorded_from: datetime | None
    last_used_at: datetime | None
    legacy_call_count: int
    lifetime: UsageTotals
    summary: UsageTotals
    points: list[UsagePoint]
    by_purpose: dict[str, UsageTotals] = Field(default_factory=dict)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def usage_series(db: Session, config_id: int, start: date | None, end: date | None) -> UsageSeries:
    config = db.get(LLMConfiguration, config_id)
    if config is None:
        raise HTTPException(404, "model configuration not found")
    current = datetime.now(USAGE_TIMEZONE)
    end = end or current.date()
    start = start or end - timedelta(days=6)
    if start > end or (end - start).days > 365 or end > current.date():
        raise HTTPException(422, "请选择有效日期：开始不晚于结束，最多 366 天，结束不晚于今天。")
    first, tracked = db.execute(select(func.min(LLMUsageEvent.recorded_at), func.count())
        .where(LLMUsageEvent.config_id == config_id)).one()
    first = _utc(first)
    hourly = start == end
    step = timedelta(hours=1) if hourly else timedelta(days=1)
    begin = datetime.combine(start, time.min, USAGE_TIMEZONE)
    finish = datetime.combine(end + timedelta(days=1), time.min, USAGE_TIMEZONE)
    bucket = func.strftime("%Y-%m-%d %H:00" if hourly else "%Y-%m-%d", LLMUsageEvent.recorded_at, "+8 hours")
    rows = db.execute(select(
        bucket.label("bucket"), func.count().label("calls"),
        func.sum(case((LLMUsageEvent.success.is_(True), 1), else_=0)).label("successes"),
        func.sum(LLMUsageEvent.duration_ms).label("duration"),
        func.sum(LLMUsageEvent.prompt_tokens).label("prompt"),
        func.sum(LLMUsageEvent.completion_tokens).label("completion"),
    ).where(
        LLMUsageEvent.config_id == config_id,
        LLMUsageEvent.recorded_at >= begin.astimezone(timezone.utc),
        LLMUsageEvent.recorded_at < finish.astimezone(timezone.utc),
    ).group_by(bucket)).all()
    by_bucket = {row.bucket: row for row in rows}
    points = []
    cursor = begin
    while cursor < finish:
        row = by_bucket.get(cursor.strftime("%Y-%m-%d %H:00" if hourly else "%Y-%m-%d"))
        values = UsageTotals() if row is None else UsageTotals(
            call_count=row.calls, success_count=row.successes, failure_count=row.calls - row.successes,
            average_duration_ms=row.duration / row.calls,
            total_prompt_tokens=row.prompt, total_completion_tokens=row.completion,
            total_tokens=row.prompt + row.completion,
        )
        # Earlier buckets have unknown history, not zero activity. Future buckets are also omitted.
        available = first is not None and cursor + step > first and cursor <= current
        points.append(UsagePoint(timestamp=cursor, available=available, **values.model_dump()))
        cursor += step
    calls = sum(row.calls for row in rows)
    successes = sum(row.successes for row in rows)
    prompt = sum(row.prompt for row in rows)
    completion = sum(row.completion for row in rows)
    purposes = db.execute(select(LLMUsageEvent.purpose, func.count(),
        func.sum(case((LLMUsageEvent.success.is_(True), 1), else_=0)),
        func.sum(LLMUsageEvent.duration_ms), func.sum(LLMUsageEvent.prompt_tokens),
        func.sum(LLMUsageEvent.completion_tokens)).where(
            LLMUsageEvent.config_id == config_id,
            LLMUsageEvent.recorded_at >= begin.astimezone(timezone.utc),
            LLMUsageEvent.recorded_at < finish.astimezone(timezone.utc),
        ).group_by(LLMUsageEvent.purpose)).all()
    by_purpose = {kind: UsageTotals(call_count=count, success_count=success, failure_count=count-success,
                    average_duration_ms=duration/count, total_prompt_tokens=prompt_tokens,
                    total_completion_tokens=completion_tokens, total_tokens=prompt_tokens+completion_tokens)
                  for kind, count, success, duration, prompt_tokens, completion_tokens in purposes}
    return UsageSeries(
        start=start, end=end, granularity="hour" if hourly else "day", recorded_from=first,
        last_used_at=_utc(config.last_used_at), legacy_call_count=max(0, config.call_count - tracked),
        lifetime=UsageTotals(
            call_count=config.call_count, success_count=config.success_count, failure_count=config.failure_count,
            average_duration_ms=config.total_duration_ms / config.call_count if config.call_count else None,
            total_prompt_tokens=config.total_prompt_tokens, total_completion_tokens=config.total_completion_tokens,
            total_tokens=config.total_prompt_tokens + config.total_completion_tokens,
        ),
        summary=UsageTotals(
            call_count=calls, success_count=successes, failure_count=calls - successes,
            average_duration_ms=sum(row.duration for row in rows) / calls if calls else None,
            total_prompt_tokens=prompt, total_completion_tokens=completion, total_tokens=prompt + completion,
        ), points=points, by_purpose=by_purpose,
    )
