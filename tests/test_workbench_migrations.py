from alembic import command
from alembic.config import Config
from alembic.operations.base import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
import pytest
import shutil
from pathlib import Path

from app.core.config import settings
from app.credentials import UnavailableCredentialStore
from app.main import create_app


def test_0014_workbench_upgrade_preserves_synthetic_legacy_rows(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'workbench-migration.db').as_posix()}"
    original_url = settings.database_url
    settings.database_url = database_url
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "0013_email_cleaning_jobs")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO companies (id, name, aliases, notes, created_at) "
                    "VALUES (11, '合成公司', '[\"别名\"]', '公司备注', CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO jobs (id, company_id, title, location, employment_type, "
                    "job_category, description, source_url, source_website, original_title, "
                    "published_at, structured_data, created_at, updated_at) "
                    "VALUES (22, 11, '合成岗位', '厦门', '校招', '数据分析', '岗位描述', "
                    "'https://example.test/job', 'fixture', '原始岗位名', '2026-09-01', "
                    "'{\"salary\":\"不推断回填\"}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO applications (id, job_id, status, notes, status_updated_at) "
                    "VALUES (33, 22, '已投递', '申请备注', CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO email_accounts (id, email_address, provider_type, imap_host, "
                    "imap_port, use_ssl, username, enabled, check_interval_minutes, "
                    "sync_lookback_days, uid_validity, last_uid, last_sync_status, "
                    "credential_id, created_at, updated_at) VALUES "
                    "(44, 'fixture@example.test', 'custom', 'imap.fixture.test', 993, 1, "
                    "'fixture@example.test', 1, 15, 30, 9876, 42, 'success', "
                    "'credential-reference-only', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO email_messages (id, email_account_id, message_id, uid_validity, "
                    "uid, sender, subject, received_at, body_text, pre_filtered, llm_processed, "
                    "synced_at) VALUES (55, 44, '<fixture-55>', 9876, 42, 'hr@example.test', "
                    "'合成邮件', CURRENT_TIMESTAMP, '合成邮件正文', 1, 1, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO email_cleaning_results (id, email_message_id, status, version, "
                    "summary, company_name, job_title, created_at, updated_at) VALUES "
                    "(88, 55, 'completed', 3, '清洗摘要', '合成公司', '合成岗位', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO recruitment_events (id, company_id, job_id, application_id, "
                    "source_email_id, email_job_entry_id, company_name, job_title, event_type, action_required, "
                    "confidence, ai_summary, association_status, created_at, updated_at) "
                    "VALUES (66, 11, 22, 33, 55, 99, '合成公司', '合成岗位', 'INTERVIEW', 1, "
                    "0.9, 'fixture event', 'confirmed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO email_job_entries (id, identity_key, cleaning_result_id, "
                    "company_id, job_id, application_id, recruitment_event_id, company_name, "
                    "job_title, work_location, event_type, summary, match_status, active, "
                    "manually_associated, created_at, updated_at) VALUES "
                    "(99, 'fixture-entry-99', 88, 11, 22, 33, 66, '合成公司', '合成岗位', "
                    "'厦门', 'INTERVIEW', '条目摘要', 'confirmed', 1, 1, CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO email_archives (id, cleaning_result_id, status, requested_at, "
                    "archived_at, created_at, updated_at) VALUES "
                    "(100, 88, 'archived', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO notifications (id, event_id, provider, title, content, status, "
                    "success, attempt_count, idempotency_key, created_at, updated_at) "
                    "VALUES (77, 66, 'desktop', '合成提醒', 'fixture', 'sent', 1, 1, "
                    "'fixture-notification-77', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

        command.upgrade(config, "head")
        inspector = inspect(engine)
        job_columns = {column["name"] for column in inspector.get_columns("jobs")}
        application_columns = {column["name"] for column in inspector.get_columns("applications")}
        assert {
            "category_review_status",
            "tags",
            "organize_status",
            "salary_text",
            "notes",
            "version",
        }.issubset(job_columns)
        assert "version" in application_columns

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT id, company_id, title, job_category, category_review_status, tags, "
                    "organize_status, salary_text, notes, version FROM jobs WHERE id = 22"
                )
            ).one() == (
                22,
                11,
                "合成岗位",
                "数据分析",
                "pending",
                "[]",
                "pending",
                None,
                None,
                1,
            )
            assert connection.execute(
                text("SELECT id, job_id, status, notes, version FROM applications WHERE id = 33")
            ).one() == (33, 22, "已投递", "申请备注", 1)
            assert connection.execute(
                text(
                    "SELECT uid_validity, last_uid, last_sync_status, credential_id "
                    "FROM email_accounts WHERE id = 44"
                )
            ).one() == (9876, 42, "success", "credential-reference-only")
            assert connection.execute(
                text(
                    "SELECT email_account_id, uid_validity, uid, body_text "
                    "FROM email_messages WHERE id = 55"
                )
            ).one() == (44, 9876, 42, "合成邮件正文")
            assert connection.execute(
                text(
                    "SELECT source_email_id, email_job_entry_id, application_id, association_status "
                    "FROM recruitment_events WHERE id = 66"
                )
            ).one() == (55, 99, 33, "confirmed")
            assert connection.execute(
                text(
                    "SELECT cleaning_result_id, recruitment_event_id, application_id, "
                    "manually_associated, match_status, source_email_id, facts_source, version, "
                    "event_round FROM email_job_entries WHERE id = 99"
                )
            ).one() == (88, 66, 33, 1, "confirmed", 55, "ai", 1, None)
            assert connection.execute(
                text(
                    "SELECT id, email_job_entry_id, source_email_id, application_id, "
                    "association_status FROM recruitment_events WHERE id = 66"
                )
            ).one() == (66, 99, 55, 33, "confirmed")
            assert connection.execute(
                text("SELECT version, summary FROM email_cleaning_results WHERE id = 88")
            ).one() == (3, "清洗摘要")
            assert connection.execute(
                text("SELECT status, archived_at IS NOT NULL FROM email_archives WHERE id = 100")
            ).one() == ("archived", 1)
            assert connection.execute(
                text("SELECT event_id, provider, status, success, attempt_count, idempotency_key "
                     "FROM notifications WHERE id = 77")
            ).one() == (66, "desktop", "sent", 1, 1, "fixture-notification-77")
            assert connection.execute(
                text("SELECT COUNT(*) FROM application_status_history")
            ).scalar_one() == 0
            assert connection.execute(
                text("SELECT COUNT(*) FROM email_manual_fact_requests")
            ).scalar_one() == 0
        engine.dispose()

        app = create_app(
            database_url,
            credential_store=UnavailableCredentialStore(),
            enable_scheduler=False,
        )
        with TestClient(app) as client:
            entries = client.get("/api/email-job-entries", params={"cleaning_id": 88})
            assert entries.status_code == 200, entries.text
            entry = next(item for item in entries.json() if item["id"] == 99)
            assert entry["source_email_id"] == 55
            assert entry["facts_source"] == "ai"
            assert entry["manually_associated"] is True
            events = client.get("/api/recruitment-events", params={"association_status": "confirmed"})
            assert events.status_code == 200, events.text
            assert any(
                event["id"] == 66 and event["email_job_entry_id"] == 99
                for event in events.json()
            )
    finally:
        settings.database_url = original_url


def test_failed_synthetic_upgrade_restores_backup_for_legacy_api(tmp_path, monkeypatch):
    database_path = tmp_path / "restore-target.sqlite3"
    backup_path = tmp_path / "restore-backup.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    original_url = settings.database_url
    settings.database_url = database_url
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "0013_email_cleaning_jobs")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO companies (id, name, aliases, notes, created_at) "
                    "VALUES (211, '恢复演练公司', '[]', '合成备注', CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO jobs (id, company_id, title, location, employment_type, "
                    "job_category, description, source_url, source_website, original_title, "
                    "published_at, structured_data, created_at, updated_at) VALUES "
                    "(222, 211, '恢复演练岗位', '厦门', '实习', '其他', '合成描述', "
                    "'https://example.test/restore', 'fixture', '旧标题', '2026-09-01', "
                    "'{\"source\":\"synthetic\"}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
        engine.dispose()

        # Prepare the current legacy-compatible baseline and save a standalone
        # backup before the injected 0016 failure.
        command.upgrade(config, "0015_import_drafts")
        shutil.copy2(database_path, backup_path)
        assert backup_path.exists()

        original_create_table = Operations.create_table

        def fail_on_progress_table(self, table_name, *args, **kwargs):
            if table_name == "email_progress_suggestions":
                raise RuntimeError("injected synthetic migration failure")
            return original_create_table(self, table_name, *args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(Operations, "create_table", fail_on_progress_table)
            with pytest.raises(RuntimeError, match="injected synthetic migration failure"):
                command.upgrade(config, "head")

        # The failed target is closed; restore only this temporary test DB from
        # the saved synthetic 0015 backup, then read it through the old jobs API.
        for suffix in ("-wal", "-shm"):
            Path(f"{database_path}{suffix}").unlink(missing_ok=True)
        shutil.copy2(backup_path, database_path)
        app = create_app(
            database_url,
            credential_store=UnavailableCredentialStore(),
            enable_scheduler=False,
        )
        with TestClient(app) as client:
            jobs = client.get("/api/jobs")
            assert jobs.status_code == 200, jobs.text
            restored = next(item for item in jobs.json() if item["id"] == 222)
            assert restored["title"] == "恢复演练岗位"
            assert restored["company_id"] == 211
    finally:
        settings.database_url = original_url
