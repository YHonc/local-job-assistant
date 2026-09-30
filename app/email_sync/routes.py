from __future__ import annotations

import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.guards import require_credential_store, restore_secret
from app.credentials import CredentialStore, CredentialStoreError
from app.database.models import (
    Application,
    Company,
    EmailAccount,
    EmailArchive,
    EmailCleaningResult,
    EmailJobEntry,
    EmailMessage,
    Job,
    Notification,
    RecruitmentEvent,
    Setting,
)
from app.database.session import get_db
from app.llm.service import LLMConfigurationError, LLMError, LLMOutputError, LLMTimeoutError

from .archiving import ArchiveBusy, get_archive, job_write_lock
from .filtering import DEFAULT_EMAIL_KEYWORDS, keyword_matches, regex_matches
from .matching import find_application_candidates, find_job_candidates
from .presets import EmailProviderType, resolve_imap_settings
from .provider import EmailAuthenticationError, EmailNetworkError, EmailProviderError
from .schemas import (
    AssociationUpdate,
    EmailAccountCreate,
    EmailAccountOut,
    EmailAccountPatch,
    EmailArchiveOut,
    EmailCleaningOut,
    EmailFilterConfig,
    EmailFilterPreviewIn,
    EmailFilterPreviewOut,
    EmailJobAssociationUpdate,
    EmailJobEntryOut,
    EmailMessageDeleteResult,
    EmailMessageDetail,
    EmailMessageOut,
    ManualEmailMessageIn,
    ProcessMessageResult,
    RecruitmentEventOut,
    SyncResult,
)
from .service import (
    AccountSyncBusy,
    EmailSyncConfigurationError,
    EmailSyncCoordinator,
    load_keywords,
    load_regex_filter,
    safe_processing_error,
)


router = APIRouter(prefix="/api", tags=["email"])


def _coordinator(request: Request) -> EmailSyncCoordinator:
    coordinator = getattr(request.app.state, "email_sync_coordinator", None)
    if coordinator is None:
        raise HTTPException(status_code=503, detail="email sync service unavailable")
    return coordinator


def _require_account(db: Session, account_id: int) -> EmailAccount:
    account = db.get(EmailAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="email account not found")
    return account


def _account_out(account: EmailAccount, store: CredentialStore) -> EmailAccountOut:
    has_secret = False
    if account.credential_id:
        try:
            has_secret = bool(store.get(account.credential_id))
        except CredentialStoreError as exc:
            raise HTTPException(status_code=503, detail="credential store unavailable") from exc
    return EmailAccountOut(
        id=account.id,
        email_address=account.email_address,
        provider_type=account.provider_type,
        imap_host=account.imap_host,
        imap_port=account.imap_port,
        use_ssl=account.use_ssl,
        username=account.username,
        enabled=account.enabled,
        check_interval_minutes=account.check_interval_minutes,
        sync_lookback_days=account.sync_lookback_days,
        uid_validity=account.uid_validity,
        last_uid=account.last_uid,
        last_sync_at=account.last_sync_at,
        last_sync_status=account.last_sync_status,
        last_sync_error=account.last_sync_error,
        has_authorization_code=has_secret,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


@router.post("/email-accounts", response_model=EmailAccountOut, status_code=201)
def create_email_account(
    payload: EmailAccountCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> EmailAccountOut:
    store = require_credential_store(request)
    try:
        imap = resolve_imap_settings(
            payload.provider_type,
            payload.email_address,
            imap_host=payload.imap_host,
            imap_port=payload.imap_port,
            use_ssl=payload.use_ssl,
            username=payload.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    credential_id = None
    if payload.authorization_code is not None:
        credential_id = f"email:{uuid4()}"
        try:
            store.set(credential_id, payload.authorization_code.get_secret_value())
        except CredentialStoreError as exc:
            raise HTTPException(status_code=503, detail="credential store unavailable") from exc

    account = EmailAccount(
        email_address=payload.email_address,
        provider_type=payload.provider_type.value,
        imap_host=imap.host,
        imap_port=imap.port,
        use_ssl=imap.use_ssl,
        username=imap.username,
        enabled=payload.enabled,
        check_interval_minutes=payload.check_interval_minutes,
        sync_lookback_days=payload.sync_lookback_days,
        credential_id=credential_id,
    )
    try:
        db.add(account)
        db.commit()
        db.refresh(account)
    except IntegrityError as exc:
        db.rollback()
        if credential_id:
            restore_secret(store, credential_id, None)
        raise HTTPException(status_code=409, detail="email account already exists") from exc
    except Exception as exc:
        db.rollback()
        rollback_ok = not credential_id or restore_secret(store, credential_id, None)
        detail = "unable to save email account"
        if not rollback_ok:
            detail += "; credential rollback failed"
        raise HTTPException(status_code=500, detail=detail) from exc
    return _account_out(account, store)


@router.get("/email-accounts", response_model=list[EmailAccountOut])
def list_email_accounts(
    request: Request,
    db: Session = Depends(get_db),
) -> list[EmailAccountOut]:
    store = require_credential_store(request)
    accounts = db.scalars(select(EmailAccount).order_by(EmailAccount.id)).all()
    return [_account_out(account, store) for account in accounts]


@router.get("/email-accounts/{account_id}", response_model=EmailAccountOut)
def get_email_account(
    account_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> EmailAccountOut:
    return _account_out(_require_account(db, account_id), require_credential_store(request))


@router.patch("/email-accounts/{account_id}", response_model=EmailAccountOut)
def patch_email_account(
    account_id: int,
    payload: EmailAccountPatch,
    request: Request,
    db: Session = Depends(get_db),
) -> EmailAccountOut:
    account = _require_account(db, account_id)
    store = require_credential_store(request)
    values = payload.model_dump(exclude_unset=True)

    email_address = values.get("email_address", account.email_address)
    provider = values.get("provider_type", EmailProviderType(account.provider_type))
    if isinstance(provider, str):
        provider = EmailProviderType(provider)
    host = values.get("imap_host", account.imap_host)
    port = values.get("imap_port", account.imap_port)
    use_ssl = values.get("use_ssl", account.use_ssl)
    username = values.get("username", account.username)
    try:
        imap = resolve_imap_settings(
            provider,
            email_address,
            imap_host=host,
            imap_port=port,
            use_ssl=use_ssl,
            username=username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    old_credential_id = account.credential_id
    action = "set" if payload.authorization_code is not None else (
        "clear" if payload.clear_authorization_code else None
    )
    action_id = old_credential_id or f"email:{uuid4()}"
    old_secret = None
    if action:
        try:
            old_secret = store.get(action_id)
            if action == "set":
                store.set(action_id, payload.authorization_code.get_secret_value())
                account.credential_id = action_id
            else:
                store.delete(action_id)
                account.credential_id = None
        except CredentialStoreError as exc:
            raise HTTPException(status_code=503, detail="credential store unavailable") from exc

    account.email_address = email_address
    account.provider_type = provider.value
    account.imap_host = imap.host
    account.imap_port = imap.port
    account.use_ssl = imap.use_ssl
    account.username = imap.username
    if payload.enabled is not None:
        account.enabled = payload.enabled
    if payload.check_interval_minutes is not None:
        account.check_interval_minutes = payload.check_interval_minutes
    if "sync_lookback_days" in values:
        account.sync_lookback_days = values["sync_lookback_days"]

    try:
        db.commit()
        db.refresh(account)
    except IntegrityError as exc:
        db.rollback()
        if action:
            restore_secret(store, action_id, old_secret)
        raise HTTPException(status_code=409, detail="email account already exists") from exc
    except Exception as exc:
        db.rollback()
        rollback_ok = not action or restore_secret(store, action_id, old_secret)
        detail = "unable to update email account"
        if not rollback_ok:
            detail += "; credential rollback failed"
        raise HTTPException(status_code=500, detail=detail) from exc
    return _account_out(account, store)


@router.delete("/email-accounts/{account_id}", status_code=204)
def delete_email_account(
    account_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    account = _require_account(db, account_id)
    store = require_credential_store(request)
    old_secret = None
    if account.credential_id:
        try:
            old_secret = store.get(account.credential_id)
            store.delete(account.credential_id)
        except CredentialStoreError as exc:
            raise HTTPException(status_code=503, detail="credential store unavailable") from exc
    credential_id = account.credential_id
    try:
        db.delete(account)
        db.commit()
    except Exception as exc:
        db.rollback()
        rollback_ok = (
            not credential_id or restore_secret(store, credential_id, old_secret)
        )
        detail = "unable to delete email account"
        if not rollback_ok:
            detail += "; credential rollback failed"
        raise HTTPException(status_code=500, detail=detail) from exc


def _map_email_error(exc: Exception) -> HTTPException:
    # Endpoint helpers may call another endpoint implementation directly (for
    # example cleaning retry).  Preserve an already classified HTTP error
    # instead of turning a useful 4xx/5xx into a generic 500.
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, LLMTimeoutError):
        return HTTPException(status_code=504, detail="LLM request timed out")
    if isinstance(exc, LLMOutputError):
        return HTTPException(status_code=502, detail="invalid structured LLM output")
    if isinstance(exc, LLMConfigurationError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, LLMError):
        return HTTPException(status_code=502, detail="LLM request failed")
    if isinstance(exc, EmailAuthenticationError):
        return HTTPException(status_code=401, detail="IMAP authentication failed")
    if isinstance(exc, EmailNetworkError):
        return HTTPException(status_code=502, detail="IMAP connection failed")
    if isinstance(exc, EmailSyncConfigurationError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, AccountSyncBusy):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, EmailProviderError):
        return HTTPException(status_code=502, detail="IMAP request failed")
    return HTTPException(status_code=500, detail="email operation failed")


@router.post("/email-accounts/{account_id}/test-connection")
def test_email_connection(
    account_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    account = _require_account(db, account_id)
    try:
        _coordinator(request).service.test_connection(account)
    except Exception as exc:
        raise _map_email_error(exc) from exc
    return {"status": "ok"}


@router.post("/email-accounts/{account_id}/sync", response_model=SyncResult)
def sync_email_account(
    account_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> SyncResult:
    try:
        return _coordinator(request).sync(db, account_id)
    except Exception as exc:
        raise _map_email_error(exc) from exc


@router.get("/email-messages", response_model=list[EmailMessageOut])
def list_email_messages(
    account_id: int | None = Query(default=None),
    pre_filtered: bool | None = Query(default=None),
    llm_processed: bool | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[EmailMessageOut]:
    statement = select(EmailMessage)
    if account_id is not None:
        statement = statement.where(EmailMessage.email_account_id == account_id)
    if pre_filtered is not None:
        statement = statement.where(EmailMessage.pre_filtered == pre_filtered)
    if llm_processed is not None:
        statement = statement.where(EmailMessage.llm_processed == llm_processed)
    if q and q.strip():
        search = f"%{q.strip()}%"
        statement = statement.where(
            or_(
                EmailMessage.subject.ilike(search),
                EmailMessage.sender.ilike(search),
                EmailMessage.body_text.ilike(search),
            )
        )
    messages = db.scalars(
        statement.order_by(
            EmailMessage.received_at.desc().nulls_last(), EmailMessage.id.desc()
        )
        .offset(offset)
        .limit(limit)
    ).all()
    return [_email_message_out(message, db) for message in messages]


@router.get("/email-messages/{message_id}", response_model=EmailMessageDetail)
def get_email_message(
    message_id: int,
    db: Session = Depends(get_db),
) -> EmailMessageDetail:
    message = db.get(EmailMessage, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="email message not found")
    event_ids = list(
        db.scalars(
            select(RecruitmentEvent.id)
            .where(
                RecruitmentEvent.source_email_id == message_id,
                RecruitmentEvent.active.is_(True),
            )
            .order_by(RecruitmentEvent.id)
        ).all()
    )
    base = _email_message_out(message, db)
    cleaning = db.scalar(
        select(EmailCleaningResult).where(
            EmailCleaningResult.email_message_id == message_id
        )
    )
    return EmailMessageDetail(
        **base.model_dump(),
        body_text=message.body_text,
        event_id=event_ids[0] if event_ids else None,
        event_ids=event_ids,
        cleaning=_cleaning_out(db, cleaning) if cleaning is not None else None,
        events=[_event_out(db, db.get(RecruitmentEvent, event_id)) for event_id in event_ids],
    )


@router.delete("/email-messages/{message_id}", response_model=EmailMessageDeleteResult)
def delete_email_message(
    message_id: int,
    db: Session = Depends(get_db),
) -> EmailMessageDeleteResult:
    """Delete the local copy of an email plus everything derived from it.

    Only local rows are removed: the original message stays on the mail server,
    and incremental IMAP sync (fetch_since last_uid) never re-imports old UIDs.
    """
    message = db.get(EmailMessage, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="email message not found")
    events = list(
        db.scalars(
            select(RecruitmentEvent).where(
                RecruitmentEvent.source_email_id == message_id
            )
        ).all()
    )
    notifications_deleted = 0
    for event in events:
        notifications_deleted += db.execute(
            delete(Notification).where(Notification.event_id == event.id)
        ).rowcount or 0
        db.delete(event)
    db.delete(message)
    db.commit()
    return EmailMessageDeleteResult(
        deleted=True,
        email_id=message_id,
        event_id=events[0].id if events else None,
        notifications_deleted=notifications_deleted,
    )


@router.post("/email-messages/{message_id}/process", response_model=ProcessMessageResult)
def process_email_message(
    message_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> ProcessMessageResult:
    message = db.get(EmailMessage, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="email message not found")
    service = _coordinator(request).service
    try:
        result, event = service.process_message(db, message)
        db.commit()
        _auto_archive_message(db, service, message_id)
        if service.notification_service is not None:
            _notify_message_events(db, service, message_id)
        return result
    except IntegrityError as exc:
        db.rollback()
        existing_event = db.scalars(
            select(RecruitmentEvent)
            .where(RecruitmentEvent.source_email_id == message_id)
            .order_by(RecruitmentEvent.id)
        ).first()
        if existing_event is not None:
            return ProcessMessageResult(
                message_id=message_id,
                event_id=existing_event.id,
                classification=existing_event.event_type,
            )
        raise HTTPException(
            status_code=409,
            detail="email message processing conflicted with another request",
        ) from exc
    except LLMTimeoutError as exc:
        message.llm_attempt_count += 1
        service._set_cleaning_failure(db, message, exc)
        message.processing_error = "LLMTimeoutError"
        db.commit()
        raise HTTPException(status_code=504, detail="LLM request timed out") from exc
    except LLMOutputError as exc:
        message.llm_attempt_count += 1
        service._set_cleaning_failure(db, message, exc)
        message.processing_error = "LLMOutputError"
        db.commit()
        raise HTTPException(status_code=502, detail="invalid structured LLM output") from exc
    except LLMConfigurationError as exc:
        message.llm_attempt_count += 1
        service._set_cleaning_failure(db, message, exc)
        message.processing_error = "LLMConfigurationError"
        db.commit()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMError as exc:
        message.llm_attempt_count += 1
        service._set_cleaning_failure(db, message, exc)
        message.processing_error = "LLMError"
        db.commit()
        raise HTTPException(status_code=502, detail="LLM request failed") from exc
    except EmailSyncConfigurationError as exc:
        service._set_cleaning_failure(db, message, exc)
        message.processing_error = str(exc)[:1000]
        db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _notify_message_events(db: Session, service, message_id: int) -> None:
    """Notify every active event for a multi-entry email exactly once."""
    events = db.scalars(
        select(RecruitmentEvent)
        .where(
            RecruitmentEvent.source_email_id == message_id,
            RecruitmentEvent.active.is_(True),
        )
        .order_by(RecruitmentEvent.id)
    ).all()
    for event in events:
        try:
            service.notification_service.notify_event(db, event)
        except Exception:
            # Notification service owns its retry/error bookkeeping; parsing
            # remains committed and independently retryable.
            continue


def _auto_archive_message(db: Session, service, message_id: int) -> None:
    """Run Job matching after the cleaning transaction has committed."""
    result = db.scalar(
        select(EmailCleaningResult).where(
            EmailCleaningResult.email_message_id == message_id
        )
    )
    if result is None or result.status not in {"completed", "submitted"}:
        return
    try:
        service.archive_cleaning(db, result.id)
    except Exception:
        # archive_cleaning records a retryable failed state; the successful
        # cleaning response remains available without another AI call.
        return
    message = db.get(EmailMessage, message_id)
    if message is not None and message.processing_error is not None:
        message.processing_error = None
        db.commit()


def _persist_reprocess_failure(db: Session, service, message_id: int, exc: Exception) -> None:
    """Keep an explicit reprocess failure visible while retaining old facts.

    The previous successful cleaning fields and active entries remain on the
    unique per-message snapshot; marking that snapshot failed gives retry a
    durable signal without making the user refetch the email or losing the old
    result during a transient model failure.
    """
    db.rollback()
    message = db.get(EmailMessage, message_id)
    if message is None:
        return
    if isinstance(exc, (LLMTimeoutError, LLMOutputError, LLMConfigurationError, LLMError)):
        message.llm_attempt_count += 1
    service._set_cleaning_failure(db, message, exc)
    message.processing_error = safe_processing_error(exc)
    db.commit()


def _require_cleaning(db: Session, cleaning_id: int) -> EmailCleaningResult:
    result = db.get(EmailCleaningResult, cleaning_id)
    if result is None:
        raise HTTPException(status_code=404, detail="email cleaning result not found")
    return result


@router.post("/email-messages/{message_id}/reprocess", response_model=ProcessMessageResult)
def reprocess_email_message(
    message_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> ProcessMessageResult:
    """Explicitly create a new cleaning version; successful cache is bypassed."""
    message = db.get(EmailMessage, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="email message not found")
    service = _coordinator(request).service
    try:
        result, _ = service.process_message(db, message, force=True)
        db.commit()
        _auto_archive_message(db, service, message_id)
        if service.notification_service is not None:
            _notify_message_events(db, service, message_id)
        return result
    except (LLMTimeoutError, LLMOutputError, LLMConfigurationError, LLMError, EmailSyncConfigurationError) as exc:
        _persist_reprocess_failure(db, service, message_id, exc)
        raise _map_email_error(exc) from exc
    except Exception as exc:
        _persist_reprocess_failure(db, service, message_id, exc)
        raise _map_email_error(exc) from exc


@router.get("/email-cleaning", response_model=list[EmailCleaningOut])
def list_email_cleaning(
    status_filter: str | None = Query(default=None, alias="status"),
    account_id: int | None = Query(default=None),
    include_stale: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[EmailCleaningOut]:
    statement = select(EmailCleaningResult).join(EmailMessage)
    if status_filter:
        statement = statement.where(EmailCleaningResult.status == status_filter)
    if account_id is not None:
        statement = statement.where(EmailMessage.email_account_id == account_id)
    results = db.scalars(
        statement.order_by(EmailCleaningResult.id.desc()).limit(limit)
    ).all()
    return [_cleaning_out(db, result) for result in results]


@router.post("/email-cleaning/{cleaning_id}/reprocess", response_model=EmailCleaningOut)
def reprocess_email_cleaning(cleaning_id: int, request: Request, db: Session = Depends(get_db)):
    cleaning = _require_cleaning(db, cleaning_id)
    reprocess_email_message(cleaning.email_message_id, request, db)
    return _cleaning_out(db, _require_cleaning(db, cleaning_id))


@router.get("/email-cleaning/{cleaning_id}", response_model=EmailCleaningOut)
def get_email_cleaning(
    cleaning_id: int,
    db: Session = Depends(get_db),
) -> EmailCleaningOut:
    return _cleaning_out(db, _require_cleaning(db, cleaning_id))


@router.post("/email-cleaning/{cleaning_id}/retry", response_model=EmailCleaningOut)
def retry_email_cleaning(
    cleaning_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> EmailCleaningOut:
    result = _require_cleaning(db, cleaning_id)
    if result.status in {"completed", "submitted"}:
        # Idempotent successful read: no provider call and no version bump.
        return _cleaning_out(db, result)
    message = db.get(EmailMessage, result.email_message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="email message not found")
    service = _coordinator(request).service
    try:
        process_email_message(message.id, request, db)
    except Exception as exc:
        db.rollback()
        raise _map_email_error(exc) from exc
    return _cleaning_out(db, _require_cleaning(db, cleaning_id))


@router.post("/email-cleaning/{cleaning_id}/submit", response_model=EmailCleaningOut)
def submit_email_cleaning(
    cleaning_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> EmailCleaningOut:
    """Submit clean facts and immediately run the durable archive stage."""
    result = _require_cleaning(db, cleaning_id)
    if result.status == "failed":
        raise HTTPException(status_code=409, detail="email cleaning result failed; retry first")
    if result.status == "completed":
        result.status = "submitted"
        db.commit()
    service = _coordinator(request).service
    try:
        service.archive_cleaning(db, cleaning_id)
    except (ArchiveBusy, LookupError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="email archive failed") from exc
    return _cleaning_out(db, _require_cleaning(db, cleaning_id))


@router.post("/email-cleaning/{cleaning_id}/archive", response_model=EmailArchiveOut)
def archive_email_cleaning(
    cleaning_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> EmailArchiveOut:
    result = _require_cleaning(db, cleaning_id)
    service = _coordinator(request).service
    try:
        archive = service.archive_cleaning(db, cleaning_id)
    except (ArchiveBusy, LookupError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="email archive failed") from exc
    db.get(EmailMessage, result.email_message_id).processing_error = None
    db.commit()
    return EmailArchiveOut.model_validate(archive)


@router.get("/email-cleaning/{cleaning_id}/archive", response_model=EmailArchiveOut | None)
def get_email_archive(
    cleaning_id: int,
    db: Session = Depends(get_db),
) -> EmailArchiveOut | None:
    _require_cleaning(db, cleaning_id)
    archive = get_archive(db, cleaning_id)
    return EmailArchiveOut.model_validate(archive) if archive is not None else None


@router.get("/email-job-entries", response_model=list[EmailJobEntryOut])
def list_email_job_entries(
    cleaning_id: int | None = Query(default=None),
    match_status: str | None = Query(default=None),
    active: bool | None = Query(default=True),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[EmailJobEntryOut]:
    statement = select(EmailJobEntry)
    if cleaning_id is not None:
        statement = statement.where(EmailJobEntry.cleaning_result_id == cleaning_id)
    if match_status:
        statement = statement.where(EmailJobEntry.match_status == match_status)
    if active is not None:
        statement = statement.where(EmailJobEntry.active == active)
    entries = db.scalars(statement.order_by(EmailJobEntry.id).limit(limit)).all()
    return [_job_entry_out(entry) for entry in entries]


@router.post("/email-job-entries/{entry_id}/rematch", response_model=EmailJobEntryOut)
def rematch_email_job_entry(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> EmailJobEntryOut:
    entry = db.get(EmailJobEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="email job entry not found")
    if entry.manually_associated:
        return _job_entry_out(entry)
    entry.match_status = "unmatched"
    entry.match_reason = None
    entry.company_id = None
    entry.job_id = None
    entry.application_id = None
    service = _coordinator(request).service
    try:
        with job_write_lock:
            service._match_job_entry(db, entry)
            db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="job rematch failed") from exc
    return _job_entry_out(entry)


@router.patch("/email-job-entries/{entry_id}/association", response_model=EmailJobEntryOut)
def update_email_job_association(
    entry_id: int,
    payload: EmailJobAssociationUpdate,
    db: Session = Depends(get_db),
) -> EmailJobEntryOut:
    entry = db.get(EmailJobEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="email job entry not found")
    event = db.get(RecruitmentEvent, entry.recruitment_event_id) if entry.recruitment_event_id else None
    if payload.dismiss:
        entry.company_id = None
        entry.job_id = None
        entry.application_id = None
        entry.match_status = "dismissed"
        entry.match_reason = "dismissed by user"
        entry.manually_associated = True
        if event is not None:
            event.company_id = event.job_id = event.application_id = None
            event.association_status = "dismissed"
    else:
        company_id = payload.company_id
        job_id = payload.job_id
        application_id = payload.application_id
        application = db.get(Application, application_id) if application_id is not None else None
        if application_id is not None and application is None:
            raise HTTPException(status_code=404, detail="application not found")
        job = db.get(Job, job_id) if job_id is not None else (application.job if application else None)
        if job_id is not None and job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job is not None:
            if application is not None and application.job_id != job.id:
                raise HTTPException(status_code=422, detail="application does not belong to selected job")
            if company_id is not None and company_id != job.company_id:
                raise HTTPException(status_code=422, detail="company does not own selected job")
            company_id = job.company_id
            job_id = job.id
            if application is None:
                application = db.scalar(select(Application).where(Application.job_id == job.id))
        if company_id is not None and db.get(Company, company_id) is None:
            raise HTTPException(status_code=404, detail="company not found")
        entry.company_id = company_id
        entry.job_id = job_id
        entry.application_id = application.id if application else application_id
        entry.match_status = "manual"
        entry.match_reason = "associated by user"
        entry.manually_associated = True
        if event is not None:
            event.company_id = company_id
            event.job_id = job_id
            event.application_id = entry.application_id
            event.association_status = "confirmed"
    db.commit()
    db.refresh(entry)
    return _job_entry_out(entry)


def _job_entry_out(entry: EmailJobEntry) -> EmailJobEntryOut:
    from sqlalchemy.orm import object_session
    values = EmailJobEntryOut.model_validate(entry)
    db = object_session(entry)
    if db is not None:
        values.candidates = find_job_candidates(db, entry.company_name, entry.job_title, entry.work_location, entry.links)
    return values


def _cleaning_out(
    db: Session,
    result: EmailCleaningResult,
) -> EmailCleaningOut:
    archive = get_archive(db, result.id)
    active_entries = [entry for entry in result.job_entries if entry.active]
    first_entry = active_entries[0] if active_entries else None
    return EmailCleaningOut(
        id=result.id,
        email_message_id=result.email_message_id,
        status=result.status,
        version=result.version,
        cleaned_body=result.cleaned_body,
        summary=result.summary,
        company_name=result.company_name or (first_entry.company_name if first_entry else None),
        job_title=result.job_title or (first_entry.job_title if first_entry else None),
        work_location=result.work_location or (first_entry.work_location if first_entry else None),
        links=result.links or [],
        field_evidence=result.field_evidence,
        event_time=result.event_time,
        deadline=result.deadline,
        action_required=result.action_required,
        actions=result.actions or [],
        error=result.error,
        attempt_count=result.attempt_count,
        ai_processed_at=result.ai_processed_at,
        created_at=result.created_at,
        updated_at=result.updated_at,
        job_entries=[_job_entry_out(entry) for entry in active_entries],
        archive_status=archive.status if archive else None,
    )


def _email_message_out(message: EmailMessage, db: Session | None = None) -> EmailMessageOut:
    cleaning = (
        db.scalar(
            select(EmailCleaningResult).where(
                EmailCleaningResult.email_message_id == message.id
            )
        )
        if db is not None
        else None
    )
    entry_conditions = [EmailJobEntry.source_email_id == message.id]
    if cleaning is not None:
        entry_conditions.append(EmailJobEntry.cleaning_result_id == cleaning.id)
    entries = (
        list(
            db.scalars(
                select(EmailJobEntry)
                .where(or_(*entry_conditions))
                .order_by(EmailJobEntry.id)
            ).all()
        )
        if db is not None
        else []
    )
    archive = get_archive(db, cleaning.id) if db is not None and cleaning is not None else None
    active_entries = [entry for entry in entries if entry.active]
    first_entry = active_entries[0] if active_entries else None
    return EmailMessageOut(
        id=message.id,
        email_account_id=message.email_account_id,
        message_id=message.message_id,
        uid_validity=message.uid_validity,
        uid=message.uid,
        sender=message.sender,
        subject=message.subject,
        received_at=message.received_at,
        body_preview=(message.body_text or "")[:500] or None,
        pre_filtered=message.pre_filtered,
        llm_processed=message.llm_processed,
        llm_classification=message.llm_classification,
        llm_attempt_count=message.llm_attempt_count,
        processing_error=message.processing_error,
        synced_at=message.synced_at,
        summary=cleaning.summary if cleaning else None,
        company_name=(cleaning.company_name if cleaning else None) or (first_entry.company_name if first_entry else None),
        job_title=(cleaning.job_title if cleaning else None) or (first_entry.job_title if first_entry else None),
        work_location=(cleaning.work_location if cleaning else None) or (first_entry.work_location if first_entry else None),
        cleaning_status=cleaning.status if cleaning else None,
        cleaning_result_id=cleaning.id if cleaning else None,
        archive_status=archive.status if archive else None,
        job_entries=[_job_entry_out(entry) for entry in active_entries],
    )


# Manual messages live in their own uid_validity namespace so they can never
# collide with (or be overwritten by) real IMAP cursors.
MANUAL_UID_VALIDITY = -1


@router.post("/email-messages/manual", response_model=EmailMessageOut, status_code=201)
def create_manual_email_message(
    payload: ManualEmailMessageIn,
    db: Session = Depends(get_db),
) -> EmailMessageOut:
    account = _require_account(db, payload.account_id)
    identity_conditions = [
        and_(
            EmailMessage.uid_validity == MANUAL_UID_VALIDITY,
            EmailMessage.uid == payload.uid,
        )
    ]
    if payload.message_id:
        identity_conditions.append(EmailMessage.message_id == payload.message_id)
    duplicate = db.scalar(
        select(EmailMessage).where(
            and_(
                EmailMessage.email_account_id == account.id,
                or_(*identity_conditions),
            )
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="manual email already exists for this account")
    message = EmailMessage(
        email_account_id=account.id,
        message_id=payload.message_id,
        uid_validity=MANUAL_UID_VALIDITY,
        uid=payload.uid,
        sender=payload.sender[:500],
        subject=payload.subject[:1000],
        received_at=None,
        body_text=(payload.body_text or "")[:100_000] or None,
        # Manual entries skip pre-filtering on purpose, but still require LLM
        # classification and human association like every other message.
        pre_filtered=True,
        llm_processed=False,
    )
    db.add(message)
    try:
        db.commit()
        db.refresh(message)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="manual email already exists for this account") from exc
    return _email_message_out(message, db)


@router.get("/email-filter", response_model=EmailFilterConfig)
def get_email_filter(db: Session = Depends(get_db)) -> EmailFilterConfig:
    setting = db.get(Setting, "email_keywords")
    regex_setting = db.get(Setting, "email_regex")
    keywords = None
    if setting and setting.value:
        try:
            keywords = json.loads(setting.value)
        except Exception:
            keywords = None
    if keywords is None:
        keywords = list(DEFAULT_EMAIL_KEYWORDS)
    regex = regex_setting.value.strip() if regex_setting and regex_setting.value else None
    return EmailFilterConfig(keywords=keywords, regex=regex or None)


@router.post("/email-filter/preview", response_model=EmailFilterPreviewOut)
def preview_email_filter(
    payload: EmailFilterPreviewIn,
    db: Session = Depends(get_db),
) -> EmailFilterPreviewOut:
    """Preview local filtering without persisting the sample or calling AI."""
    regex = payload.regex if "regex" in payload.model_fields_set else load_regex_filter(db)
    keywords = load_keywords(db)
    regex_active = bool(regex)
    regex_match = regex_matches(
        payload.subject, payload.sender, payload.body_text, regex
    )
    keyword_match = keyword_matches(
        payload.subject, payload.sender, payload.body_text, keywords
    )
    matched = regex_match if regex_active else keyword_match
    if regex_active:
        reason = "regex matched" if regex_match else "regex did not match"
    else:
        reason = "keyword matched" if keyword_match else "no configured keyword matched"
    return EmailFilterPreviewOut(
        matched=matched,
        regex_active=regex_active,
        regex_match=regex_match,
        keyword_match=keyword_match,
        reason=reason,
    )


@router.put("/email-filter", response_model=EmailFilterConfig)
def put_email_filter(
    payload: EmailFilterConfig,
    db: Session = Depends(get_db),
) -> EmailFilterConfig:
    for key, value in (
        ("email_keywords", json.dumps(payload.keywords, ensure_ascii=False)),
        ("email_regex", payload.regex),
    ):
        setting = db.get(Setting, key)
        if setting is None:
            setting = Setting(key=key)
            db.add(setting)
        setting.value = value
    db.commit()
    return payload


def _event_out(db: Session, event: RecruitmentEvent) -> RecruitmentEventOut:
    candidates = find_application_candidates(db, event.company_name, event.job_title)
    values = RecruitmentEventOut.model_validate(event).model_dump()
    values["candidates"] = candidates
    return RecruitmentEventOut.model_validate(values)


@router.get("/recruitment-events", response_model=list[RecruitmentEventOut])
def list_recruitment_events(
    event_type: str | None = Query(default=None),
    association_status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[RecruitmentEventOut]:
    statement = select(RecruitmentEvent).where(RecruitmentEvent.active.is_(True))
    if event_type:
        statement = statement.where(RecruitmentEvent.event_type == event_type)
    if association_status:
        statement = statement.where(
            RecruitmentEvent.association_status == association_status
        )
    events = db.scalars(statement.order_by(RecruitmentEvent.id.desc()).limit(limit)).all()
    return [_event_out(db, event) for event in events]


@router.get("/recruitment-events/{event_id}", response_model=RecruitmentEventOut)
def get_recruitment_event(
    event_id: int,
    db: Session = Depends(get_db),
) -> RecruitmentEventOut:
    event = db.get(RecruitmentEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="recruitment event not found")
    return _event_out(db, event)


@router.patch(
    "/recruitment-events/{event_id}/association",
    response_model=RecruitmentEventOut,
)
def update_event_association(
    event_id: int,
    payload: AssociationUpdate,
    db: Session = Depends(get_db),
) -> RecruitmentEventOut:
    event = db.get(RecruitmentEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="recruitment event not found")

    if payload.application_id is not None:
        application = db.get(Application, payload.application_id)
        if application is None:
            raise HTTPException(status_code=404, detail="application not found")
        event.application_id = application.id
        event.job_id = application.job_id
        event.company_id = application.job.company_id
        event.association_status = "confirmed"
    else:
        event.application_id = None
        event.job_id = None
        event.company_id = None
        event.association_status = "dismissed" if payload.dismiss else "unmatched"
    if event.email_job_entry_id:
        entry = db.get(EmailJobEntry, event.email_job_entry_id)
        if entry is not None:
            entry.company_id, entry.job_id, entry.application_id = event.company_id, event.job_id, event.application_id
            entry.manually_associated = True
            entry.match_status = "manual" if event.job_id else "dismissed"
            entry.match_reason = "人工修改事件关联"
    db.commit()
    db.refresh(event)
    return _event_out(db, event)
