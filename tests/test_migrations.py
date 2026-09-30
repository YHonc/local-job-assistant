from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import settings


def test_alembic_upgrade_builds_v0_through_v3_schema(tmp_path):
    database_path = tmp_path / "migrated.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    old_database_url = settings.database_url
    settings.database_url = database_url
    try:
        config = Config("alembic.ini")
        command.upgrade(config, "head")
        engine = create_engine(database_url)
        inspector = inspect(engine)
        assert {
            "companies",
            "jobs",
            "applications",
            "settings",
            "llm_configurations",
            "email_accounts",
            "email_messages",
            "recruitment_events",
            "notification_policies",
            "notifications",
        }.issubset(set(inspector.get_table_names()))
        email_columns = {column["name"] for column in inspector.get_columns("email_accounts")}
        message_columns = {column["name"] for column in inspector.get_columns("email_messages")}
        policy_columns = {column["name"] for column in inspector.get_columns("notification_policies")}
        notification_columns = {column["name"] for column in inspector.get_columns("notifications")}
        job_columns = {column["name"] for column in inspector.get_columns("jobs")}
        routing_columns = {column["name"] for column in inspector.get_columns("llm_routing_settings")}
        llm_columns = {column["name"] for column in inspector.get_columns("llm_configurations")}
        assert "authorization_code" not in email_columns
        assert "push_url" not in policy_columns
        assert "credential_id" in email_columns
        assert "sync_lookback_days" in email_columns
        assert "llm_attempt_count" in message_columns
        assert "uid_validity" in email_columns
        assert {"total_prompt_tokens", "total_completion_tokens"}.issubset(llm_columns)
        assert "uid_validity" in message_columns
        assert "push_credential_id" in policy_columns
        assert {"delivery_token", "sending_until"}.issubset(notification_columns)
        assert "email_config_id" in routing_columns
        assert "job_category" in job_columns
        assert "published_at" in job_columns
        assert "ix_jobs_job_category" in {index["name"] for index in inspector.get_indexes("jobs")}
        assert "ix_jobs_published_at" in {index["name"] for index in inspector.get_indexes("jobs")}
        engine.dispose()
    finally:
        settings.database_url = old_database_url


def _migrate(tmp_path, revision):
    database_url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    old_database_url = settings.database_url
    settings.database_url = database_url
    config = Config("alembic.ini")
    command.upgrade(config, revision)
    return database_url, old_database_url, config


def test_job_category_upgrade_backfills_existing_jobs(tmp_path):
    database_url, old_url, config = _migrate(tmp_path, "0009_llm_tokens")
    try:
        engine = create_engine(database_url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO companies (id, name, created_at) VALUES (1, 'Example', CURRENT_TIMESTAMP)"))
            conn.execute(text("""
                INSERT INTO jobs (id, company_id, title, description, created_at, updated_at)
                VALUES
                    (1, 1, '数据分析实习生', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    (2, 1, '用户运营', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    (3, 1, '未命名岗位', '暂无明确职责', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """))
        command.upgrade(config, "head")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT id, job_category FROM jobs ORDER BY id")).all() == [
                (1, "数据分析"),
                (2, "运营"),
                (3, "其他"),
            ]
    finally:
        settings.database_url = old_url


def test_job_publication_date_upgrade_preserves_existing_jobs(tmp_path):
    database_url, old_url, config = _migrate(tmp_path, "0010_job_category")
    try:
        engine = create_engine(database_url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO companies (id, name, created_at) VALUES (1, 'Example', CURRENT_TIMESTAMP)"))
            conn.execute(text("INSERT INTO jobs (id, company_id, title, job_category, created_at, updated_at) VALUES (1, 1, 'Analyst', '数据分析', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        command.upgrade(config, "head")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM jobs")).scalar_one() == 1
            assert conn.execute(text("SELECT published_at FROM jobs WHERE id = 1")).scalar_one() is None
    finally:
        settings.database_url = old_url


def test_multi_llm_upgrade_migrates_legacy_row_and_creates_routing(tmp_path):
    database_url, old_url, config = _migrate(tmp_path, "0003_v2_v3")
    try:
        engine = create_engine(database_url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO llm_configurations (id, provider, model, credential_id, updated_at) VALUES (1, 'openai', 'gpt-4o', 'cred', CURRENT_TIMESTAMP)"))
        command.upgrade(config, "head")
        with engine.connect() as conn:
            row = conn.execute(text("SELECT display_name, enabled, credential_id, call_count FROM llm_configurations")).one()
            assert row == ("默认配置", True, "cred", 0)
            assert conn.execute(text("SELECT default_config_id FROM llm_routing_settings")).scalar_one() == 1
    finally:
        settings.database_url = old_url


def test_multi_llm_upgrade_fresh_database_has_no_routing_row(tmp_path):
    database_url, old_url, config = _migrate(tmp_path, "0003_v2_v3")
    try:
        command.upgrade(config, "head")
        engine = create_engine(database_url)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM llm_configurations")).scalar_one() == 0
            assert conn.execute(text("SELECT COUNT(*) FROM llm_routing_settings")).scalar_one() == 0
    finally:
        settings.database_url = old_url


def test_multi_llm_downgrade_single_row_restores_legacy_schema(tmp_path):
    database_url, old_url, config = _migrate(tmp_path, "head")
    try:
        engine = create_engine(database_url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO llm_configurations (id, provider, model, updated_at, display_name, enabled, created_at, call_count, success_count, failure_count, total_duration_ms) VALUES (1, 'openai', 'm', CURRENT_TIMESTAMP, 'default', 1, CURRENT_TIMESTAMP, 0, 0, 0, 0)"))
        command.downgrade(config, "0003_v2_v3")
        assert {c["name"] for c in inspect(engine).get_columns("llm_configurations")} == {"id", "provider", "model", "api_base", "temperature", "timeout_seconds", "credential_id", "updated_at"}
    finally:
        settings.database_url = old_url


def test_multi_llm_downgrade_rejects_multiple_rows_without_deleting(tmp_path):
    database_url, old_url, config = _migrate(tmp_path, "head")
    try:
        engine = create_engine(database_url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO llm_configurations (id, provider, model, updated_at, display_name, enabled, created_at, call_count, success_count, failure_count, total_duration_ms) VALUES (1, 'openai', 'm1', CURRENT_TIMESTAMP, 'one', 1, CURRENT_TIMESTAMP, 0, 0, 0, 0), (2, 'openai', 'm2', CURRENT_TIMESTAMP, 'two', 1, CURRENT_TIMESTAMP, 0, 0, 0, 0)"))
        import pytest
        with pytest.raises(RuntimeError, match="more than one row"):
            command.downgrade(config, "0003_v2_v3")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM llm_configurations")).scalar_one() == 2
    finally:
        settings.database_url = old_url


def test_multi_llm_downgrade_rejects_single_non_one_id_without_deleting(tmp_path):
    database_url, old_url, config = _migrate(tmp_path, "head")
    try:
        engine = create_engine(database_url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO llm_configurations (id, provider, model, updated_at, display_name, enabled, created_at, call_count, success_count, failure_count, total_duration_ms) VALUES (2, 'openai', 'm2', CURRENT_TIMESTAMP, 'two', 1, CURRENT_TIMESTAMP, 0, 0, 0, 0)"))
            conn.execute(text("INSERT INTO llm_routing_settings (id, auto_select_enabled, default_config_id, max_parallel_calls, updated_at) VALUES (1, 0, 2, 1, CURRENT_TIMESTAMP)"))
        import pytest
        with pytest.raises(RuntimeError, match="id is not 1"):
            command.downgrade(config, "0003_v2_v3")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT id, model FROM llm_configurations")).one() == (2, "m2")
            assert conn.execute(text("SELECT id, default_config_id FROM llm_routing_settings")).one() == (1, 2)
    finally:
        settings.database_url = old_url


def test_email_0013_preserves_legacy_manual_event_notifications_and_blocks_lossy_downgrade(tmp_path):
    """Old 0012 email data keeps associations while 0013 permits many events."""
    database_url, old_url, config = _migrate(tmp_path, "0012_llm_usage_events")
    try:
        engine = create_engine(database_url)
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO companies (id, name, created_at)
                VALUES (1, '旧公司', CURRENT_TIMESTAMP)
            """))
            conn.execute(text("""
                INSERT INTO jobs (id, company_id, title, location, created_at, updated_at)
                VALUES (1, 1, '旧岗位', '厦门', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """))
            conn.execute(text("""
                INSERT INTO applications (id, job_id, status, status_updated_at)
                VALUES (1, 1, '已投递', CURRENT_TIMESTAMP)
            """))
            conn.execute(text("""
                INSERT INTO email_accounts (
                    id, email_address, provider_type, imap_host, imap_port,
                    use_ssl, username, enabled, check_interval_minutes,
                    last_uid, last_sync_status, created_at, updated_at
                ) VALUES (
                    1, 'legacy@example.test', 'custom', 'imap.example.test', 993,
                    1, 'legacy@example.test', 1, 15,
                    1, 'success', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                INSERT INTO email_messages (
                    id, email_account_id, message_id, uid_validity, uid,
                    sender, subject, received_at, body_text, pre_filtered,
                    llm_processed, llm_classification, synced_at
                ) VALUES (
                    1, 1, '<legacy-1@example.test>', 7, 1,
                    'hr@example.test', '旧邮件', CURRENT_TIMESTAMP, '旧邮件正文',
                    1, 1, 'INTERVIEW', CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                INSERT INTO recruitment_events (
                    id, company_id, job_id, application_id, source_email_id,
                    company_name, job_title, event_type, action_required,
                    confidence, ai_summary, association_status, created_at, updated_at
                ) VALUES (
                    1, 1, 1, 1, 1,
                    '旧公司', '旧岗位', 'INTERVIEW', 1,
                    0.95, '旧事件摘要', 'confirmed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                INSERT INTO notifications (
                    id, event_id, provider, title, content, status, success,
                    attempt_count, idempotency_key, created_at, updated_at
                ) VALUES (
                    1, 1, 'desktop', '旧通知', '旧通知内容', 'sent', 1,
                    1, 'legacy-event-1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            """))

        command.upgrade(config, "head")
        with engine.begin() as conn:
            legacy = conn.execute(text("""
                SELECT source_email_id, application_id, association_status, active
                FROM recruitment_events WHERE id = 1
            """)).one()
            assert legacy == (1, 1, "confirmed", 1)
            assert conn.execute(text("SELECT event_id, idempotency_key FROM notifications WHERE id = 1")).one() == (1, "legacy-event-1")

            # The old source-email uniqueness is intentionally removed; a
            # second event for the same legacy message can now be inserted.
            conn.execute(text("""
                INSERT INTO recruitment_events (
                    id, source_email_id, company_name, job_title, event_type,
                    action_required, confidence, ai_summary, association_status,
                    active, created_at, updated_at
                ) VALUES (
                    2, 1, '旧公司', '旧岗位二', 'OFFER',
                    0, 0.8, '第二个事件', 'unmatched',
                    1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                INSERT INTO email_cleaning_results (
                    id, email_message_id, status, version, created_at, updated_at
                ) VALUES (1, 1, 'completed', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """))
            assert conn.execute(text("SELECT COUNT(*) FROM recruitment_events WHERE source_email_id = 1")).scalar_one() == 2

        # Downgrade must stop before dropping the durable cleaning snapshot;
        # the old event and notification rows are still present afterwards.
        import pytest
        with pytest.raises(RuntimeError, match="Cannot downgrade email cleaning data"):
            command.downgrade(config, "0012_llm_usage_events")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM email_cleaning_results")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM recruitment_events WHERE source_email_id = 1")).scalar_one() == 2
            assert conn.execute(text("SELECT event_id FROM notifications WHERE id = 1")).scalar_one() == 1
    finally:
        settings.database_url = old_url
