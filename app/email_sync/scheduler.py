from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.database.models import EmailAccount

from .service import EmailSyncCoordinator


class LocalScheduler:
    """Single-process scheduler for due email accounts and notification retries."""

    def __init__(
        self,
        session_factory: Any,
        email_coordinator: EmailSyncCoordinator,
        notification_service: Any,
        model_health: Any = None,
    ) -> None:
        self.session_factory = session_factory
        self.email_coordinator = email_coordinator
        self.notification_service = notification_service
        self.model_health = model_health
        self._scheduler = None

    @staticmethod
    def _is_due(account: EmailAccount, current: datetime) -> bool:
        if not account.enabled:
            return False
        if account.last_sync_at is None:
            return True
        last_sync = account.last_sync_at
        if last_sync.tzinfo is None:
            last_sync = last_sync.replace(tzinfo=timezone.utc)
        return last_sync + timedelta(minutes=account.check_interval_minutes) <= current

    def run_once(self) -> dict[str, int]:
        current = datetime.now(timezone.utc)
        with self.session_factory() as db:
            accounts = db.scalars(
                select(EmailAccount).where(EmailAccount.enabled.is_(True)).order_by(EmailAccount.id)
            ).all()
            due_ids = [account.id for account in accounts if self._is_due(account, current)]

        synced = failed = 0
        for account_id in due_ids:
            with self.session_factory() as db:
                try:
                    self.email_coordinator.sync(db, account_id)
                    synced += 1
                except Exception:
                    # EmailSyncService stores a redacted, user-visible account error.
                    failed += 1

        with self.session_factory() as db:
            retried = self.notification_service.retry_due(db)
        return {"synced": synced, "failed": failed, "notifications_retried": retried}

    def start(self) -> None:
        if self._scheduler is not None:
            return
        from apscheduler.schedulers.background import BackgroundScheduler

        self._scheduler = BackgroundScheduler(timezone="UTC")
        if self.model_health is not None:
            self.model_health.reset_schedule()
            self._scheduler.add_job(self.model_health.run_once, "interval", seconds=15,
                                    id="model-health", coalesce=True, max_instances=1)
        self._scheduler.add_job(
            self.run_once,
            "interval",
            minutes=1,
            id="local-job-assistant-sync",
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        self._scheduler.start()

    def shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
