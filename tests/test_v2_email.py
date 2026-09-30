import json
import threading
import time
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.database.models import EmailAccount
from app.email_sync.provider import EmailEnvelope, EmailFetchBatch, EmailNetworkError, ImapToolsProvider
from app.email_sync.scheduler import LocalScheduler
from app.main import create_app


class FakeCredentialStore:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, credential_id):
        return self.values.get(credential_id)

    def set(self, credential_id, secret):
        self.values[credential_id] = secret

    def delete(self, credential_id):
        self.values.pop(credential_id, None)


class FakeProvider:
    def __init__(self, envelopes=None, *, failure=None):
        self.envelopes = list(envelopes or [])
        self.failure = failure
        self.tested = False
        self.last_uid_calls: list[int] = []

    def test_connection(self):
        if self.failure:
            raise self.failure
        self.tested = True

    def fetch_since(self, last_uid):
        self.last_uid_calls.append(last_uid)
        if self.failure:
            raise self.failure
        return [item for item in self.envelopes if item.uid > last_uid]


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def send(self, target_url, title, body):
        self.calls.append((target_url, title, body))
        return True


def make_envelope(uid, subject, body, *, message_id=None, sender="hr@example.com"):
    return EmailEnvelope(
        uid=uid,
        message_id=message_id or f"<message-{uid}@example.com>",
        sender=sender,
        subject=subject,
        received_at=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
        body_text=body,
    )


def classification_payload(**overrides):
    payload = {
        "is_recruitment": True,
        "event_type": "INTERVIEW",
        "company_name": "示例科技",
        "job_title": "数据分析实习生",
        "event_time": "2026-08-25T14:00:00+08:00",
        "deadline": "2026-08-24T18:00:00+08:00",
        "location": "厦门软件园",
        "meeting_url": "https://meet.example.test/interview",
        "action_required": True,
        "confidence": 0.93,
        "summary": "请确认面试时间并按时参加。",
        "suggested_application_status": "一面",
    }
    payload.update(overrides)
    return payload


def configure_llm(client):
    response = client.put(
        "/api/llm/config",
        json={
            "provider": "ollama",
            "model": "llama3",
            "api_base": "http://127.0.0.1:11434",
            "temperature": 0,
            "timeout_seconds": 20,
        },
    )
    assert response.status_code == 200


def create_account(client, **overrides):
    payload = {
        "email_address": "candidate@qq.com",
        "provider_type": "qq",
        "authorization_code": "mail-secret",
        "check_interval_minutes": 5,
    }
    payload.update(overrides)
    return client.post("/api/email-accounts", json=payload)


def make_client(tmp_path, provider_factory, completion, notifier=None):
    store = FakeCredentialStore()
    app = create_app(
        f"sqlite:///{tmp_path / 'v2.db'}",
        create_tables=True,
        credential_store=store,
        llm_completion=completion,
        email_provider_factory=provider_factory,
        notification_adapter=notifier or FakeNotifier(),
    )
    return app, store


def test_email_presets_custom_validation_and_secret_boundary(tmp_path):
    provider = FakeProvider()
    app, store = make_client(tmp_path, lambda account, secret: provider, lambda **kwargs: None)
    with TestClient(app) as client:
        qq = create_account(
            client,
            imap_host="attacker.invalid",
            imap_port=143,
        )
        assert qq.status_code == 201
        body = qq.json()
        assert body["imap_host"] == "imap.qq.com"
        assert body["imap_port"] == 993
        assert body["use_ssl"] is True
        assert body["has_authorization_code"] is True
        assert "authorization_code" not in body
        assert "mail-secret" not in qq.text
        credential_id = next(iter(store.values))
        assert credential_id.startswith("email:")
        assert store.values[credential_id] == "mail-secret"

        netease = create_account(
            client,
            email_address="candidate@163.com",
            provider_type="163",
            authorization_code=None,
        )
        assert netease.status_code == 201
        assert netease.json()["imap_host"] == "imap.163.com"
        assert netease.json()["has_authorization_code"] is False

        invalid_custom = create_account(
            client,
            email_address="candidate@example.com",
            provider_type="custom",
            imap_host=None,
            imap_port=None,
        )
        assert invalid_custom.status_code == 422

        custom = create_account(
            client,
            email_address="candidate@example.com",
            provider_type="custom",
            imap_host="mail.example.com",
            imap_port=993,
            use_ssl=True,
            username="candidate-login",
        )
        assert custom.status_code == 201
        assert custom.json()["username"] == "candidate-login"
        assert "mail-secret" not in client.get("/api/email-accounts").text

        cleared = client.patch(
            f"/api/email-accounts/{qq.json()['id']}",
            json={"clear_authorization_code": True},
        )
        assert cleared.status_code == 200
        assert cleared.json()["has_authorization_code"] is False
        assert credential_id not in store.values


def test_irrelevant_email_is_stored_but_never_sent_to_llm(tmp_path):
    provider = FakeProvider(
        [make_envelope(1, "商场优惠券", "本周末全场折扣。")]
    )

    def forbidden_completion(**kwargs):
        raise AssertionError("irrelevant mail must not reach the LLM")

    app, _ = make_client(tmp_path, lambda account, secret: provider, forbidden_completion)
    with TestClient(app) as client:
        account = create_account(client).json()
        result = client.post(f"/api/email-accounts/{account['id']}/sync")
        assert result.status_code == 200
        assert result.json() == {
            "account_id": account["id"],
            "fetched": 1,
            "created": 1,
            "duplicates": 0,
            "filtered_candidates": 0,
            "events_created": 0,
            "processing_errors": 0,
            "skipped_by_regex": 0,
            "retry_paused": 0,
        }
        messages = client.get("/api/email-messages").json()
        assert messages[0]["pre_filtered"] is False
        assert messages[0]["llm_processed"] is False
        assert client.get("/api/recruitment-events").json() == []


def test_incremental_sync_classifies_matches_and_never_changes_status(tmp_path):
    captured = []

    def completion(**kwargs):
        captured.append(kwargs)
        return {
            "choices": [
                {"message": {"content": json.dumps(classification_payload(), ensure_ascii=False)}}
            ]
        }

    provider = FakeProvider(
        [make_envelope(10, "面试邀请", "PAGE_PRIVATE_MAIL：请确认面试时间。")]
    )
    notifier = FakeNotifier()
    app, _ = make_client(
        tmp_path,
        lambda account, secret: provider,
        completion,
        notifier,
    )
    with TestClient(app) as client:
        configure_llm(client)
        company = client.post(
            "/api/companies",
            json={"name": "示例科技", "aliases": ["示例集团"]},
        ).json()
        job = client.post(
            "/api/jobs",
            json={"company_id": company["id"], "title": "数据分析实习生"},
        ).json()
        application = client.post(
            "/api/applications",
            json={"job_id": job["id"], "status": "已投递"},
        ).json()
        account = create_account(client).json()

        synced = client.post(f"/api/email-accounts/{account['id']}/sync")
        assert synced.status_code == 200
        assert synced.json()["events_created"] == 1
        assert provider.last_uid_calls == [0]
        assert "<untrusted_email_data>" in captured[0]["messages"][1]["content"]
        assert "PAGE_PRIVATE_MAIL" in captured[0]["messages"][1]["content"]

        event = client.get("/api/recruitment-events").json()[0]
        assert event["event_type"] == "INTERVIEW"
        assert event["application_id"] == application["id"]
        assert event["suggested_application_id"] == application["id"]
        assert event["association_status"] == "auto_bound"
        assert event["suggested_application_status"] == "一面"
        assert event["candidates"][0]["application_id"] == application["id"]
        assert client.get(f"/api/applications/{application['id']}").json()["status"] == "已投递"

        confirmed = client.patch(
            f"/api/recruitment-events/{event['id']}/association",
            json={"application_id": application["id"]},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["association_status"] == "confirmed"
        assert confirmed.json()["application_id"] == application["id"]
        assert client.get(f"/api/applications/{application['id']}").json()["status"] == "已投递"

        assert len(notifier.calls) == 1
        assert notifier.calls[0][0] == "windows://"

        provider.envelopes.append(
            make_envelope(
                11,
                "面试邀请（重复消息）",
                "same",
                message_id="<message-10@example.com>",
            )
        )
        second = client.post(f"/api/email-accounts/{account['id']}/sync")
        assert second.status_code == 200
        assert second.json()["duplicates"] == 1
        assert provider.last_uid_calls == [0, 10]
        assert len(client.get("/api/email-messages").json()) == 1
        assert len(client.get("/api/recruitment-events").json()) == 1


def test_keyword_configuration_and_processed_non_recruitment_is_idempotent(tmp_path):
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        payload = classification_payload(is_recruitment=False, event_type="OTHER")
        return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}

    provider = FakeProvider([make_envelope(1, "专属暗号", "候选内容")])
    app, _ = make_client(tmp_path, lambda account, secret: provider, completion)
    with TestClient(app) as client:
        configure_llm(client)
        assert client.put(
            "/api/email-filter",
            json={"keywords": ["专属暗号", "专属暗号"]},
        ).json() == {"keywords": ["专属暗号"], "regex": None}
        account = create_account(client).json()
        result = client.post(f"/api/email-accounts/{account['id']}/sync")
        assert result.json()["filtered_candidates"] == 1
        message = client.get("/api/email-messages").json()[0]
        assert message["llm_classification"] == "NOT_RECRUITMENT"
        assert len(calls) == 1
        retried = client.post(f"/api/email-messages/{message['id']}/process")
        assert retried.status_code == 200
        assert retried.json()["classification"] == "NOT_RECRUITMENT"
        assert len(calls) == 1


def test_failed_candidate_is_retried_from_local_queue_without_refetch(tmp_path):
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TimeoutError("late")
        return {
            "choices": [
                {"message": {"content": json.dumps(classification_payload(), ensure_ascii=False)}}
            ]
        }

    provider = FakeProvider([make_envelope(1, "面试邀请", "请确认面试时间")])
    app, _ = make_client(tmp_path, lambda account, secret: provider, completion)
    with TestClient(app) as client:
        configure_llm(client)
        account = create_account(client).json()
        first = client.post(f"/api/email-accounts/{account['id']}/sync")
        assert first.status_code == 200
        assert first.json()["processing_errors"] == 1
        assert client.get("/api/recruitment-events").json() == []

        second = client.post(f"/api/email-accounts/{account['id']}/sync")
        assert second.status_code == 200
        assert second.json()["fetched"] == 0
        assert second.json()["events_created"] == 1
        assert second.json()["processing_errors"] == 0
        assert provider.last_uid_calls == [0, 1]
        assert len(client.get("/api/email-messages").json()) == 1
        assert len(client.get("/api/recruitment-events").json()) == 1
        assert len(calls) == 2


def test_failed_llm_classification_caps_and_manual_recovery(tmp_path):
    """Repeated LLM failures stop auto-retrying after MAX_LLM_ATTEMPTS; manual processing still works."""
    calls = []
    state = {"fail": True}

    def completion(**kwargs):
        calls.append(kwargs)
        if state["fail"]:
            raise TimeoutError("late")
        return {
            "choices": [
                {"message": {"content": json.dumps(classification_payload(), ensure_ascii=False)}}
            ]
        }

    provider = FakeProvider([make_envelope(1, "面试邀请", "请确认面试时间")])
    app, _ = make_client(tmp_path, lambda account, secret: provider, completion)
    with TestClient(app) as client:
        configure_llm(client)
        account = create_account(client).json()

        for _ in range(4):
            result = client.post(f"/api/email-accounts/{account['id']}/sync").json()
            assert result["processing_errors"] == 1
            assert result["retry_paused"] == 0
        fifth = client.post(f"/api/email-accounts/{account['id']}/sync").json()
        assert fifth["processing_errors"] == 1
        assert fifth["retry_paused"] == 1
        assert len(calls) == 5

        message = client.get("/api/email-messages").json()[0]
        assert message["llm_attempt_count"] == 5
        assert "已暂停自动重试" in message["processing_error"]
        refreshed = client.get(f"/api/email-accounts/{account['id']}").json()
        assert refreshed["last_sync_status"] == "partial"

        # 达到上限后，自动同步不再为该邮件调用模型。
        sixth = client.post(f"/api/email-accounts/{account['id']}/sync").json()
        assert sixth["processing_errors"] == 0
        assert sixth["retry_paused"] == 1
        assert len(calls) == 5
        refreshed = client.get(f"/api/email-accounts/{account['id']}").json()
        assert refreshed["last_sync_status"] == "partial"
        assert "paused" in refreshed["last_sync_error"]

        # 手动处理不受上限约束，成功后计数归零。
        state["fail"] = False
        manual = client.post(f"/api/email-messages/{message['id']}/process")
        assert manual.status_code == 200
        assert manual.json()["event_id"] is not None
        detail = client.get(f"/api/email-messages/{message['id']}").json()
        assert detail["llm_processed"] is True
        assert detail["llm_attempt_count"] == 0
        seventh = client.post(f"/api/email-accounts/{account['id']}/sync").json()
        assert seventh["retry_paused"] == 0
        refreshed_final = client.get(f"/api/email-accounts/{account['id']}").json()
        assert refreshed_final["last_sync_status"] == "success"


def test_parallel_classification_is_bounded_by_routing_setting(tmp_path):
    active = 0
    peak = 0
    guard = threading.Lock()

    def completion(**kwargs):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.15)
        with guard:
            active -= 1
        return {
            "choices": [
                {"message": {"content": json.dumps(classification_payload(), ensure_ascii=False)}}
            ]
        }

    provider = FakeProvider(
        [
            make_envelope(1, "面试邀请一", "请确认面试时间"),
            make_envelope(2, "面试邀请二", "请确认面试时间"),
        ]
    )
    app, _ = make_client(tmp_path, lambda account, secret: provider, completion)
    with TestClient(app) as client:
        configure_llm(client)
        routing = client.put("/api/llm/routing", json={"max_parallel_calls": 2})
        assert routing.status_code == 200
        assert routing.json()["max_parallel_calls"] == 2
        account = create_account(client).json()

        result = client.post(f"/api/email-accounts/{account['id']}/sync")
        assert result.status_code == 200
        assert result.json()["events_created"] == 2
        assert result.json()["processing_errors"] == 0
        assert peak == 2, "classification calls should run concurrently when routing allows it"
        assert len(client.get("/api/recruitment-events").json()) == 2


def test_regex_prefilter_runs_before_keywords(tmp_path):
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        payload = classification_payload(is_recruitment=False, event_type="OTHER")
        return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}

    provider = FakeProvider(
        [
            make_envelope(1, "面试邀请", "请确认面试时间"),
            make_envelope(2, "录用通知", "您已通过筛选"),
        ]
    )
    app, _ = make_client(tmp_path, lambda account, secret: provider, completion)
    with TestClient(app) as client:
        configure_llm(client)
        saved = client.put(
            "/api/email-filter",
            json={"keywords": ["面试", "录用"], "regex": "(面试|笔试|测评)"},
        )
        assert saved.status_code == 200
        assert saved.json()["regex"] == "(面试|笔试|测评)"
        invalid = client.put("/api/email-filter", json={"keywords": ["面试"], "regex": "(未闭合"})
        assert invalid.status_code == 422

        account = create_account(client).json()
        result = client.post(f"/api/email-accounts/{account['id']}/sync")
        assert result.status_code == 200
        assert result.json()["filtered_candidates"] == 1
        assert result.json()["skipped_by_regex"] == 1
        assert result.json()["events_created"] == 0
        assert len(calls) == 1
        messages = sorted(client.get("/api/email-messages").json(), key=lambda item: item["uid"])
        assert messages[0]["pre_filtered"] is True
        assert messages[1]["pre_filtered"] is False
        # 正则未命中的邮件只保留摘要，正文不落库。
        assert messages[0]["body_preview"] is not None
        assert messages[1]["body_preview"] is None
        detail = client.get(f"/api/email-messages/{messages[1]['id']}").json()
        assert detail["body_text"] is None
        assert detail["subject"] == "录用通知"


def test_regex_gate_keeps_metadata_row_for_dedup(tmp_path):
    """Regex-skipped emails stay as metadata rows so a UIDVALIDITY reset re-detects them."""

    def completion(**kwargs):
        payload = classification_payload(is_recruitment=False, event_type="OTHER")
        return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}

    class ValidityBumpProvider:
        def __init__(self):
            self.calls = 0

        def test_connection(self):
            return None

        def fetch_since(self, last_uid):
            self.calls += 1
            return EmailFetchBatch(
                uid_validity=self.calls,
                envelopes=[
                    make_envelope(1, "商场优惠券", "本周末全场折扣。"),
                    make_envelope(2, "面试邀请", "请确认面试时间"),
                ],
            )

    provider = ValidityBumpProvider()
    app, _ = make_client(tmp_path, lambda account, secret: provider, completion)
    with TestClient(app) as client:
        configure_llm(client)
        assert client.put(
            "/api/email-filter",
            json={"keywords": ["面试"], "regex": "(面试|笔试|测评)"},
        ).status_code == 200
        account = create_account(client).json()

        first = client.post(f"/api/email-accounts/{account['id']}/sync").json()
        assert first["created"] == 2
        assert first["skipped_by_regex"] == 1
        assert first["filtered_candidates"] == 1
        coupon = sorted(
            client.get("/api/email-messages").json(), key=lambda item: item["uid"]
        )[0]
        assert coupon["subject"] == "商场优惠券"
        assert coupon["body_preview"] is None

        # 游标重置后重新拉到同一批邮件：摘要行让它们被识别为重复而不是再次入库。
        second = client.post(f"/api/email-accounts/{account['id']}/sync").json()
        assert second["created"] == 0
        assert second["duplicates"] == 2
        assert len(client.get("/api/email-messages").json()) == 2


def test_manual_email_skips_prefilter_and_processes_during_sync(tmp_path):
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return {
            "choices": [
                {"message": {"content": json.dumps(classification_payload(), ensure_ascii=False)}}
            ]
        }

    provider = FakeProvider([])
    app, _ = make_client(tmp_path, lambda account, secret: provider, completion)
    with TestClient(app) as client:
        configure_llm(client)
        account = create_account(client).json()

        manual = client.post(
            "/api/email-messages/manual",
            json={
                "account_id": account["id"],
                "sender": "hr@example.com",
                "subject": "面试邀请（手动添加）",
                "body_text": "请确认面试时间。",
                "message_id": "<manual-1@example.com>",
            },
        )
        assert manual.status_code == 201
        body = manual.json()
        assert body["pre_filtered"] is True
        assert body["llm_processed"] is False
        assert body["uid_validity"] == -1
        assert calls == []

        duplicate = client.post(
            "/api/email-messages/manual",
            json={
                "account_id": account["id"],
                "sender": "hr@example.com",
                "subject": "面试邀请（手动添加）",
                "body_text": "same",
                "message_id": "<manual-1@example.com>",
            },
        )
        assert duplicate.status_code == 409

        synced = client.post(f"/api/email-accounts/{account['id']}/sync")
        assert synced.status_code == 200
        assert synced.json()["fetched"] == 0
        assert synced.json()["events_created"] == 1
        assert synced.json()["processing_errors"] == 0
        assert len(calls) == 1
        events = client.get("/api/recruitment-events").json()
        assert len(events) == 1
        assert events[0]["company_name"] == "示例科技"


def test_scheduler_isolates_account_failures(tmp_path):
    good_provider = FakeProvider([])
    bad_provider = FakeProvider(failure=EmailNetworkError("IMAP connection failed"))

    def provider_factory(account, secret):
        return bad_provider if account.email_address.startswith("bad") else good_provider

    app, _ = make_client(tmp_path, provider_factory, lambda **kwargs: None)
    with TestClient(app) as client:
        create_account(client, email_address="good@qq.com")
        bad = create_account(client, email_address="bad@qq.com").json()
        scheduler = LocalScheduler(
            app.state.session_factory,
            app.state.email_sync_coordinator,
            app.state.notification_service,
        )
        result = scheduler.run_once()
        assert result["synced"] == 1
        assert result["failed"] == 1
        bad_status = client.get(f"/api/email-accounts/{bad['id']}").json()
        assert bad_status["last_sync_status"] == "error"
        assert bad_status["last_sync_error"] == "IMAP connection failed"


def test_persistent_sync_lease_blocks_overlap_without_clearing_other_lease(tmp_path):
    provider = FakeProvider([])
    app, _ = make_client(tmp_path, lambda account, secret: provider, lambda **kwargs: None)
    with TestClient(app) as client:
        account = create_account(client).json()
        with app.state.session_factory() as db:
            record = db.get(EmailAccount, account["id"])
            record.sync_lease_until = datetime(2099, 1, 1, tzinfo=timezone.utc)
            db.commit()
        response = client.post(f"/api/email-accounts/{account['id']}/sync")
        assert response.status_code == 409, response.text
        with app.state.session_factory() as db:
            record = db.get(EmailAccount, account["id"])
            assert record.sync_lease_until is not None


def test_imap_tools_adapter_contract_with_mock_mailbox():
    class Folder:
        def get(self):
            return "INBOX"

        def status(self, folder_name):
            assert folder_name == "INBOX"
            return {"UIDVALIDITY": 42, "UIDNEXT": 9}

    class Message:
        uid = "8"
        from_ = "hr@example.com"
        subject = "面试邀请"
        date = datetime(2026, 8, 20, tzinfo=timezone.utc)
        text = "请确认面试"
        headers = {"message-id": ["<imap-tools@example.com>"]}

    class Mailbox:
        def __init__(self):
            self.folder = Folder()
            self.login_args = None
            self.criteria = None
            self.fetch_kwargs = None
            self.logout_count = 0

        def login(self, username, authorization_code, initial_folder):
            self.login_args = (username, authorization_code, initial_folder)
            return self

        def uids(self, criteria):
            self.criteria = criteria
            return ["7", "8"]

        def fetch(self, **kwargs):
            self.fetch_kwargs = kwargs
            return [Message()]

        def logout(self):
            self.logout_count += 1

    mailbox = Mailbox()
    account = SimpleNamespace(
        username="candidate@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        use_ssl=True,
        uid_validity=42,
    )
    provider = ImapToolsProvider(account, "authorization-code", lambda _: mailbox)
    provider.test_connection()
    batch = provider.fetch_since(7)
    assert mailbox.login_args == ("candidate@example.com", "authorization-code", "INBOX")
    assert mailbox.criteria == "UID 8:*"
    assert mailbox.fetch_kwargs == {"uid_list": ["8"], "mark_seen": False, "bulk": 50}
    assert batch.uid_validity == 42
    assert batch.envelopes[0].uid == 8
    assert batch.envelopes[0].message_id == "<imap-tools@example.com>"
    assert mailbox.logout_count == 2

    account.uid_validity = 41
    reset_batch = provider.fetch_since(7)
    assert mailbox.criteria == "UID 1:*"
    assert reset_batch.uid_validity == 42

    account.uid_validity = 42
    bounded = provider.fetch_since(7, since_date=date(2026, 7, 1))
    assert mailbox.criteria == "(SINCE 01-Jul-2026 UID 8:*)"
    assert bounded.uid_validity == 42


def test_uidvalidity_change_resets_cursor_without_uid_collision(tmp_path):
    class ValidityProvider:
        def __init__(self):
            self.calls = 0

        def test_connection(self):
            return None

        def fetch_since(self, last_uid):
            self.calls += 1
            validity = self.calls
            return EmailFetchBatch(
                uid_validity=validity,
                envelopes=[
                    make_envelope(
                        1,
                        "普通邮件",
                        f"validity-{validity}",
                        message_id=f"<validity-{validity}@example.com>",
                    )
                ],
            )

    provider = ValidityProvider()
    app, _ = make_client(tmp_path, lambda account, secret: provider, lambda **kwargs: None)
    with TestClient(app) as client:
        account = create_account(client).json()
        assert client.post(f"/api/email-accounts/{account['id']}/sync").status_code == 200
        assert client.post(f"/api/email-accounts/{account['id']}/sync").status_code == 200
        messages = client.get("/api/email-messages").json()
        assert sorted(item["uid_validity"] for item in messages) == [1, 2]
        refreshed = client.get(f"/api/email-accounts/{account['id']}").json()
        assert refreshed["uid_validity"] == 2
        assert refreshed["last_uid"] == 1


def test_sync_lookback_window_roundtrip_and_fetch_gate(tmp_path):
    class RecordingProvider(FakeProvider):
        def __init__(self, envelopes=None):
            super().__init__(envelopes)
            self.since_dates = []

        def fetch_since(self, last_uid, since_date=None):
            self.since_dates.append(since_date)
            return super().fetch_since(last_uid)

    provider = RecordingProvider([])
    app, _ = make_client(tmp_path, lambda account, secret: provider, lambda **kwargs: None)
    with TestClient(app) as client:
        bounded = create_account(client, sync_lookback_days=30).json()
        assert bounded["sync_lookback_days"] == 30
        assert client.post(f"/api/email-accounts/{bounded['id']}/sync").status_code == 200
        assert (datetime.now(timezone.utc).date() - provider.since_dates[-1]).days == 30

        updated = client.patch(
            f"/api/email-accounts/{bounded['id']}", json={"sync_lookback_days": 7}
        ).json()
        assert updated["sync_lookback_days"] == 7
        assert client.post(f"/api/email-accounts/{updated['id']}/sync").status_code == 200
        assert (datetime.now(timezone.utc).date() - provider.since_dates[-1]).days == 7

        cleared = client.patch(
            f"/api/email-accounts/{bounded['id']}", json={"sync_lookback_days": None}
        ).json()
        assert cleared["sync_lookback_days"] is None
        assert client.post(f"/api/email-accounts/{bounded['id']}/sync").status_code == 200
        assert provider.since_dates[-1] is None

        invalid = create_account(client, email_address="bounds@qq.com", sync_lookback_days=0)
        assert invalid.status_code == 422


def test_real_apscheduler_lifecycle_starts_and_stops(tmp_path):
    app = create_app(
        f"sqlite:///{tmp_path / 'scheduler.db'}",
        create_tables=True,
        credential_store=FakeCredentialStore(),
        llm_completion=lambda **kwargs: None,
        email_provider_factory=lambda account, secret: FakeProvider(),
        notification_adapter=FakeNotifier(),
        enable_scheduler=True,
    )
    scheduler = app.state.local_scheduler
    assert scheduler._scheduler is None
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert scheduler._scheduler is not None
        assert scheduler._scheduler.running is True
    assert scheduler._scheduler is None


@pytest.mark.parametrize(
    "payload",
    [
        {"email_address": "not-an-email", "provider_type": "qq"},
        {
            "email_address": "user@example.com",
            "provider_type": "custom",
            "imap_host": "https://mail.example.com",
            "imap_port": 993,
        },
        {"email_address": "user@example.com", "provider_type": "unknown"},
    ],
)
def test_email_account_validation(tmp_path, payload):
    app, _ = make_client(tmp_path, lambda account, secret: FakeProvider(), lambda **kwargs: None)
    with TestClient(app) as client:
        assert client.post("/api/email-accounts", json=payload).status_code == 422


def test_email_message_detail_returns_full_body_and_event_link(tmp_path):
    def completion(**kwargs):
        return {
            "choices": [
                {"message": {"content": json.dumps(classification_payload(), ensure_ascii=False)}}
            ]
        }

    app, _ = make_client(tmp_path, lambda account, secret: FakeProvider([]), completion)
    with TestClient(app) as client:
        configure_llm(client)
        account = create_account(client).json()
        manual = client.post(
            "/api/email-messages/manual",
            json={
                "account_id": account["id"],
                "sender": "hr@example.com",
                "subject": "面试邀请",
                "body_text": "请确认面试时间。" * 100,
                "message_id": "<detail-1@example.com>",
            },
        ).json()
        assert client.post(f"/api/email-accounts/{account['id']}/sync").json()["events_created"] == 1
        event = client.get("/api/recruitment-events").json()[0]

        detail = client.get(f"/api/email-messages/{manual['id']}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["subject"] == "面试邀请"
        assert body["body_text"] == "请确认面试时间。" * 100
        assert body["event_id"] == event["id"]
        assert body["body_preview"] is not None and len(body["body_preview"]) <= 500

        assert client.get("/api/email-messages/999999").status_code == 404


def test_delete_email_removes_event_and_notifications_but_not_account(tmp_path):
    def completion(**kwargs):
        return {
            "choices": [
                {"message": {"content": json.dumps(classification_payload(), ensure_ascii=False)}}
            ]
        }

    app, _ = make_client(tmp_path, lambda account, secret: FakeProvider([]), completion)
    with TestClient(app) as client:
        configure_llm(client)
        account = create_account(client).json()
        manual = client.post(
            "/api/email-messages/manual",
            json={
                "account_id": account["id"],
                "sender": "hr@example.com",
                "subject": "面试邀请",
                "body_text": "请确认面试时间。",
                "message_id": "<delete-1@example.com>",
            },
        ).json()
        assert client.post(f"/api/email-accounts/{account['id']}/sync").json()["events_created"] == 1
        event = client.get("/api/recruitment-events").json()[0]
        sent = client.post(f"/api/recruitment-events/{event['id']}/notify")
        assert sent.status_code == 200
        assert client.get("/api/notifications").json()

        removed = client.delete(f"/api/email-messages/{manual['id']}")
        assert removed.status_code == 200
        result = removed.json()
        assert result["deleted"] is True
        assert result["event_id"] == event["id"]
        assert result["notifications_deleted"] >= 1

        assert client.get(f"/api/email-messages/{manual['id']}").status_code == 404
        assert client.get("/api/recruitment-events").json() == []
        assert client.get("/api/notifications").json() == []
        assert client.delete(f"/api/email-messages/{manual['id']}").status_code == 404
        accounts = client.get("/api/email-accounts").json()
        assert [item["id"] for item in accounts] == [account["id"]]


def test_email_sync_uses_pinned_email_model_over_global_routing(tmp_path):
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return {
            "choices": [
                {"message": {"content": json.dumps(classification_payload(), ensure_ascii=False)}}
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
        }

    provider = FakeProvider([])
    app, _ = make_client(tmp_path, lambda account, secret: provider, completion)
    with TestClient(app) as client:
        base = {
            "provider": "openai",
            "api_base": "https://api.example.test/v1/",
            "temperature": 0,
            "timeout_seconds": 20,
        }
        general = client.post("/api/llm/configs", json={**base, "display_name": "全局", "model": "general-model"}).json()
        pinned = client.post("/api/llm/configs", json={**base, "display_name": "邮件专用", "model": "email-model"}).json()
        routing = client.put(
            "/api/llm/routing",
            json={"auto_select_enabled": True, "email_config_id": pinned["id"]},
        ).json()
        assert routing["email_config_id"] == pinned["id"]

        account = create_account(client).json()
        manual = client.post(
            "/api/email-messages/manual",
            json={
                "account_id": account["id"],
                "sender": "hr@example.com",
                "subject": "面试邀请",
                "body_text": "请确认面试时间。",
                "message_id": "<pin-1@example.com>",
            },
        ).json()
        synced = client.post(f"/api/email-accounts/{account['id']}/sync").json()
        assert synced["events_created"] == 1
        assert calls and all(call["model"] == "openai/email-model" for call in calls)

        counts = {c["id"]: c for c in client.get("/api/llm/configs").json()}
        assert counts[pinned["id"]]["call_count"] == 1
        assert counts[general["id"]]["call_count"] == 0
        assert counts[pinned["id"]]["total_prompt_tokens"] == 120
        assert counts[pinned["id"]]["total_completion_tokens"] == 30
        assert counts[general["id"]]["total_prompt_tokens"] == 0


def test_email_sync_falls_back_to_global_routing_when_pin_disabled(tmp_path):
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return {
            "choices": [
                {"message": {"content": json.dumps(classification_payload(), ensure_ascii=False)}}
            ]
        }

    provider = FakeProvider([])
    app, _ = make_client(tmp_path, lambda account, secret: provider, completion)
    with TestClient(app) as client:
        base = {
            "provider": "openai",
            "api_base": "https://api.example.test/v1/",
            "temperature": 0,
            "timeout_seconds": 20,
        }
        general = client.post("/api/llm/configs", json={**base, "display_name": "全局", "model": "general-model"}).json()
        pinned = client.post("/api/llm/configs", json={**base, "display_name": "邮件专用", "model": "email-model"}).json()
        assert client.put("/api/llm/routing", json={"auto_select_enabled": True, "email_config_id": pinned["id"]}).status_code == 200

        # Pause the pinned model directly in the database (the API refuses to
        # pin a disabled config, but a model can be paused after being pinned).
        from app.database.models import LLMConfiguration
        with app.state.session_factory() as db:
            db.get(LLMConfiguration, pinned["id"]).enabled = False
            db.commit()

        account = create_account(client).json()
        client.post(
            "/api/email-messages/manual",
            json={
                "account_id": account["id"],
                "sender": "hr@example.com",
                "subject": "面试邀请",
                "body_text": "请确认面试时间。",
                "message_id": "<pin-2@example.com>",
            },
        )
        synced = client.post(f"/api/email-accounts/{account['id']}/sync").json()
        assert synced["events_created"] == 1
        assert calls and all(call["model"] == "openai/general-model" for call in calls)


def test_email_sync_survives_paused_default_model(tmp_path):
    """Pausing the fixed default via the API must fall back, not fail sync."""
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return {
            "choices": [
                {"message": {"content": json.dumps(classification_payload(), ensure_ascii=False)}}
            ]
        }

    provider = FakeProvider([])
    app, _ = make_client(tmp_path, lambda account, secret: provider, completion)
    with TestClient(app) as client:
        base = {
            "provider": "openai",
            "api_base": "https://api.example.test/v1/",
            "temperature": 0,
            "timeout_seconds": 20,
        }
        default = client.post("/api/llm/configs", json={**base, "display_name": "默认", "model": "default-model"}).json()
        backup = client.post("/api/llm/configs", json={**base, "display_name": "备用", "model": "backup-model"}).json()
        saved = client.put(
            "/api/llm/routing",
            json={"auto_select_enabled": False, "default_config_id": default["id"]},
        ).json()
        assert saved["default_config_id"] == default["id"]

        # Same sequence a user can trigger from the settings page: pause the
        # default model after it was pinned as the fixed default.
        paused = client.put(f"/api/llm/configs/{default['id']}", json={"enabled": False})
        assert paused.status_code == 200

        account = create_account(client).json()
        client.post(
            "/api/email-messages/manual",
            json={
                "account_id": account["id"],
                "sender": "hr@example.com",
                "subject": "面试邀请",
                "body_text": "请确认面试时间。",
                "message_id": "<paused-default-1@example.com>",
            },
        )
        synced = client.post(f"/api/email-accounts/{account['id']}/sync").json()
        assert synced["events_created"] == 1
        assert calls and all(call["model"] == "openai/backup-model" for call in calls)
