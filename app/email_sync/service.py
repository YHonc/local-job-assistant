from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any

from sqlalchemy import and_, func, or_, select, update, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.orm import Session as OrmSession

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
    LLMRoutingSettings,
    RecruitmentEvent,
    Setting,
    now,
)
from app.llm.service import LLMError, LLMService, record_usage, select_config
from app.job_categories import resolve_job_category
from app.imports.duplicate import normalize_text

from .cleaning import (
    EMAIL_CLEANING_SYSTEM_PROMPT,
    build_cleaning_prompt,
    event_type_value,
    legacy_classification,
    normalize_cleaning_output,
)
from .archiving import ArchiveBusy, archive_cleaning as run_archive_cleaning
from .filtering import DEFAULT_EMAIL_KEYWORDS, keyword_matches, regex_matches
from .matching import (
    find_application_candidates,
    find_job_candidates,
    reliable_job_match,
    safe_normalize_url,
    suggested_application_id,
)
from .provider import EmailFetchBatch, EmailProviderAdapter, EmailProviderError, ImapToolsProvider
from .schemas import (
    EmailClassification,
    EmailCleaningOutput,
    EmailJobExtraction,
    ProcessMessageResult,
    SyncResult,
)


# Kept as a public alias for integrations that imported the old prompt name.
EMAIL_SYSTEM_PROMPT = EMAIL_CLEANING_SYSTEM_PROMPT

# 连续 LLM 失败达到该次数后，自动同步不再为该邮件调用模型；
# 手动处理（POST /email-messages/{id}/process）不受此限制。
MAX_LLM_ATTEMPTS = 5


class EmailSyncConfigurationError(RuntimeError):
    pass


class AccountSyncBusy(RuntimeError):
    pass


def load_keywords(db: Session) -> list[str]:
    setting = db.get(Setting, "email_keywords")
    if setting and setting.value:
        try:
            value = json.loads(setting.value)
            if isinstance(value, list) and all(
                isinstance(item, str) and item.strip() for item in value
            ):
                return [item.strip() for item in value[:100]]
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return list(DEFAULT_EMAIL_KEYWORDS)


def load_regex_filter(db: Session) -> str | None:
    """Optional first-pass regex; stored only as a non-sensitive setting."""
    setting = db.get(Setting, "email_regex")
    if setting and setting.value and setting.value.strip():
        return setting.value.strip()
    return None


def safe_processing_error(exc: Exception) -> str:
    if isinstance(exc, EmailSyncConfigurationError):
        return str(exc)[:1000]
    if isinstance(exc, EmailProviderError):
        return str(exc)[:1000]
    if isinstance(exc, LLMError):
        return exc.__class__.__name__
    if isinstance(exc, SQLAlchemyError):
        return "database error"
    return "unexpected processing error"


class EmailSyncService:
    def __init__(
        self,
        credential_store: CredentialStore,
        *,
        completion: Callable[..., Any] | None = None,
        provider_factory: Callable[[EmailAccount, str], EmailProviderAdapter] | None = None,
        notification_service: Any | None = None,
    ) -> None:
        self.credential_store = credential_store
        self.completion = completion
        self.provider_factory = provider_factory or (
            lambda account, secret: ImapToolsProvider(account, secret)
        )
        self.notification_service = notification_service

    def _authorization_code(self, account: EmailAccount) -> str:
        if not account.credential_id:
            raise EmailSyncConfigurationError("email authorization code is not configured")
        try:
            secret = self.credential_store.get(account.credential_id)
        except CredentialStoreError as exc:
            raise EmailSyncConfigurationError("credential store unavailable") from exc
        if not secret:
            raise EmailSyncConfigurationError("email authorization code is not configured")
        return secret

    def provider(self, account: EmailAccount) -> EmailProviderAdapter:
        return self.provider_factory(account, self._authorization_code(account))

    def test_connection(self, account: EmailAccount) -> None:
        self.provider(account).test_connection()

    @staticmethod
    def _email_prompt(message: EmailMessage) -> str:
        return build_cleaning_prompt(message)

    def _classify_message(self, db: Session, prompt: str) -> EmailCleaningOutput:
        """Run one structured cleaning call and record usage.

        The output schema intentionally accepts the previous one-event keys;
        ``_apply_classification`` persists both the new multi-entry snapshot
        and the legacy event compatibility row from the same provider call.
        """
        # The optional email_config_id pins email parsing to one model; without
        # it the global routing rules (auto-select / default) decide as before.
        # A paused or deleted pin falls back to global routing so sync survives.
        routing = db.get(LLMRoutingSettings, 1)
        pinned = routing.email_config_id if routing is not None else None
        try:
            try:
                config = select_config(db, pinned) if pinned else select_config(db)
            except LLMError:
                if not pinned:
                    raise
                config = select_config(db)
        except LLMError as exc:
            raise EmailSyncConfigurationError("LLM is not configured") from exc
        started = perf_counter()
        service = LLMService(config, self.credential_store, self.completion)
        try:
            classification = service.classify_email(
                EMAIL_CLEANING_SYSTEM_PROMPT, prompt, EmailCleaningOutput
            )
        except LLMError:
            usage = service.last_usage or {}
            record_usage(
                db,
                config.id,
                False,
                round((perf_counter() - started) * 1000),
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            )
            raise
        usage = service.last_usage or {}
        record_usage(
            db,
            config.id,
            True,
            round((perf_counter() - started) * 1000),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )
        return classification

    def process_message(
        self,
        db: Session,
        message: EmailMessage,
        *,
        force: bool = False,
    ) -> tuple[ProcessMessageResult, RecruitmentEvent | None]:
        """Clean one pre-filtered message and persist the resulting snapshot.

        A successful snapshot is a durable cache: ordinary reads/retries return
        it without another provider call.  ``force=True`` is reserved for an
        explicit reprocess request and increments the snapshot version.
        """
        if not message.pre_filtered:
            raise EmailSyncConfigurationError("email did not pass email pre-filter")
        cleaning = db.scalar(
            select(EmailCleaningResult).where(
                EmailCleaningResult.email_message_id == message.id
            )
        )
        existing_event = db.scalar(
            select(RecruitmentEvent).where(RecruitmentEvent.source_email_id == message.id)
        )
        if not force and (
            (cleaning is not None and cleaning.status in {"completed", "submitted"})
            or (cleaning is None and existing_event is not None)
        ):
            if existing_event is not None:
                classification = existing_event.event_type
                event_id = existing_event.id
            elif cleaning is not None:
                first_entry = db.scalar(
                    select(EmailJobEntry)
                    .where(EmailJobEntry.cleaning_result_id == cleaning.id)
                    .order_by(EmailJobEntry.id)
                )
                classification = first_entry.event_type if first_entry else (
                    "NOT_RECRUITMENT" if message.llm_classification == "NOT_RECRUITMENT" else "OTHER"
                )
                event_id = first_entry.recruitment_event_id if first_entry else None
            else:
                classification = "OTHER"
                event_id = None
            return (
                ProcessMessageResult(
                    message_id=message.id,
                    event_id=event_id,
                    classification=classification,
                ),
                existing_event,
            )
        if (
            not force
            and message.llm_processed
            and message.llm_classification == "NOT_RECRUITMENT"
        ):
            return (
                ProcessMessageResult(
                    message_id=message.id,
                    event_id=None,
                    classification="NOT_RECRUITMENT",
                ),
                None,
            )

        classification = self._classify_message(db, self._email_prompt(message))
        return self._apply_classification(db, message, classification, force=force)

    @staticmethod
    def _cleaning_for_message(db: Session, message_id: int) -> EmailCleaningResult | None:
        return db.scalar(
            select(EmailCleaningResult).where(
                EmailCleaningResult.email_message_id == message_id
            )
        )

    @staticmethod
    def _set_cleaning_failure(
        db: Session,
        message: EmailMessage,
        exc: Exception,
    ) -> EmailCleaningResult:
        """Persist a retryable failure without persisting provider details."""
        result = EmailSyncService._cleaning_for_message(db, message.id)
        if result is None:
            result = EmailCleaningResult(
                email_message_id=message.id,
                status="failed",
                version=1,
                attempt_count=1,
                error=safe_processing_error(exc),
            )
            db.add(result)
        else:
            result.status = "failed"
            result.attempt_count = (result.attempt_count or 0) + 1
            result.error = safe_processing_error(exc)
        return result

    def _apply_classification(
        self,
        db: Session,
        message: EmailMessage,
        classification: EmailCleaningOutput | EmailClassification,
        *,
        force: bool = False,
    ) -> tuple[ProcessMessageResult, RecruitmentEvent | None]:
        """Persist a finished cleaning snapshot and compatibility event.

        Automatic matching runs per ``EmailJobEntry``.  Existing manually
        associated entries are retained verbatim while stale automatic rows
        from a prior version are removed before the new output is inserted.
        """
        message.llm_processed = True
        message.llm_attempt_count = 0
        message.processing_error = None
        if isinstance(classification, EmailClassification):
            output = EmailCleaningOutput.model_validate(
                classification.model_dump(mode="json")
            )
        else:
            output = classification

        root, extracted = normalize_cleaning_output(output, message.body_text)
        result = self._cleaning_for_message(db, message.id)
        if result is None:
            result = EmailCleaningResult(email_message_id=message.id, version=1)
            db.add(result)
        elif force:
            result.version = (result.version or 1) + 1
        result.status = "completed"
        result.error = None
        result.attempt_count = 0
        result.ai_processed_at = now()
        result.cleaned_body = root.get("body") or message.body_text
        result.summary = root.get("summary")
        result.company_name = root.get("company")
        result.job_title = root.get("job")
        result.work_location = root.get("work_location")
        result.links = root.get("links") or []
        result.field_evidence = root.get("field_evidence")
        result.event_time = root.get("event_time")
        result.deadline = root.get("deadline")
        result.action_required = bool(root.get("action_required", False))
        result.actions = root.get("actions") or []
        result.structured_data = output.model_dump(mode="json")
        db.flush()
        archive = db.scalar(select(EmailArchive).where(EmailArchive.cleaning_result_id == result.id))
        if archive is not None:
            archive.status = "pending"
            archive.error = None
            archive.lease_until = None

        message.llm_classification = "NOT_RECRUITMENT" if not output.is_recruitment else (
            extracted[0].event_type.value if extracted else "OTHER"
        )

        existing_entries = list(
            db.scalars(
                select(EmailJobEntry)
                .where(EmailJobEntry.cleaning_result_id == result.id)
                .order_by(EmailJobEntry.id)
            ).all()
        )
        by_identity = {
            entry.identity_key: entry
            for entry in existing_entries
            if entry.identity_key
        }
        auto_entries = [
            entry
            for entry in existing_entries
            if not entry.manually_associated and entry.facts_source != "manual"
        ]
        old_events = {
            event.id: event
            for event in db.scalars(
                select(RecruitmentEvent).where(
                    RecruitmentEvent.source_email_id == message.id
                )
            ).all()
        }
        used_keys: set[str] = set()

        if not output.is_recruitment or not extracted:
            for entry in auto_entries:
                entry.active = False
                entry.match_status = "stale"
                entry.match_reason = "latest cleaning has no recruitment entry"
                if entry.recruitment_event_id and entry.recruitment_event_id in old_events:
                    old_events[entry.recruitment_event_id].active = False
            for event in old_events.values():
                if event.email_job_entry_id is None and event.association_status not in {
                    "confirmed",
                    "manual",
                }:
                    event.active = False
            return (
                ProcessMessageResult(
                    message_id=message.id,
                    event_id=None,
                    classification="NOT_RECRUITMENT",
                ),
                None,
            )

        for extracted_entry in extracted:
            identity_key = self._entry_identity(extracted_entry)
            if identity_key in used_keys:
                # A model may repeat the same event in both ``jobs`` and
                # ``events``; one stable entry is enough and avoids a unique
                # constraint race within this transaction.
                continue
            used_keys.add(identity_key)
            entry = by_identity.get(identity_key)
            if entry is None:
                entry = EmailJobEntry(cleaning_result_id=result.id, identity_key=identity_key)
                db.add(entry)
                db.flush()
                existing_entries.append(entry)
            elif not entry.manually_associated:
                entry.identity_key = identity_key
            if entry.facts_source != "manual":
                entry.active = True
                if not entry.manually_associated and entry.match_status == "stale":
                    entry.match_status = "unmatched"
                self._update_entry_facts(entry, extracted_entry)

            event = old_events.get(entry.recruitment_event_id) if entry.recruitment_event_id else None
            if event is None:
                # A legacy pre-0013 event has no entry link.  Reuse the oldest
                # unlinked event for the first entry to avoid duplicate
                # notifications during migration/reprocessing.
                event = next(
                    (candidate for candidate in old_events.values() if candidate.email_job_entry_id is None),
                    None,
                )
            if event is None:
                event = RecruitmentEvent(
                    source_email_id=message.id,
                    email_job_entry_id=entry.id,
                    event_type=event_type_value(extracted_entry.event_type),
                    confidence=output.confidence,
                    ai_summary=extracted_entry.summary or output.summary or "招聘邮件",
                )
                db.add(event)
                db.flush()
                old_events[event.id] = event
            if event.association_status in {"confirmed", "manual", "dismissed"}:
                entry.company_id, entry.job_id, entry.application_id = event.company_id, event.job_id, event.application_id
                entry.manually_associated = True
                entry.match_status = "manual" if event.job_id else "dismissed"
            event.email_job_entry_id = entry.id
            event.active = True
            self._update_legacy_event(db, event, output, extracted_entry)
            entry.recruitment_event_id = event.id
            # Job matching/creation is deliberately deferred to the archive
            # stage.  Cleaning can therefore be reviewed and submitted while
            # its structured facts are already durable.

        # Keep human rows even if a newer provider result omitted them.  Auto
        # rows whose stable identity disappeared are retained as inactive audit
        # records so event history and notification idempotency remain intact.
        for entry in auto_entries:
            if entry.identity_key not in used_keys:
                entry.active = False
                entry.match_status = "stale"
                entry.match_reason = "entry absent from latest cleaning version"
                if entry.recruitment_event_id and entry.recruitment_event_id in old_events:
                    old_events[entry.recruitment_event_id].active = False

        first_event = None
        for entry in existing_entries:
            if entry.active and entry.recruitment_event_id:
                first_event = old_events.get(entry.recruitment_event_id)
                if first_event is not None:
                    break
        return (
            ProcessMessageResult(
                message_id=message.id,
                event_id=first_event.id if first_event else None,
                classification=first_event.event_type if first_event else "OTHER",
            ),
            first_event,
        )

    def archive_cleaning(self, db: Session, cleaning_id: int):
        """Run the post-cleaning Job matching/creation stage."""
        return run_archive_cleaning(db, cleaning_id, self._match_job_entry)

    @staticmethod
    def _entry_identity(extracted: EmailJobExtraction) -> str:
        import hashlib

        payload = {
            "company": normalize_text(extracted.company),
            "job": normalize_text(extracted.job),
            "event_type": event_type_value(extracted.event_type),
            "event_round": extracted.event_round,
            "event_time": extracted.event_time.isoformat() if extracted.event_time else None,
            "deadline": extracted.deadline.isoformat() if extracted.deadline else None,
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:64]

    @staticmethod
    def _update_legacy_event(
        db: Session,
        event: RecruitmentEvent,
        output: EmailCleaningOutput,
        extracted: EmailJobExtraction,
    ) -> None:
        """Refresh event facts while retaining any human association."""
        event.company_name = extracted.company
        event.job_title = extracted.job
        event.event_type = event_type_value(extracted.event_type)
        event.event_time = extracted.event_time
        event.deadline = extracted.deadline
        # Legacy location is the event/meeting venue.  New work location stays
        # on EmailJobEntry and never leaks into this compatibility field.
        event.location = output.location if output.location else None
        event.meeting_url = extracted.meeting_url or output.meeting_url
        event.action_required = extracted.action_required
        event.confidence = output.confidence
        event.ai_summary = extracted.summary or output.summary or "No recruitment event detected."
        event.suggested_application_status = (
            extracted.suggested_application_status.value
            if extracted.suggested_application_status
            else (output.suggested_application_status.value if output.suggested_application_status else None)
        )
        if event.association_status not in {"confirmed", "manual", "dismissed"}:
            candidates = find_application_candidates(db, extracted.company, extracted.job)
            suggestion = suggested_application_id(candidates)
            event.suggested_application_id = suggestion
            event.association_status = "suggested" if suggestion else "unmatched"

    @staticmethod
    def _update_entry_facts(entry: EmailJobEntry, extracted: EmailJobExtraction) -> None:
        if entry.facts_source == "manual":
            return
        entry.company_name = extracted.company
        entry.job_title = extracted.job
        entry.work_location = extracted.work_location
        entry.links = list(extracted.links or [])
        entry.field_evidence = extracted.field_evidence
        entry.event_type = event_type_value(extracted.event_type)
        entry.event_round = extracted.event_round
        entry.event_time = extracted.event_time
        entry.deadline = extracted.deadline
        entry.action_required = bool(extracted.action_required)
        entry.actions = list(extracted.actions or [])
        entry.summary = extracted.summary

    @staticmethod
    def _find_company(db: Session, name: str | None) -> Company | None:
        target = normalize_text(name)
        if not target:
            return None
        for company in db.scalars(select(Company).order_by(Company.id)).all():
            if normalize_text(company.name) == target or any(
                normalize_text(alias) == target for alias in company.aliases or []
            ):
                return company
        return None

    def _match_job_entry(self, db: Session, entry: EmailJobEntry) -> EmailJobEntry:
        """Match against Job first, creating a complete local record if clear."""
        if entry.manually_associated:
            return entry
        if entry.active and entry.match_status in {"auto_bound", "auto_created"} and entry.job_id:
            return entry
        # Obtain SQLite's write reservation before reading candidates, including
        # requests from a second process. This prevents check-then-create races.
        db.execute(text("UPDATE email_job_entries SET id=id WHERE id=:id"), {"id": entry.id})
        candidates = find_job_candidates(db, entry.company_name, entry.job_title, entry.work_location, entry.links)
        reliable = reliable_job_match(candidates)
        if reliable is not None:
            entry.company_id = reliable.company_id
            entry.job_id = reliable.job_id
            entry.application_id = reliable.application_id
            entry.match_status = "auto_bound"
            entry.match_reason = "可靠候选：" + ", ".join(reliable.reasons)
            self._sync_entry_event(db, entry)
            return entry
        if candidates:
            entry.match_status = "needs_review"
            entry.match_reason = json.dumps(
                {
                    "candidates": [candidate.model_dump() for candidate in candidates[:20]],
                    "reason": "ambiguous or partial Job match",
                },
                ensure_ascii=False,
            )[:1000]
            return entry

        if not entry.company_name or not entry.job_title:
            entry.match_status = "unmatched"
            entry.match_reason = "company and job title are required for automatic creation"
            return entry

        company = self._find_company(db, entry.company_name)
        if company is None:
            company = Company(name=entry.company_name, aliases=[])
            db.add(company)
            db.flush()
        source_url = next(
            (link for link in entry.links or [] if safe_normalize_url(link)),
            None,
        )
        job = Job(
            company_id=company.id,
            title=entry.job_title,
            location=entry.work_location,
            source_url=source_url,
            source_website="邮件",
            description=entry.summary,
            job_category=resolve_job_category(
                None,
                title=entry.job_title,
                description=entry.summary,
            ),
            structured_data={"email_job_entry_id": entry.id},
        )
        db.add(job)
        db.flush()
        application = Application(job_id=job.id, status="收藏")
        db.add(application)
        db.flush()
        entry.company_id = company.id
        entry.job_id = job.id
        entry.application_id = application.id
        entry.match_status = "auto_created"
        entry.match_reason = "no candidate; created Company, Job, and 收藏 Application"
        self._sync_entry_event(db, entry)
        return entry

    @staticmethod
    def _sync_entry_event(db: Session, entry: EmailJobEntry) -> None:
        event = db.get(RecruitmentEvent, entry.recruitment_event_id) if entry.recruitment_event_id else None
        if event is not None and event.association_status not in {"confirmed", "manual", "dismissed"}:
            event.company_id = entry.company_id
            event.job_id = entry.job_id
            event.application_id = entry.application_id
            event.association_status = "auto_bound"

    def _retry_archives(self, db: Session, account_id: int) -> int:
        ids = db.scalars(select(EmailCleaningResult.id).join(EmailMessage)
            .outerjoin(EmailArchive, EmailArchive.cleaning_result_id == EmailCleaningResult.id)
            .where(EmailMessage.email_account_id == account_id,
                   EmailCleaningResult.status.in_(["completed", "submitted"]),
                   or_(EmailArchive.id.is_(None), EmailArchive.status != "archived"))).all()
        errors = 0
        for cleaning_id in ids:
            try:
                self.archive_cleaning(db, cleaning_id)
                cleaning = db.get(EmailCleaningResult, cleaning_id)
                db.get(EmailMessage, cleaning.email_message_id).processing_error = None
                db.commit()
            except ArchiveBusy:
                continue
            except Exception:
                errors += 1
        return errors
    def _classify_or_error(
        self,
        db: Session,
        prompt: str,
    ) -> EmailClassification | Exception:
        try:
            return self._classify_message(db, prompt)
        except Exception as exc:
            return exc

    def _classify_pending(self, db: Session, prompts: dict[int, str]) -> dict[int, Any]:
        """Classify prompts, in parallel when routing allows it.

        Values are classifications or the caught exception, so one failing
        message never aborts the batch. Parallel workers run the provider call
        on their own short-lived session; every database write still happens
        serially afterwards in _process_pending.
        """
        routing = db.get(LLMRoutingSettings, 1)
        max_parallel = routing.max_parallel_calls if routing is not None else 1
        max_parallel = max(1, min(8, max_parallel))
        if max_parallel == 1 or len(prompts) <= 1:
            return {
                message_id: self._classify_or_error(db, prompt)
                for message_id, prompt in prompts.items()
            }

        bind = db.get_bind()

        def worker(prompt: str):
            with OrmSession(bind=bind) as session:
                return self._classify_or_error(session, prompt)

        results: dict[int, Any] = {}
        with ThreadPoolExecutor(max_workers=max_parallel) as pool:
            futures = {
                message_id: pool.submit(worker, prompt)
                for message_id, prompt in prompts.items()
            }
            for message_id, future in futures.items():
                results[message_id] = future.result()
        return results

    def _process_pending(self, db: Session, account: EmailAccount) -> tuple[int, int, int]:
        """Classify and apply every pending pre-filtered message of one account.

        Covers both previously failed messages and candidates stored by the
        current sync. Messages whose consecutive LLM failures reached
        MAX_LLM_ATTEMPTS are left alone until a human retries or deletes them.
        Returns (events_created, processing_errors, retry_paused).
        """
        pending = db.scalars(
            select(EmailMessage)
            .where(
                EmailMessage.email_account_id == account.id,
                EmailMessage.pre_filtered.is_(True),
                EmailMessage.llm_processed.is_(False),
                EmailMessage.llm_attempt_count < MAX_LLM_ATTEMPTS,
            )
            .order_by(EmailMessage.uid, EmailMessage.id)
        ).all()
        if not pending:
            return 0, 0, self._retry_paused(db, account)

        existing_events = {
            event.source_email_id: event
            for event in db.scalars(
                select(RecruitmentEvent).where(
                    RecruitmentEvent.source_email_id.in_(
                        [message.id for message in pending]
                    )
                )
            ).all()
        }
        prompts = {
            message.id: self._email_prompt(message)
            for message in pending
            if message.id not in existing_events
        }
        classifications = self._classify_pending(db, prompts)

        events_created = processing_errors = 0
        for message in pending:
            event = None
            if message.id in existing_events:
                event = existing_events[message.id]
                events_created += 1
            else:
                outcome = classifications.get(message.id)
                if isinstance(outcome, Exception):
                    message.llm_attempt_count += 1
                    self._set_cleaning_failure(db, message, outcome)
                    if message.llm_attempt_count >= MAX_LLM_ATTEMPTS:
                        message.processing_error = (
                            f"LLM 识别连续失败 {MAX_LLM_ATTEMPTS} 次，已暂停自动重试；"
                            "请人工处理或删除该邮件"
                        )[:1000]
                    else:
                        message.processing_error = safe_processing_error(outcome)
                    processing_errors += 1
                    db.commit()
                elif outcome is not None:
                    events_before = int(
                        db.scalar(
                            select(func.count()).select_from(RecruitmentEvent).where(
                                RecruitmentEvent.source_email_id == message.id
                            )
                        )
                        or 0
                    )
                    try:
                        _, event = self._apply_classification(db, message, outcome)
                    except Exception as exc:
                        db.rollback()
                        message = db.get(EmailMessage, message.id)
                        message.llm_attempt_count += 1
                        self._set_cleaning_failure(db, message, exc)
                        message.processing_error = safe_processing_error(exc)
                        processing_errors += 1
                        db.commit()
                    else:
                        try:
                            db.commit()
                        except SQLAlchemyError:
                            db.rollback()
                            processing_errors += 1
                        else:
                            if event is not None:
                                events_after = int(
                                    db.scalar(
                                        select(func.count()).select_from(RecruitmentEvent).where(
                                            RecruitmentEvent.source_email_id == message.id
                                        )
                                    )
                                    or 0
                                )
                                # Count only rows created by this cleaning
                                # operation. Reusing a legacy event or
                                # replaying an identical result is idempotent.
                                events_created += max(0, events_after - events_before)
                            cleaning = self._cleaning_for_message(db, message.id)
                            if cleaning is not None and cleaning.status == "completed":
                                # Cleaning is committed first; the archive
                                # stage then performs direct Job matching and
                                # creation in short, independently retryable
                                # transactions.
                                try:
                                    self.archive_cleaning(db, cleaning.id)
                                except Exception as exc:
                                    processing_errors += 1
                                    message = db.get(EmailMessage, message.id)
                                    message.processing_error = safe_processing_error(exc)
                                    db.commit()
            if event is not None and self.notification_service is not None:
                events = db.scalars(
                    select(RecruitmentEvent)
                    .where(
                        RecruitmentEvent.source_email_id == message.id,
                        RecruitmentEvent.active.is_(True),
                    )
                    .order_by(RecruitmentEvent.id)
                ).all()
                for notification_event in events:
                    try:
                        self.notification_service.notify_event(db, notification_event)
                    except Exception:
                        processing_errors += 1
        return events_created, processing_errors, self._retry_paused(db, account)

    @staticmethod
    def _retry_paused(db: Session, account: EmailAccount) -> int:
        return int(
            db.scalar(
                select(func.count()).select_from(EmailMessage).where(
                    EmailMessage.email_account_id == account.id,
                    EmailMessage.pre_filtered.is_(True),
                    EmailMessage.llm_processed.is_(False),
                    EmailMessage.llm_attempt_count >= MAX_LLM_ATTEMPTS,
                )
            )
            or 0
        )

    def sync_account(self, db: Session, account: EmailAccount) -> SyncResult:
        if not account.enabled:
            raise EmailSyncConfigurationError("email account is disabled")
        account.last_sync_status = "running"
        account.last_sync_error = None
        db.commit()

        # Fetch and store every new message first; LLM work happens afterwards
        # so a slow model never blocks IMAP progress or last_uid advancement.
        # An optional per-account lookback window bounds first-sync / cursor
        # resets to the last N days instead of the whole mailbox.
        lookback_days = getattr(account, "sync_lookback_days", None)
        since_date = (
            (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date()
            if lookback_days
            else None
        )
        try:
            if since_date is not None:
                fetch_result = self.provider(account).fetch_since(
                    account.last_uid, since_date=since_date
                )
            else:
                fetch_result = self.provider(account).fetch_since(account.last_uid)
        except Exception as exc:
            db.rollback()
            account = db.get(EmailAccount, account.id)
            account.last_sync_at = datetime.now(timezone.utc)
            account.last_sync_status = "error"
            account.last_sync_error = safe_processing_error(exc)
            db.commit()
            raise

        if isinstance(fetch_result, EmailFetchBatch):
            envelopes = fetch_result.envelopes
            account = db.get(EmailAccount, account.id)
            if (
                fetch_result.uid_validity is not None
                and account.uid_validity != fetch_result.uid_validity
            ):
                account.uid_validity = fetch_result.uid_validity
                account.last_uid = 0
                db.commit()
        else:
            # Test doubles and third-party adapters written for the initial contract remain usable.
            envelopes = fetch_result

        created = duplicates = candidates_count = processing_errors = skipped_by_regex = 0
        regex_pattern = load_regex_filter(db)
        for envelope in sorted(envelopes, key=lambda item: item.uid):
            account = db.get(EmailAccount, account.id)
            account.last_uid = max(account.last_uid, envelope.uid)
            current_uid_validity = account.uid_validity or 0
            identity_conditions = [
                and_(
                    EmailMessage.uid_validity == current_uid_validity,
                    EmailMessage.uid == envelope.uid,
                )
            ]
            if envelope.message_id:
                identity_conditions.append(EmailMessage.message_id == envelope.message_id)
            duplicate = db.scalar(
                select(EmailMessage).where(
                    and_(
                        EmailMessage.email_account_id == account.id,
                        or_(*identity_conditions),
                    )
                )
            )
            if duplicate is not None:
                duplicates += 1
                db.commit()
                continue

            # 已配置正则时它同时是入库闸门：未命中的邮件只保留发件人/主题
            # 等摘要用于去重，正文不落库，也永远不会进入识别队列。
            regex_passed = regex_matches(
                envelope.subject,
                envelope.sender,
                envelope.body_text,
                regex_pattern,
            )
            regex_gate_active = bool(regex_pattern)
            keyword_passed = keyword_matches(
                envelope.subject,
                envelope.sender,
                envelope.body_text,
                load_keywords(db),
            )
            # A configured regex is the primary gate.  This lets users define
            # a precise rule without also having to keep the broad keyword
            # list in sync.  With no regex, retain the historical keyword
            # behaviour for compatibility.
            candidate = regex_passed if regex_gate_active else keyword_passed
            keep_body = regex_passed or not regex_gate_active
            message = EmailMessage(
                email_account_id=account.id,
                message_id=envelope.message_id,
                uid_validity=current_uid_validity,
                uid=envelope.uid,
                sender=envelope.sender[:500],
                subject=envelope.subject[:1000],
                received_at=envelope.received_at,
                body_text=(
                    ((envelope.body_text or "")[:100_000] or None) if keep_body else None
                ),
                pre_filtered=candidate,
                llm_processed=False,
            )
            db.add(message)
            try:
                db.flush()
                created += 1
                if candidate:
                    candidates_count += 1
                if regex_gate_active and not regex_passed:
                    skipped_by_regex += 1
                db.commit()
            except IntegrityError:
                db.rollback()
                duplicates += 1
            except SQLAlchemyError:
                db.rollback()
                processing_errors += 1

        # Classification, event creation and notifications now run against the
        # local queue, covering both fresh candidates and previously failed ones.
        # Recover previous crashes/failures before handling new messages, so a
        # freshly failed archive stays visible until the next sync or retry.
        processing_errors += self._retry_archives(db, account.id)
        events_created, pending_errors, retry_paused = self._process_pending(db, account)
        processing_errors += pending_errors

        account = db.get(EmailAccount, account.id)
        account.last_sync_at = datetime.now(timezone.utc)
        account.last_sync_status = "partial" if (processing_errors or retry_paused) else "success"
        account.last_sync_error = (
            f"{processing_errors} operation(s) require attention"
            if processing_errors
            else (f"{retry_paused} message(s) paused after repeated LLM failures" if retry_paused else None)
        )
        db.commit()
        return SyncResult(
            account_id=account.id,
            fetched=len(envelopes),
            created=created,
            duplicates=duplicates,
            filtered_candidates=candidates_count,
            events_created=events_created,
            processing_errors=processing_errors,
            skipped_by_regex=skipped_by_regex,
            retry_paused=retry_paused,
        )


class EmailSyncCoordinator:
    def __init__(self, service: EmailSyncService) -> None:
        self.service = service
        self._guard = threading.Lock()
        self._locks: dict[int, threading.Lock] = {}

    def _account_lock(self, account_id: int) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(account_id, threading.Lock())

    def sync(self, db: Session, account_id: int) -> SyncResult:
        lock = self._account_lock(account_id)
        if not lock.acquire(blocking=False):
            raise AccountSyncBusy("email account sync is already running")
        lease_acquired = False
        try:
            account = db.get(EmailAccount, account_id)
            if account is None:
                raise EmailSyncConfigurationError("email account not found")
            current = datetime.now(timezone.utc)
            lease = current + timedelta(hours=1)
            acquired = db.execute(
                update(EmailAccount)
                .where(
                    EmailAccount.id == account_id,
                    or_(
                        EmailAccount.sync_lease_until.is_(None),
                        EmailAccount.sync_lease_until <= current,
                    ),
                )
                .values(sync_lease_until=lease)
                .execution_options(synchronize_session=False)
            )
            db.commit()
            if acquired.rowcount != 1:
                raise AccountSyncBusy("email account sync is already running")
            lease_acquired = True
            account = db.get(EmailAccount, account_id)
            return self.service.sync_account(db, account)
        finally:
            try:
                db.rollback()
                if lease_acquired:
                    account = db.get(EmailAccount, account_id)
                    cleanup_values: dict[str, Any] = {"sync_lease_until": None}
                    if account is not None and account.last_sync_status == "running":
                        cleanup_values.update(
                            last_sync_status="error",
                            last_sync_at=datetime.now(timezone.utc),
                            last_sync_error="email sync stopped unexpectedly",
                        )
                    db.execute(
                        update(EmailAccount)
                        .where(EmailAccount.id == account_id)
                        .values(**cleanup_values)
                        .execution_options(synchronize_session=False)
                    )
                    db.commit()
            finally:
                lock.release()
