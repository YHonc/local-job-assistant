"""Manual email facts and explicitly confirmed application progress."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.exc import StaleDataError

from app.database.models import (
    Application,
    Company,
    EmailCleaningResult,
    EmailJobEntry,
    EmailMessage,
    Job,
)
from app.database.session import get_db
from app.imports.duplicate import normalize_text

from .matching import find_job_candidates
from .progress_models import (
    ApplicationStatusHistory,
    EmailManualFactRequest,
    EmailProgressSuggestion,
)
from .progress_schemas import (
    ManualEmailFactsIn,
    ManualEmailFactsUpdateIn,
    ProgressSuggestionAcceptIn,
    ProgressSuggestionCreateIn,
    ProgressSuggestionOut,
    ProgressSuggestionRejectIn,
)
from .schemas import EmailJobEntryOut


router = APIRouter(tags=["email-progress"])

_INTERVIEW_ROUNDS = {
    "一面": "一面",
    "1": "一面",
    "1面": "一面",
    "first": "一面",
    "first round": "一面",
    "二面": "二面",
    "2": "二面",
    "2面": "二面",
    "second": "二面",
    "second round": "二面",
    "hr": "HR面",
    "hr面": "HR面",
    "人力资源面": "HR面",
    "人事面": "HR面",
}
_STATUS_ORDER = {
    "收藏": 0,
    "待投递": 1,
    "已投递": 2,
    "测评": 3,
    "笔试": 4,
    "一面": 5,
    "二面": 6,
    "待面试": 4.5,
    "1面": 5,
    "2面": 6,
    "3面": 6.5,
    "HR面": 7,
    "Offer": 8,
}
_TERMINAL_STATUSES = {"Offer", "拒绝", "放弃"}
_CANCELLED_OR_WITHDRAWN = re.compile(
    r"取消|撤销|不再安排|无需参加|不需要参加|不参加|取消安排|"
    r"cancel(?:led|ed)?|withdrawn|reschedul(?:ed|e)",
    re.IGNORECASE,
)
_NEGATED_REJECTION = re.compile(
    r"尚未拒绝|并非拒信|不是拒信|没有拒绝|未收到拒绝|并未拒绝|"
    r"not\s+rejected|no\s+rejection|not\s+a\s+rejection",
    re.IGNORECASE,
)
_REJECTION_FACT = re.compile(
    r"不合适|不予录用|未予录用|未被录用|未被录取|未被选中|"
    r"拒绝.{0,12}(?:申请|录用|您)|拒信|招聘流程.{0,12}(?:结束|到此为止)|"
    r"(?:未通过|未能通过|未能通过).{0,10}(?:面试|笔试|测评|简历筛选|初筛|考核|甄选)|"
    r"(?:面试|笔试|测评|筛选|申请).{0,10}(?:未通过|不通过|失败)|"
    r"rejected|not\s+selected|regret\s+to\s+inform|unsuccessful|"
    r"did\s+not\s+pass\s+(?:the\s+)?(?:interview|assessment|screening)",
    re.IGNORECASE,
)
_EVENT_EVIDENCE = {
    "APPLICATION_RECEIVED": re.compile(
        r"(?:已|已为您|成功)?收到.{0,12}(?:申请|报名)|感谢.{0,12}申请|"
        r"application\s+(?:received|submitted)|thank\s+you\s+for\s+applying",
        re.IGNORECASE,
    ),
    # A bare topic mention is not a progress event. Require an invitation,
    # instruction to complete/attend, a scheduled time, or a result notice.
    "ASSESSMENT": re.compile(
        r"(?:请|请您|请你|需要|须|诚邀|邀请|安排|要求|进入).{0,18}"
        r"(?:测评|评测|assessment)|"
        r"(?:请|请您|请你|需要|须|诚邀|邀请|安排|要求|进入).{0,18}"
        r"(?:完成|参加|进行).{0,8}(?:测评|评测|assessment)|"
        r"(?:测评|评测|assessment).{0,18}(?:邀请|邀约|安排|时间|请完成|需完成|需要完成|须完成|请参加|已完成|已提交)|"
        r"(?:please\s+(?:complete|take|attend).{0,10}assessment|"
        r"invited\s+to\s+(?:complete|take|attend).{0,10}assessment|"
        r"assessment\s+(?:invitation|notice|scheduled))",
        re.IGNORECASE,
    ),
    "WRITTEN_TEST": re.compile(
        r"(?:请|请您|请你|需要|须|诚邀|邀请|安排|要求|进入).{0,18}"
        r"(?:笔试|书面测试|written\s+test|coding\s+test)|"
        r"(?:请|请您|请你|需要|须|诚邀|邀请|安排|要求|进入).{0,18}"
        r"(?:完成|参加|进行).{0,8}(?:笔试|书面测试|written\s+test|coding\s+test)|"
        r"(?:笔试|书面测试|written\s+test|coding\s+test).{0,18}"
        r"(?:邀请|邀约|通知|安排|时间|请完成|需完成|需要完成|须完成|请参加|已完成|已通过)|"
        r"(?:please\s+(?:complete|attend).{0,10}(?:written\s+test|coding\s+test)|"
        r"invited\s+to\s+(?:complete|attend).{0,10}(?:written\s+test|coding\s+test)|"
        r"(?:written\s+test|coding\s+test)\s+(?:invitation|notice|scheduled))",
        re.IGNORECASE,
    ),
    "INTERVIEW": re.compile(
        r"(?:请|请您|请你|诚邀|邀请|安排|通知|约请|进入).{0,24}"
        r"(?:面试|面谈|interview)|"
        r"(?:请|请您|请你|诚邀|邀请|安排|通知|约请|进入).{0,18}"
        r"(?:参加|进行).{0,8}(?:面试|面谈|interview)|"
        r"(?:面试|面谈|interview).{0,24}(?:邀约|邀请|通知|安排|时间|请参加|将于|定于|scheduled)|"
        r"(?:please\s+attend\s+(?:an?\s+)?interview|"
        r"invited\s+to\s+(?:an?\s+)?interview|interview\s+(?:invitation|notice|scheduled))",
        re.IGNORECASE,
    ),
    "OFFER": re.compile(
        r"(?:恭喜|正式|拟向|现向|已向|很高兴|很荣幸|决定).{0,18}"
        r"(?:录用|聘用|offer)|(?:录用|聘用|offer).{0,18}"
        r"(?:通知|结果|函|正式|发给|给予|入职通知)|"
        r"(?:pleased|delighted|glad)\s+to\s+offer|offer\s+(?:letter|of\s+employment)",
        re.IGNORECASE,
    ),
    "REJECTED": _REJECTION_FACT,
}
_ROUND_EVIDENCE = {
    "一面": re.compile(r"一面|1\s*面|第一轮(?:面试)?|first\s+(?:round|interview)", re.IGNORECASE),
    "二面": re.compile(r"二面|2\s*面|第二轮(?:面试)?|second\s+(?:round|interview)", re.IGNORECASE),
    "HR面": re.compile(r"HR\s*面|人力资源面|人事面|HR\s+interview|human\s+resources\s+interview", re.IGNORECASE),
}
_ROUND_ACTION = re.compile(
    r"邀请|邀约|诚邀|安排|通知|参加|请您|请你|请参加|已完成|已通过|进入下一轮|"
    r"invited|scheduled|attend|completed|passed",
    re.IGNORECASE,
)
_ROUND_BOUNDARY = re.compile(r"[，。；;\n.!?]+")


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _begin_write(db: Session) -> None:
    """Take a cross-process write reservation before validation on SQLite."""

    if db.get_bind().dialect.name == "sqlite":
        # Session autobegin is logical until the first driver statement. This
        # statement therefore starts the database transaction as IMMEDIATE.
        db.execute(text("BEGIN IMMEDIATE"))


def _lock_entry(db: Session, entry_id: int) -> EmailJobEntry | None:
    entry = db.scalar(
        select(EmailJobEntry)
        .where(EmailJobEntry.id == entry_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if entry is not None and entry.cleaning_result_id is not None:
        # Lock the cleaning row before the entry row on databases with row
        # locks. Reprocessing uses the same cleaning-then-entry write order.
        db.scalar(
            select(EmailCleaningResult)
            .where(EmailCleaningResult.id == entry.cleaning_result_id)
            .with_for_update()
        )
        entry = db.scalar(
            select(EmailJobEntry)
            .where(EmailJobEntry.id == entry_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    return entry


def _entry_out(db: Session, entry: EmailJobEntry) -> EmailJobEntryOut:
    values = EmailJobEntryOut.model_validate(entry)
    values.candidates = find_job_candidates(
        db, entry.company_name, entry.job_title, entry.work_location, entry.links
    )
    return values


def _manual_payload_hash(payload: ManualEmailFactsIn) -> str:
    encoded = json.dumps(
        payload.model_dump(mode="json", exclude={"idempotency_key"}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manual_identity(payload: ManualEmailFactsIn) -> str:
    identity = "|".join(
        (
            normalize_text(payload.company_name),
            normalize_text(payload.job_title),
            payload.event_type.value,
            normalize_text(payload.event_round),
            payload.event_time.isoformat() if payload.event_time else "",
        )
    )
    return "manual:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _apply_facts(entry: EmailJobEntry, values) -> None:
    entry.facts_source = "manual"
    entry.company_name = values.company_name
    entry.job_title = values.job_title
    entry.work_location = values.work_location
    entry.links = list(values.links or [])
    entry.field_evidence = values.field_evidence
    entry.event_type = values.event_type.value
    entry.event_round = values.event_round
    entry.event_time = values.event_time
    entry.deadline = values.deadline
    entry.action_required = values.action_required
    entry.actions = list(values.actions or [])
    entry.summary = values.summary


@router.post(
    "/email-messages/{email_id}/manual-entries",
    response_model=EmailJobEntryOut,
    status_code=201,
)
def create_manual_email_entry(
    email_id: int,
    payload: ManualEmailFactsIn,
    db: Session = Depends(get_db),
) -> EmailJobEntryOut:
    """Create a user-checked entry directly from a locally stored email.

    This path neither constructs an EmailCleaningResult nor calls the model.
    """

    _begin_write(db)
    try:
        message = db.get(EmailMessage, email_id)
        if message is None:
            raise HTTPException(status_code=404, detail="email message not found")
        payload_hash = _manual_payload_hash(payload)
        prior = db.scalar(
            select(EmailManualFactRequest).where(
                EmailManualFactRequest.source_email_id == email_id,
                EmailManualFactRequest.idempotency_key == payload.idempotency_key,
            )
        )
        if prior is not None:
            if prior.payload_hash != payload_hash:
                raise _conflict("idempotency key was already used for different manual facts")
            entry = db.get(EmailJobEntry, prior.email_job_entry_id)
            if entry is None:
                raise _conflict("the original manual entry no longer exists")
            db.commit()
            return _entry_out(db, entry)

        identity_key = _manual_identity(payload)
        existing = db.scalar(
            select(EmailJobEntry).where(
                EmailJobEntry.source_email_id == email_id,
                EmailJobEntry.identity_key == identity_key,
            )
        )
        if existing is not None:
            raise _conflict("this event already has an entry; edit its facts instead")

        entry = EmailJobEntry(
            source_email_id=email_id,
            cleaning_result_id=None,
            identity_key=identity_key,
            facts_source="manual",
            company_name=payload.company_name,
            job_title=payload.job_title,
            work_location=payload.work_location,
            links=list(payload.links or []),
            field_evidence=payload.field_evidence,
            event_type=payload.event_type.value,
            event_round=payload.event_round,
            event_time=payload.event_time,
            deadline=payload.deadline,
            action_required=payload.action_required,
            actions=list(payload.actions or []),
            summary=payload.summary,
            match_status="unmatched",
        )
        db.add(entry)
        db.flush()
        db.add(
            EmailManualFactRequest(
                source_email_id=email_id,
                idempotency_key=payload.idempotency_key,
                payload_hash=payload_hash,
                email_job_entry_id=entry.id,
            )
        )
        db.commit()
        db.refresh(entry)
        return _entry_out(db, entry)
    except HTTPException:
        db.rollback()
        raise
    except (IntegrityError, StaleDataError) as exc:
        db.rollback()
        raise _conflict("manual facts were changed concurrently") from exc


@router.post(
    "/email-job-entries/{entry_id}/manual-facts",
    response_model=EmailJobEntryOut,
)
def update_manual_email_facts(
    entry_id: int,
    payload: ManualEmailFactsUpdateIn,
    db: Session = Depends(get_db),
) -> EmailJobEntryOut:
    _begin_write(db)
    entry = _lock_entry(db, entry_id)
    if entry is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="email job entry not found")
    if not entry.active:
        db.rollback()
        raise _conflict("inactive entry cannot receive new facts")
    if entry.version != payload.expected_entry_version:
        db.rollback()
        raise _conflict("email job entry version changed")
    _apply_facts(entry, payload)
    entry.match_reason = "facts entered or confirmed by user; association unchanged"
    try:
        db.commit()
        db.refresh(entry)
    except StaleDataError as exc:
        db.rollback()
        raise _conflict("email job entry version changed") from exc
    return _entry_out(db, entry)


def _cleaning_snapshot(db: Session, entry: EmailJobEntry):
    cleaning = None
    if entry.cleaning_result_id is not None:
        cleaning = db.get(EmailCleaningResult, entry.cleaning_result_id)
    email_id = entry.source_email_id
    if cleaning is not None:
        email_id = cleaning.email_message_id
    if email_id is not None and cleaning is None:
        cleaning = db.scalar(
            select(EmailCleaningResult).where(
                EmailCleaningResult.email_message_id == email_id
            )
        )
    return email_id, cleaning


def _snapshot_key(entry: EmailJobEntry, email_id: int | None, cleaning, application):
    return ":".join(
        (
            str(email_id or 0),
            str(cleaning.id if cleaning else 0),
            str(cleaning.version if cleaning else 0),
            str(entry.version),
            str(application.id if application else 0),
            str(application.version if application else 0),
            application.status if application else "",
            str(entry.company_id or 0),
            str(entry.job_id or 0),
            str(entry.application_id or 0),
        )
    )


def _flatten_evidence(value) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        result = []
        for item in value.values():
            result.extend(_flatten_evidence(item))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            result.extend(_flatten_evidence(item))
        return result
    return []


def _round_evidence_matches(target: str, evidence_text: str) -> bool:
    """Require the recorded interview round to be stated in relevant evidence."""

    target_pattern = _ROUND_EVIDENCE.get(target)
    if target_pattern is None:
        return True
    rounds_present = {
        name for name, pattern in _ROUND_EVIDENCE.items() if pattern.search(evidence_text)
    }
    if target not in rounds_present:
        return False
    if rounds_present == {target}:
        return True
    # When an email mentions multiple rounds, the target round must be tied to
    # its own invitation, schedule, attendance, or result wording. A token for
    # "一面" elsewhere cannot validate a row recorded as "二面".
    for sentence in _ROUND_BOUNDARY.split(evidence_text):
        if target_pattern.search(sentence) and _ROUND_ACTION.search(sentence):
            return True
    return False


def _target_status(entry: EmailJobEntry) -> tuple[str | None, str | None]:
    event_type = (entry.event_type or "OTHER").upper()
    if event_type == "APPLICATION_RECEIVED":
        return "已投递", None
    if event_type == "ASSESSMENT":
        return "测评", None
    if event_type == "WRITTEN_TEST":
        return "笔试", None
    if event_type == "OFFER":
        return "Offer", None
    if event_type == "REJECTED":
        return "拒绝", None
    if event_type == "INTERVIEW":
        round_key = normalize_text(entry.event_round)
        target = _INTERVIEW_ROUNDS.get(round_key)
        if target is None:
            return None, "面试轮次不明确，需人工核对后再修改状态"
        return target, None
    if event_type == "INTERVIEW_CHANGED":
        return None, "取消或变更通知不推进投递阶段"
    return None, "该事件类型不对应可确认的投递阶段"


def _exact_job_candidates(db: Session, entry: EmailJobEntry) -> list[Job]:
    company_name = normalize_text(entry.company_name)
    title = normalize_text(entry.job_title)
    if not company_name or not title:
        return []
    jobs = db.scalars(
        select(Job)
        .options(joinedload(Job.company), joinedload(Job.application))
        .order_by(Job.id)
    ).unique().all()
    matches = []
    for job in jobs:
        names = {normalize_text(job.company.name)}
        names.update(normalize_text(alias) for alias in (job.company.aliases or []))
        if company_name not in names or normalize_text(job.title) != title:
            continue
        entry_location = normalize_text(entry.work_location)
        job_location = normalize_text(job.location)
        if entry_location and job_location and entry_location != job_location:
            continue
        matches.append(job)
    return matches


def _resolve_target_application(db: Session, entry: EmailJobEntry):
    if entry.manually_associated:
        if entry.match_status == "dismissed" or not entry.application_id:
            return None, None, "人工关联尚未选择目标申请记录"
        application = db.get(Application, entry.application_id)
        if application is None:
            return None, None, "人工关联的申请记录已不存在"
        job = db.scalar(
            select(Job).options(joinedload(Job.company)).where(Job.id == application.job_id)
        )
        if job is None or entry.job_id != job.id or entry.company_id != job.company_id:
            return None, None, "人工关联与申请记录不一致"
        known_names = {normalize_text(job.company.name)}
        known_names.update(normalize_text(alias) for alias in (job.company.aliases or []))
        if normalize_text(entry.company_name) not in known_names or normalize_text(job.title) != normalize_text(entry.job_title):
            return None, None, "人工关联岗位与邮件事实不一致"
        if entry.work_location and job.location and normalize_text(entry.work_location) != normalize_text(job.location):
            return None, None, "人工关联岗位地点与邮件事实冲突"
        return application, job, None

    matches = _exact_job_candidates(db, entry)
    if len(matches) != 1:
        if not matches:
            return None, None, "没有唯一的公司和岗位精确匹配，请先人工关联"
        return None, None, "同公司同岗位存在多个候选，请先人工关联"
    job = matches[0]
    if job.application is None:
        return None, job, "岗位尚无申请记录，请先显式创建或人工选择申请"
    return job.application, job, None


def _suggestion_decision(db: Session, entry: EmailJobEntry):
    target, reason = _target_status(entry)
    email_id, cleaning = _cleaning_snapshot(db, entry)
    company_id = entry.company_id
    job_id = entry.job_id
    application = None
    if not entry.active:
        target, reason = None, "条目已停用，不能更新申请状态"
    elif reason is None and target is not None:
        evidence = _flatten_evidence(entry.field_evidence)
        evidence_text = " ".join(evidence)
        if not evidence:
            target, reason = None, "缺少可核对的邮件原文证据"
        elif _CANCELLED_OR_WITHDRAWN.search(evidence_text + " " + (entry.summary or "")):
            target, reason = None, "证据包含取消、撤回或改期内容，需人工判断"
        elif _NEGATED_REJECTION.search(evidence_text):
            target, reason = None, "邮件证据否定了拒绝结论，需人工判断"
        elif re.search(
            r"(?:尚未|暂未|还未|并未|未安排|时间待定|尚未安排|not\s+yet).{0,12}"
            r"(?:测评|评测|assessment|笔试|written\s+test|面试|interview|录用|offer)|"
            r"(?:测评|评测|assessment|笔试|written\s+test|面试|interview|录用|offer)"
            r".{0,12}(?:时间待定|尚未安排|未安排|not\s+yet)",
            evidence_text,
            re.IGNORECASE,
        ):
            target, reason = None, "邮件仅提及未安排或待定事项，需人工核对"
        elif entry.event_type == "REJECTED":
            if not _REJECTION_FACT.search(evidence_text):
                target, reason = None, "邮件证据未明确支持拒绝结果"
        elif _REJECTION_FACT.search(evidence_text):
            target, reason = None, "邮件证据包含拒绝结果，与提取事件类型冲突"
        elif not _EVENT_EVIDENCE.get(entry.event_type, re.compile(r"(?!)")).search(evidence_text):
            target, reason = None, "邮件原文证据未明确支持该招聘事件"
        elif target in _ROUND_EVIDENCE and not _ROUND_EVIDENCE[target].search(evidence_text):
            target, reason = None, "邮件证据未明确支持所记录的面试轮次"

    if reason is None and target is not None:
        application, job, reason = _resolve_target_application(db, entry)
        if job is not None:
            company_id, job_id = job.company_id, job.id
    if reason is None and application is not None and target is not None:
        current = application.status
        if current in _TERMINAL_STATUSES:
            target, reason = None, f"申请已处于终态 {current}，需人工处理矛盾邮件"
        elif target != "拒绝" and current not in _STATUS_ORDER:
            target, reason = None, "当前申请状态不在可自动比较的阶段范围"
        elif target == "拒绝" and current == "Offer":
            target, reason = None, "已录用与拒信冲突，需人工处理"
        elif target != "拒绝" and _STATUS_ORDER[target] <= _STATUS_ORDER[current]:
            target, reason = None, "邮件对应阶段不晚于当前申请状态，禁止倒退或重复推进"

    return {
        "target_status": target,
        "reason": reason or f"证据支持从 {application.status} 推进到 {target}",
        "email_id": email_id,
        "cleaning": cleaning,
        "application": application,
        "company_id": company_id,
        "job_id": job_id,
    }


def _get_or_create_suggestion(db: Session, entry: EmailJobEntry) -> EmailProgressSuggestion:
    result = _suggestion_decision(db, entry)
    application = result["application"]
    snapshot_key = _snapshot_key(
        entry, result["email_id"], result["cleaning"], application
    )
    existing = db.scalar(
        select(EmailProgressSuggestion).where(
            EmailProgressSuggestion.email_job_entry_id == entry.id,
            EmailProgressSuggestion.entry_version == entry.version,
            EmailProgressSuggestion.snapshot_key == snapshot_key,
        )
    )
    if existing is not None:
        return existing
    row = EmailProgressSuggestion(
        email_job_entry_id=entry.id,
        source_email_id=result["email_id"] or entry.source_email_id,
        cleaning_result_id=result["cleaning"].id if result["cleaning"] else None,
        cleaning_version=result["cleaning"].version if result["cleaning"] else None,
        entry_version=entry.version,
        snapshot_key=snapshot_key,
        company_id=result["company_id"],
        job_id=result["job_id"],
        application_id=application.id if application else None,
        application_version=application.version if application else None,
        expected_application_status=application.status if application else None,
        target_status=result["target_status"],
        event_type=entry.event_type,
        event_round=entry.event_round,
        evidence=entry.field_evidence,
        reason=result["reason"],
        status="pending" if result["target_status"] and application else "needs_review",
    )
    db.add(row)
    db.flush()
    return row


@router.post(
    "/email-job-entries/{entry_id}/progress-suggestions",
    response_model=ProgressSuggestionOut,
)
def create_progress_suggestion(
    entry_id: int,
    payload: ProgressSuggestionCreateIn,
    db: Session = Depends(get_db),
) -> ProgressSuggestionOut:
    _begin_write(db)
    entry = _lock_entry(db, entry_id)
    if entry is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="email job entry not found")
    if entry.version != payload.expected_entry_version:
        db.rollback()
        raise _conflict("email job entry version changed")
    try:
        suggestion = _get_or_create_suggestion(db, entry)
        db.commit()
        db.refresh(suggestion)
        return ProgressSuggestionOut.model_validate(suggestion)
    except (IntegrityError, StaleDataError) as exc:
        db.rollback()
        raise _conflict("progress suggestion snapshot changed concurrently") from exc


def _lock_suggestion_context(db: Session, suggestion_id: int):
    suggestion = db.get(EmailProgressSuggestion, suggestion_id)
    if suggestion is None:
        return None, None, None
    entry = _lock_entry(db, suggestion.email_job_entry_id)
    if entry is None:
        return suggestion, None, None
    application = None
    if suggestion.application_id is not None:
        application = db.scalar(
            select(Application)
            .where(Application.id == suggestion.application_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    suggestion = db.scalar(
        select(EmailProgressSuggestion)
        .where(EmailProgressSuggestion.id == suggestion_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return suggestion, entry, application


@router.get(
    "/email-job-entries/{entry_id}/progress-suggestions",
    response_model=list[ProgressSuggestionOut],
)
def list_progress_suggestions(
    entry_id: int,
    db: Session = Depends(get_db),
) -> list[ProgressSuggestionOut]:
    entry = db.get(EmailJobEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="email job entry not found")
    rows = db.scalars(
        select(EmailProgressSuggestion)
        .where(EmailProgressSuggestion.email_job_entry_id == entry_id)
        .order_by(EmailProgressSuggestion.id.desc())
    ).all()
    output = []
    for row in rows:
        values = ProgressSuggestionOut.model_validate(row).model_dump()
        if row.status == "pending":
            app = db.get(Application, row.application_id) if row.application_id else None
            email_id, cleaning = _cleaning_snapshot(db, entry)
            current_key = _snapshot_key(entry, email_id, cleaning, app)
            if current_key != row.snapshot_key:
                values["status"] = "stale"
                values["reason"] = "条目、清洗版本、关联或申请状态已变化，请重新生成建议"
        output.append(ProgressSuggestionOut.model_validate(values))
    return output


@router.post(
    "/progress-suggestions/{suggestion_id}/accept",
    response_model=ProgressSuggestionOut,
)
def accept_progress_suggestion(
    suggestion_id: int,
    payload: ProgressSuggestionAcceptIn,
    db: Session = Depends(get_db),
) -> ProgressSuggestionOut:
    _begin_write(db)
    suggestion, entry, application = _lock_suggestion_context(db, suggestion_id)
    if suggestion is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="progress suggestion not found")
    if entry is None:
        db.rollback()
        raise _conflict("email job entry no longer exists")
    if suggestion.status == "accepted":
        if (
            suggestion.idempotency_key == payload.idempotency_key
            and suggestion.accept_expected_version == payload.expected_suggestion_version
        ):
            db.commit()
            return ProgressSuggestionOut.model_validate(suggestion)
        db.rollback()
        raise _conflict("progress suggestion was already accepted by another request")
    if suggestion.status != "pending":
        db.rollback()
        raise _conflict(f"progress suggestion is {suggestion.status} and cannot be accepted")
    if suggestion.version != payload.expected_suggestion_version:
        db.rollback()
        raise _conflict("progress suggestion version changed")
    if entry.version != suggestion.entry_version or not entry.active:
        db.rollback()
        raise _conflict("email job entry changed or is inactive")
    if application is None or application.id != suggestion.application_id:
        db.rollback()
        raise _conflict("target application no longer exists")
    if application.version != payload.expected_application_version:
        db.rollback()
        raise _conflict("application version changed")
    if suggestion.application_version != application.version or (
        suggestion.expected_application_status != application.status
    ):
        db.rollback()
        raise _conflict("application state no longer matches this suggestion")
    email_id, cleaning = _cleaning_snapshot(db, entry)
    if (
        (cleaning.id if cleaning else None) != suggestion.cleaning_result_id
        or (cleaning.version if cleaning else None) != suggestion.cleaning_version
        or _snapshot_key(entry, email_id, cleaning, application) != suggestion.snapshot_key
    ):
        db.rollback()
        raise _conflict("email facts, cleaning version, or association changed")
    if not suggestion.target_status:
        db.rollback()
        raise _conflict("suggestion has no safe target status")

    # Repeat the conservative decision while holding the entry, cleaning and
    # application locks. The stored recommendation itself never changes state.
    decision = _suggestion_decision(db, entry)
    if (
        decision["application"] is None
        or decision["application"].id != application.id
        or decision["target_status"] != suggestion.target_status
    ):
        db.rollback()
        raise _conflict("email evidence no longer supports this application update")

    application.status = suggestion.target_status
    application.status_updated_at = datetime.now(timezone.utc)
    db.info["application_status_source"] = "email_progress_suggestion"
    db.info["application_status_suggestion_id"] = suggestion.id
    suggestion.status = "accepted"
    suggestion.idempotency_key = payload.idempotency_key
    suggestion.accept_expected_version = payload.expected_suggestion_version
    suggestion.result_application_status = suggestion.target_status
    suggestion.accepted_at = datetime.now(timezone.utc)
    try:
        db.flush()
        suggestion.result_application_version = application.version
        db.commit()
        db.refresh(suggestion)
        return ProgressSuggestionOut.model_validate(suggestion)
    except (IntegrityError, StaleDataError) as exc:
        db.rollback()
        raise _conflict("progress suggestion was accepted concurrently; reload current state") from exc
    finally:
        db.info.pop("application_status_source", None)
        db.info.pop("application_status_suggestion_id", None)


@router.post(
    "/progress-suggestions/{suggestion_id}/reject",
    response_model=ProgressSuggestionOut,
)
def reject_progress_suggestion(
    suggestion_id: int,
    payload: ProgressSuggestionRejectIn,
    db: Session = Depends(get_db),
) -> ProgressSuggestionOut:
    _begin_write(db)
    suggestion, entry, _application = _lock_suggestion_context(db, suggestion_id)
    if suggestion is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="progress suggestion not found")
    if entry is None:
        db.rollback()
        raise _conflict("email job entry no longer exists")
    if suggestion.status == "rejected" and suggestion.version == payload.expected_suggestion_version + 1:
        db.commit()
        return ProgressSuggestionOut.model_validate(suggestion)
    if suggestion.status != "pending" or suggestion.version != payload.expected_suggestion_version:
        db.rollback()
        raise _conflict("progress suggestion changed and cannot be rejected")
    suggestion.status = "rejected"
    try:
        db.commit()
        db.refresh(suggestion)
        return ProgressSuggestionOut.model_validate(suggestion)
    except StaleDataError as exc:
        db.rollback()
        raise _conflict("progress suggestion changed concurrently") from exc
