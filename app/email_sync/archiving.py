"""Recoverable, idempotent local archive state machine."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import EmailArchive, EmailCleaningResult, now


ARCHIVE_LEASE = timedelta(minutes=15)


class ArchiveBusy(RuntimeError):
    pass


_archive_guard = threading.Lock()
_archive_locks: dict[int, threading.Lock] = {}
# Serialize the short match-and-create transaction across different emails.
# SQLite is the supported store; take its writer lock before checking duplicates.
job_write_lock = threading.RLock()


def _lock_for(cleaning_id: int) -> threading.Lock:
    with _archive_guard:
        return _archive_locks.setdefault(cleaning_id, threading.Lock())


def get_archive(db: Session, cleaning_id: int) -> EmailArchive | None:
    return db.scalar(
        select(EmailArchive).where(EmailArchive.cleaning_result_id == cleaning_id)
    )


def archive_cleaning(
    db: Session,
    cleaning_id: int,
    matcher: Callable[[Session, object], object] | None = None,
) -> EmailArchive:
    """Archive one cleaning result exactly once.

    The archive stage is the durable database phase after cleaning: every
    active entry is matched directly to a Job, and a clear company/title pair
    may create Company, Job, and a 收藏 Application through ``matcher``.  The
    claim and completion are separate commits so a failed entry can be retried
    without holding a long transaction or invoking AI again.
    """

    lock = _lock_for(cleaning_id)
    if not lock.acquire(blocking=False):
        raise ArchiveBusy("archive operation is already running")
    claimed = False
    try:
        result = db.get(EmailCleaningResult, cleaning_id)
        if result is None:
            raise LookupError("email cleaning result not found")
        if result.status not in {"completed", "submitted"}:
            raise ValueError("email cleaning result is not ready for archive")
        current = now()
        record = get_archive(db, cleaning_id)
        if record is not None and record.status == "archived":
            return record
        if record is not None and record.status == "archiving" and record.lease_until and record.lease_until.replace(tzinfo=timezone.utc) > current:
            raise ArchiveBusy("archive operation is already running")
        if record is None:
            record = EmailArchive(
                cleaning_result_id=cleaning_id,
                status="pending",
                requested_at=current,
            )
            db.add(record)
            db.flush()
        record.status = "archiving"
        record.lease_until = current + ARCHIVE_LEASE
        record.error = None
        db.commit()
        claimed = True

        if matcher is not None:
            entries = list(result.job_entries)
            for entry in entries:
                if not entry.active or entry.manually_associated:
                    continue
                if entry.match_status in {"auto_bound", "auto_created"} and entry.job_id:
                    continue
                with job_write_lock:
                    matcher(db, entry)
                    db.commit()

        result = db.get(EmailCleaningResult, cleaning_id)
        result.status = "completed"
        record = db.get(EmailArchive, record.id)
        record.status = "archived"
        record.archived_at = now()
        record.lease_until = None
        record.error = None
        db.commit()
        db.refresh(record)
        return record
    except Exception:
        db.rollback()
        record = get_archive(db, cleaning_id)
        if claimed and record is not None and record.status == "archiving":
            record.status = "failed"
            record.lease_until = None
            record.error = "archive operation failed"
            db.commit()
        raise
    finally:
        lock.release()
