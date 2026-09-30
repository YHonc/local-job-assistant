from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.credentials import CredentialStore, CredentialStoreError
from app.database.models import Notification, NotificationPolicy, RecruitmentEvent

from .schemas import IMPORTANT_EVENT_TYPES


PUSH_CREDENTIAL_ID = "notification:push"
MAX_AUTOMATIC_ATTEMPTS = 3
DELIVERY_LEASE_MINUTES = 15


class NotificationConfigurationError(RuntimeError):
    pass


class NotificationAdapter(Protocol):
    def send(self, target_url: str, title: str, body: str) -> bool: ...


class AppriseAdapter:
    def send(self, target_url: str, title: str, body: str) -> bool:
        try:
            import apprise
        except Exception as exc:
            raise NotificationConfigurationError("Apprise backend unavailable") from exc
        app = apprise.Apprise()
        if not app.add(target_url):
            raise NotificationConfigurationError("notification target is invalid")
        return bool(
            app.notify(
                title=title,
                body=body,
                body_format=apprise.NotifyFormat.TEXT,
            )
        )


def desktop_apprise_url() -> str:
    if sys.platform.startswith("win"):
        return "windows://"
    if sys.platform == "darwin":
        return "macosx://"
    return "dbus://"


def ensure_policy(db: Session) -> NotificationPolicy:
    policy = db.get(NotificationPolicy, 1)
    if policy is None:
        policy = NotificationPolicy(
            id=1,
            name="默认策略",
            desktop_enabled=True,
            push_enabled=False,
            push_provider=None,
            push_credential_id=None,
            important_event_types=[item.value for item in IMPORTANT_EVENT_TYPES],
            notify_actionable_deadlines=True,
        )
        db.add(policy)
        db.flush()
    return policy


def enabled_policies(db: Session) -> list[NotificationPolicy]:
    return list(
        db.scalars(
            select(NotificationPolicy)
            .where(NotificationPolicy.enabled.is_(True))
            .order_by(NotificationPolicy.id)
        ).all()
    )


def should_notify(policy: NotificationPolicy, event: RecruitmentEvent) -> bool:
    return event.event_type in (policy.important_event_types or []) or (
        policy.notify_actionable_deadlines
        and event.action_required
        and event.deadline is not None
    )


class NotificationService:
    def __init__(
        self,
        credential_store: CredentialStore,
        adapter: NotificationAdapter | None = None,
    ) -> None:
        self.credential_store = credential_store
        self.adapter = adapter or AppriseAdapter()

    def _target(self, policy: NotificationPolicy, provider: str) -> str:
        if provider == "desktop":
            return desktop_apprise_url()
        if provider != "push" or not policy.push_credential_id:
            raise NotificationConfigurationError("push target is not configured")
        try:
            target = self.credential_store.get(policy.push_credential_id)
        except CredentialStoreError as exc:
            raise NotificationConfigurationError("credential store unavailable") from exc
        if not target:
            raise NotificationConfigurationError("push target is not configured")
        scheme = target.split("://", 1)[0].casefold() if "://" in target else ""
        if policy.push_provider == "ntfy" and scheme not in {"ntfy", "ntfys"}:
            raise NotificationConfigurationError("push target does not match configured provider")
        if policy.push_provider == "bark" and scheme != "bark":
            raise NotificationConfigurationError("push target does not match configured provider")
        return target

    @staticmethod
    def _event_content(event: RecruitmentEvent) -> tuple[str, str]:
        identity = " · ".join(
            value for value in (event.company_name, event.job_title) if value
        )
        title = f"招聘事件：{event.event_type}"
        if identity:
            title = f"{title} · {identity}"
        details = [event.ai_summary]
        if event.event_time:
            details.append(f"事件时间：{event.event_time.isoformat()}")
        if event.deadline:
            details.append(f"截止时间：{event.deadline.isoformat()}")
        if event.location:
            details.append(f"地点：{event.location}")
        return title[:300], "\n".join(details)

    def _new_notification(
        self,
        event: RecruitmentEvent | None,
        policy: NotificationPolicy,
        provider: str,
        title: str,
        content: str,
    ) -> Notification:
        return Notification(
            event_id=event.id if event else None,
            policy_id=policy.id,
            provider=provider,
            title=title[:300],
            content=content,
            status="pending",
            success=False,
            attempt_count=0,
            idempotency_key=(
                f"event:{event.id}:p{policy.id}:{provider}" if event else f"test:p{policy.id}:{uuid4()}"
            ),
        )

    def _policy_providers(self, policy: NotificationPolicy) -> list[str]:
        providers = []
        if policy.desktop_enabled:
            providers.append("desktop")
        if policy.push_enabled:
            providers.append("push")
        return providers

    def _notification_for_key(self, db: Session, key: str) -> Notification | None:
        return db.scalar(select(Notification).where(Notification.idempotency_key == key))

    def notify_event(self, db: Session, event: RecruitmentEvent) -> list[Notification]:
        policies = enabled_policies(db)
        if not policies and db.scalar(select(NotificationPolicy).limit(1)) is None:
            # First use on a fresh database still materializes the default policy;
            # a database where every policy is disabled stays intentionally quiet.
            policies = [ensure_policy(db)]
        if not any(should_notify(policy, event) for policy in policies):
            db.commit()
            return []
        title, content = self._event_content(event)

        records: list[Notification] = []
        for policy in policies:
            if not should_notify(policy, event):
                continue
            for provider in self._policy_providers(policy):
                key = f"event:{event.id}:p{policy.id}:{provider}"
                record = self._notification_for_key(db, key)
                if record is None:
                    record = self._new_notification(event, policy, provider, title, content)
                    db.add(record)
                    try:
                        db.commit()
                        db.refresh(record)
                    except IntegrityError:
                        db.rollback()
                        record = self._notification_for_key(db, key)
                        if record is None:
                            raise
                if record.status != "sent":
                    self.send_notification(db, record)
                records.append(record)
        return records

    def create_test_notification(
        self,
        db: Session,
        provider: str,
        policy: NotificationPolicy | None = None,
    ) -> Notification:
        policy = policy if policy is not None else ensure_policy(db)
        record = self._new_notification(
            None,
            policy,
            provider,
            "Local Job Assistant 测试通知",
            "这是一条由用户主动触发的本地通知测试。",
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return self.send_notification(db, record, manual=True)

    def _policy_for_notification(
        self,
        db: Session,
        notification: Notification,
    ) -> NotificationPolicy:
        if notification.policy_id is not None:
            policy = db.get(NotificationPolicy, notification.policy_id)
            if policy is not None:
                return policy
        return ensure_policy(db)

    def send_notification(
        self,
        db: Session,
        notification: Notification,
        *,
        manual: bool = False,
    ) -> Notification:
        policy = self._policy_for_notification(db, notification)
        now = datetime.now(timezone.utc)
        token = str(uuid4())
        failed_is_claimable = Notification.status == "failed"
        if not manual:
            failed_is_claimable = and_(
                Notification.status == "failed",
                Notification.next_retry_at.is_not(None),
                Notification.next_retry_at <= now,
                Notification.attempt_count < MAX_AUTOMATIC_ATTEMPTS,
            )
        claimed = db.execute(
            update(Notification)
            .where(
                Notification.id == notification.id,
                Notification.status != "sent",
                or_(
                    Notification.status == "pending",
                    failed_is_claimable,
                    and_(
                        Notification.status == "sending",
                        Notification.sending_until.is_not(None),
                        Notification.sending_until <= now,
                        Notification.attempt_count < MAX_AUTOMATIC_ATTEMPTS,
                    ),
                ),
            )
            .values(
                attempt_count=Notification.attempt_count + 1,
                status="sending",
                success=False,
                error=None,
                next_retry_at=None,
                delivery_token=token,
                sending_until=now + timedelta(minutes=DELIVERY_LEASE_MINUTES),
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        db.refresh(notification)
        if claimed.rowcount != 1:
            return notification

        retryable = True
        status = "sent"
        success = True
        error = None
        sent_at = None
        try:
            target = self._target(policy, notification.provider)
            delivered = self.adapter.send(target, notification.title, notification.content)
            if not delivered:
                raise RuntimeError("provider returned failure")
            sent_at = datetime.now(timezone.utc)
        except NotificationConfigurationError as exc:
            retryable = False
            status = "failed"
            success = False
            error = str(exc)[:1000]
        except Exception:
            status = "failed"
            success = False
            error = "notification provider failure"

        next_retry_at = None
        if status == "failed" and retryable and not manual and notification.attempt_count < MAX_AUTOMATIC_ATTEMPTS:
            delay_minutes = 2 ** (notification.attempt_count - 1)
            next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
        db.execute(
            update(Notification)
            .where(
                Notification.id == notification.id,
                Notification.delivery_token == token,
            )
            .values(
                status=status,
                success=success,
                error=error,
                next_retry_at=next_retry_at,
                sent_at=sent_at,
                delivery_token=None,
                sending_until=None,
                updated_at=datetime.now(timezone.utc),
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        db.refresh(notification)
        return notification

    def retry_due(self, db: Session) -> int:
        now = datetime.now(timezone.utc)
        due = db.scalars(
            select(Notification)
            .where(
                or_(
                    and_(
                        Notification.status == "failed",
                        Notification.next_retry_at.is_not(None),
                        Notification.next_retry_at <= now,
                    ),
                    and_(
                        Notification.status == "sending",
                        Notification.sending_until.is_not(None),
                        Notification.sending_until <= now,
                    ),
                ),
                Notification.attempt_count < MAX_AUTOMATIC_ATTEMPTS,
            )
            .order_by(Notification.id)
        ).all()
        attempted = 0
        for notification in due:
            previous_attempts = notification.attempt_count
            refreshed = self.send_notification(db, notification)
            if refreshed.attempt_count > previous_attempts:
                attempted += 1
        return attempted
