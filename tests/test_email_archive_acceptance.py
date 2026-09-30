"""Independent acceptance coverage for the versioned email pipeline.

These cases deliberately use only temporary SQLite databases and fake
provider/model/notification dependencies.  They are kept separate from the
historical V2 tests so the legacy event API can be checked alongside the new
cleaning/job-entry/archive contract.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select
import pytest

from app.database.models import (
    Application,
    Company,
    EmailArchive,
    EmailCleaningResult,
    EmailJobEntry,
    EmailMessage,
    Job,
    Notification,
    RecruitmentEvent,
)
from app.email_sync.provider import EmailEnvelope
from app.main import create_app


class FakeCredentialStore:
    def __init__(self):
        self.values = {}

    def get(self, credential_id):
        return self.values.get(credential_id)

    def set(self, credential_id, secret):
        self.values[credential_id] = secret

    def delete(self, credential_id):
        self.values.pop(credential_id, None)


class FakeProvider:
    def __init__(self, envelopes=None):
        self.envelopes = list(envelopes or [])
        self.last_uid_calls = []

    def test_connection(self):
        return None

    def fetch_since(self, last_uid, **kwargs):
        self.last_uid_calls.append(last_uid)
        return [item for item in self.envelopes if item.uid > last_uid]


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def send(self, target_url, title, body):
        self.calls.append((target_url, title, body))
        return True


def make_envelope(uid, subject="面试邀请", body="请参加面试", *, message_id=None):
    return EmailEnvelope(
        uid=uid,
        message_id=message_id or f"<acceptance-{uid}@example.test>",
        sender="hr@example.test",
        subject=subject,
        received_at=datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc),
        body_text=body,
    )


def job_entry(
    company,
    title,
    *,
    event_type="INTERVIEW",
    location="厦门",
    link="https://jobs.example.test/role",
    summary="请确认招聘安排。",
):
    return {
        "company": company,
        "job": title,
        "work_location": location,
        "links": [link] if link else [],
        "event_type": event_type,
        "event_time": "2026-09-20T14:00:00+08:00",
        "action_required": True,
        "summary": summary,
    }


def cleaning_payload(entries, *, body="清洗后的邮件正文", summary="清洗摘要"):
    return {
        "is_recruitment": True,
        "body": body,
        "summary": summary,
        "jobs": entries,
    }


def make_client(tmp_path, envelopes, outputs):
    store = FakeCredentialStore()
    provider = FakeProvider(envelopes)
    notifier = FakeNotifier()
    calls = []
    output_queue = list(outputs)

    def completion(**kwargs):
        calls.append(kwargs)
        outcome = output_queue[min(len(calls) - 1, len(output_queue) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(outcome, ensure_ascii=False),
                    }
                }
            ]
        }

    app = create_app(
        f"sqlite:///{tmp_path / 'email-acceptance.db'}",
        create_tables=True,
        enable_scheduler=False,
        credential_store=store,
        llm_completion=completion,
        email_provider_factory=lambda account, secret: provider,
        notification_adapter=notifier,
    )
    return app, provider, notifier, calls


def configure_account(client):
    configured = client.put(
        "/api/llm/config",
        json={
            "provider": "ollama",
            "model": "acceptance-model",
            "api_base": "http://127.0.0.1:11434",
            "temperature": 0,
            "timeout_seconds": 20,
        },
    )
    assert configured.status_code == 200, configured.text
    account = client.post(
        "/api/email-accounts",
        json={
            "email_address": "acceptance@qq.com",
            "provider_type": "qq",
            "authorization_code": "fake-mail-code",
        },
    )
    assert account.status_code == 201, account.text
    return account.json()["id"]


def counts(app):
    with app.state.session_factory() as db:
        return {
            "companies": db.scalar(select(func.count()).select_from(Company)),
            "jobs": db.scalar(select(func.count()).select_from(Job)),
            "applications": db.scalar(select(func.count()).select_from(Application)),
            "events": db.scalar(select(func.count()).select_from(RecruitmentEvent)),
            "notifications": db.scalar(select(func.count()).select_from(Notification)),
        }


def test_regex_preview_then_cleaning_is_persisted_and_displayed(tmp_path):
    payload = cleaning_payload(
        [job_entry("示例科技", "数据分析实习生", link="https://jobs.example.test/analyst")]
    )
    app, _, _, calls = make_client(
        tmp_path,
        [
            make_envelope(1, body="面试安排：请确认时间"),
            make_envelope(2, subject="市场优惠", body="本周折扣"),
        ],
        [payload],
    )
    with TestClient(app) as client:
        saved = client.put(
            "/api/email-filter",
            json={"keywords": ["面试"], "regex": "面试|测评"},
        )
        assert saved.status_code == 200, saved.text

        preview = client.post(
            "/api/email-filter/preview",
            json={
                "subject": "面试邀请",
                "sender": "hr@example.test",
                "body_text": "面试安排：请确认时间",
            },
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["regex_match"] is True

        account_id = configure_account(client)
        synced = client.post(f"/api/email-accounts/{account_id}/sync")
        assert synced.status_code == 200, synced.text
        assert synced.json()["skipped_by_regex"] == 1
        assert len(calls) == 1

        messages = client.get("/api/email-messages").json()
        assert len(messages) == 2
        candidate = next(item for item in messages if item["uid"] == 1)
        skipped = next(item for item in messages if item["uid"] == 2)
        assert candidate["cleaning_status"] == "completed"
        assert candidate["cleaning_result_id"] is not None
        assert candidate["summary"] == "清洗摘要"
        assert candidate["company_name"] == "示例科技"
        assert candidate["job_title"] == "数据分析实习生"
        assert candidate["archive_status"] == "archived"
        assert candidate["job_entries"]
        assert skipped["cleaning_status"] in {"not_needed", "pending", None}
        assert skipped["body_preview"] is None

        detail = client.get(f"/api/email-messages/{candidate['id']}")
        assert detail.status_code == 200, detail.text
        detail_body = detail.json()
        assert detail_body["cleaning_result_id"] == candidate["cleaning_result_id"]
        assert detail_body["cleaning_status"] == "completed"
        assert detail_body["job_entries"][0]["company_name"] == "示例科技"
        assert detail_body["job_entries"][0]["job_title"] == "数据分析实习生"
        with app.state.session_factory() as db:
            result = db.get(EmailCleaningResult, candidate["cleaning_result_id"])
            assert result.cleaned_body == "清洗后的邮件正文"


def test_two_positions_and_events_create_two_legacy_events_and_notifications(tmp_path):
    payload = cleaning_payload(
        [
            job_entry(
                "甲公司",
                "数据分析实习生",
                event_type="INTERVIEW",
                link="https://jobs.example.test/a",
            ),
            job_entry(
                "乙公司",
                "运营实习生",
                event_type="OFFER",
                link="https://jobs.example.test/b",
            ),
        ]
    )
    app, _, notifier, calls = make_client(
        tmp_path,
        [make_envelope(1, body="两个岗位分别有面试和录用通知")],
        [payload],
    )
    with TestClient(app) as client:
        account_id = configure_account(client)
        synced = client.post(f"/api/email-accounts/{account_id}/sync")
        assert synced.status_code == 200, synced.text
        assert len(calls) == 1

        detail = client.get("/api/email-messages").json()[0]
        assert synced.json()["events_created"] == 2
        assert len(detail["job_entries"]) == 2
        assert {entry["event_type"] for entry in detail["job_entries"]} == {
            "INTERVIEW",
            "OFFER",
        }
        with app.state.session_factory() as db:
            entries = db.scalars(select(EmailJobEntry).order_by(EmailJobEntry.id)).all()
            events = db.scalars(
                select(RecruitmentEvent).where(
                    RecruitmentEvent.source_email_id == detail["id"]
                )
            ).all()
        assert len(entries) == 2
        assert len(events) == 2
        assert {entry.recruitment_event_id for entry in entries} == {
            event.id for event in events
        }
        assert len(notifier.calls) == 2


def test_cross_email_same_link_is_one_job_and_application_is_favorite(tmp_path):
    payload = cleaning_payload(
        [job_entry("同一公司", "数据分析实习生", link="https://jobs.example.test/same")]
    )
    app, _, _, _ = make_client(
        tmp_path,
        [
            make_envelope(1, body="第一封"),
            make_envelope(2, body="第二封"),
        ],
        [payload, payload],
    )
    with TestClient(app) as client:
        account_id = configure_account(client)
        synced = client.post(f"/api/email-accounts/{account_id}/sync")
        assert synced.status_code == 200, synced.text
        assert synced.json()["events_created"] == 2

    actual = counts(app)
    assert actual["companies"] == 1
    assert actual["jobs"] == 1
    assert actual["applications"] == 1
    with app.state.session_factory() as db:
        jobs = db.scalars(select(Job)).all()
        entries = db.scalars(select(EmailJobEntry).order_by(EmailJobEntry.id)).all()
        application = db.scalar(select(Application))
    assert application.status == "收藏"
    assert {entry.job_id for entry in entries} == {jobs[0].id}
    assert {entry.match_status for entry in entries} == {"auto_created", "auto_bound"}


def test_fuzzy_or_missing_identity_stays_pending_without_creating_job(tmp_path):
    payload = cleaning_payload(
        [
            job_entry("待确认公司", "数据分析实习"),
            {
                "company": None,
                "job": "运营实习生",
                "links": ["https://jobs.example.test/missing"],
                "event_type": "INTERVIEW",
                "summary": "缺少公司名称。",
            },
        ]
    )
    app, _, _, _ = make_client(
        tmp_path,
        [make_envelope(1, body="信息不完整")],
        [payload],
    )
    with TestClient(app) as client:
        existing_company = client.post("/api/companies", json={"name": "待确认公司"}).json()
        existing_job = client.post(
            "/api/jobs",
            json={
                "company_id": existing_company["id"],
                "title": "数据分析实习生",
            },
        ).json()
        existing_application = client.post(
            "/api/applications",
            json={"job_id": existing_job["id"], "status": "已投递"},
        ).json()
        account_id = configure_account(client)
        synced = client.post(f"/api/email-accounts/{account_id}/sync")
        assert synced.status_code == 200, synced.text
        detail = client.get("/api/email-messages").json()[0]
        assert {entry["match_status"] for entry in detail["job_entries"]} <= {
            "needs_review",
            "unmatched",
        }
        assert counts(app)["jobs"] == 1
        assert (
            client.get(f"/api/applications/{existing_application['id']}").json()["status"]
            == "已投递"
        )


def test_reprocess_keeps_manual_binding_and_event_id_and_archive_retry_does_not_call_ai(
    tmp_path,
):
    payload = cleaning_payload(
        [job_entry("人工公司", "人工岗位", link="https://jobs.example.test/manual")]
    )
    app, _, _, calls = make_client(
        tmp_path,
        [make_envelope(1)],
        [payload, payload],
    )
    with TestClient(app) as client:
        account_id = configure_account(client)
        synced = client.post(f"/api/email-accounts/{account_id}/sync")
        assert synced.status_code == 200, synced.text
        message = client.get("/api/email-messages").json()[0]
        entry_snapshot = message["job_entries"][0]
        cleaning_id = message["cleaning_result_id"]

        with app.state.session_factory() as db:
            entry = db.get(EmailJobEntry, entry_snapshot["id"])
            event_id = entry.recruitment_event_id
            application = db.get(Application, entry.application_id)
            entry.manually_associated = True
            entry.match_status = "manual"
            entry.match_reason = "人工指定"
            application.status = "已投递"
            db.commit()

        reprocessed = client.post(f"/api/email-cleaning/{cleaning_id}/reprocess")
        assert reprocessed.status_code == 200, reprocessed.text
        assert len(calls) == 2

        with app.state.session_factory() as db:
            result = db.get(EmailCleaningResult, cleaning_id)
            entry = db.get(EmailJobEntry, entry_snapshot["id"])
            assert result.version == 2
            assert entry.recruitment_event_id == event_id
            assert entry.manually_associated is True
            assert entry.match_status == "manual"
            assert db.get(Application, entry.application_id).status == "已投递"

        archived = client.post(f"/api/email-cleaning/{cleaning_id}/archive")
        assert archived.status_code == 200, archived.text
        assert archived.json()["status"] == "archived"
        assert len(calls) == 2
        with app.state.session_factory() as db:
            archive = db.scalar(
                select(EmailArchive).where(
                    EmailArchive.cleaning_result_id == cleaning_id
                )
            )
            archive.status = "failed"
            archive.error = "test archive failure"
            db.commit()
        retried = client.post(f"/api/email-cleaning/{cleaning_id}/archive")
        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] == "archived"
        assert len(calls) == 2


def test_reprocess_llm_failure_is_visible_and_retry_keeps_previous_facts(tmp_path):
    payload = cleaning_payload([job_entry("失败恢复公司", "失败恢复岗位")])
    app, _, _, calls = make_client(
        tmp_path,
        [make_envelope(1)],
        [payload, TimeoutError("provider timed out"), payload],
    )
    with TestClient(app) as client:
        account_id = configure_account(client)
        assert client.post(f"/api/email-accounts/{account_id}/sync").status_code == 200
        message = client.get("/api/email-messages").json()[0]
        cleaning_id = message["cleaning_result_id"]

        failed = client.post(f"/api/email-cleaning/{cleaning_id}/reprocess")
        assert failed.status_code == 504, failed.text
        visible = client.get("/api/email-messages").json()[0]
        assert visible["cleaning_status"] == "failed"
        assert visible["processing_error"] == "LLMTimeoutError"
        assert visible["summary"] == "清洗摘要"
        assert visible["company_name"] == "失败恢复公司"
        assert visible["job_title"] == "失败恢复岗位"

        retried = client.post(f"/api/email-cleaning/{cleaning_id}/retry")
        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] == "completed"
        assert len(calls) == 3
        recovered = client.get("/api/email-messages").json()[0]
        assert recovered["processing_error"] is None
        assert recovered["archive_status"] == "archived"


def test_delete_email_removes_all_derived_rows_but_keeps_auto_created_jobs(tmp_path):
    payload = cleaning_payload(
        [
            job_entry("删除公司", "删除岗位一", link="https://jobs.example.test/delete-a"),
            job_entry("删除公司", "删除岗位二", link="https://jobs.example.test/delete-b"),
        ]
    )
    app, _, _, _ = make_client(tmp_path, [make_envelope(1)], [payload])
    with TestClient(app) as client:
        account_id = configure_account(client)
        assert client.post(f"/api/email-accounts/{account_id}/sync").status_code == 200
        message_id = client.get("/api/email-messages").json()[0]["id"]
        before = counts(app)
        assert before["jobs"] == 2
        assert before["events"] == 2
        assert before["notifications"] == 2

        deleted = client.delete(f"/api/email-messages/{message_id}")
        assert deleted.status_code == 200, deleted.text
        after = counts(app)
        assert after["jobs"] == 2
        assert after["applications"] == 2
        assert after["events"] == 0
        assert after["notifications"] == 0
        with app.state.session_factory() as db:
            assert db.scalar(select(func.count()).select_from(EmailMessage)) == 0
            assert db.scalar(select(func.count()).select_from(EmailCleaningResult)) == 0
            assert db.scalar(select(func.count()).select_from(EmailJobEntry)) == 0
            assert db.scalar(select(func.count()).select_from(EmailArchive)) == 0


def test_existing_job_without_application_is_reused_by_automatic_archive(tmp_path):
    """A Job is a match target even when its one-to-one Application is absent."""
    payload = cleaning_payload(
        [job_entry("已有公司", "无申请岗位", link="https://jobs.example.test/existing")]
    )
    app, _, _, _ = make_client(tmp_path, [make_envelope(1)], [payload])
    with TestClient(app) as client:
        company = client.post("/api/companies", json={"name": "已有公司"})
        assert company.status_code == 201, company.text
        job = client.post(
            "/api/jobs",
            json={"company_id": company.json()["id"], "title": "无申请岗位"},
        )
        assert job.status_code == 201, job.text
        account_id = configure_account(client)

        synced = client.post(f"/api/email-accounts/{account_id}/sync")
        assert synced.status_code == 200, synced.text

    with app.state.session_factory() as db:
        entries = db.scalars(select(EmailJobEntry)).all()
        jobs = db.scalars(select(Job)).all()
        applications = db.scalars(select(Application)).all()

    assert len(entries) == 1
    assert len(jobs) == 1
    assert applications == []
    assert entries[0].job_id == job.json()["id"]
    assert entries[0].application_id is None
    assert entries[0].match_status == "auto_bound"


def test_failed_archive_retry_creates_job_without_second_ai_call(tmp_path, monkeypatch):
    """A failed archive is retryable from local state and never re-cleans the mail."""
    payload = cleaning_payload(
        [job_entry("归档失败公司", "重试建岗", link="https://jobs.example.test/retry")]
    )
    app, provider, _, calls = make_client(tmp_path, [make_envelope(1)], [payload])
    service = app.state.email_sync_coordinator.service
    original_matcher = service._match_job_entry
    state = {"fail_once": True}

    def fail_first_archive(db, entry):
        if state["fail_once"]:
            state["fail_once"] = False
            raise RuntimeError("injected archive write failure")
        return original_matcher(db, entry)

    monkeypatch.setattr(service, "_match_job_entry", fail_first_archive)
    with TestClient(app) as client:
        account_id = configure_account(client)
        first = client.post(f"/api/email-accounts/{account_id}/sync")
        assert first.status_code == 200, first.text
        assert len(calls) == 1

        with app.state.session_factory() as db:
            cleaning = db.scalar(select(EmailCleaningResult))
            archive = db.scalar(select(EmailArchive))
            assert cleaning is not None
            assert archive is not None
            assert archive.status == "failed"
            assert db.scalar(select(func.count()).select_from(Job)) == 0

        # The next sync has no new UID to fetch.  It must retry the failed
        # archive from the durable cleaning snapshot without invoking the LLM.
        monkeypatch.setattr(service, "_match_job_entry", original_matcher)
        second = client.post(f"/api/email-accounts/{account_id}/sync")
        assert second.status_code == 200, second.text
        assert second.json()["fetched"] == 0
        assert provider.last_uid_calls == [0, 1]
        assert len(calls) == 1

        with app.state.session_factory() as db:
            archive = db.scalar(select(EmailArchive))
            assert archive.status == "archived"
            assert db.scalar(select(func.count()).select_from(Job)) == 1
            assert db.scalar(select(func.count()).select_from(Application)) == 1


def test_regex_is_primary_and_preview_rejects_invalid_pattern(tmp_path):
    app, _, _, calls = make_client(tmp_path, [make_envelope(1, subject="专属编号", body="邀请")],
        [cleaning_payload([job_entry("规则公司", "规则岗位")])])
    with TestClient(app) as client:
        aid = configure_account(client)
        assert client.put('/api/email-filter', json={'keywords': [], 'regex': '专属编号'}).status_code == 200
        assert client.get('/api/email-filter').json()['keywords'] == []
        assert client.post('/api/email-filter/preview', json={'regex': '['}).status_code == 422
        assert client.post('/api/email-filter/preview', json={'regex': '', 'subject': '专属编号'}).json()['matched'] is False
        assert client.post(f'/api/email-accounts/{aid}/sync').json()['events_created'] == 1
        assert len(calls) == 1


def test_reprocess_new_position_runs_archive_and_hides_stale_entry(tmp_path):
    first = cleaning_payload([job_entry("版本公司", "原岗位", link=None)])
    second = cleaning_payload([job_entry("版本公司", "完全不同职位", link=None)])
    app, _, notifier, calls = make_client(tmp_path, [make_envelope(1)], [first, second, second])
    with TestClient(app) as client:
        aid = configure_account(client)
        client.post(f'/api/email-accounts/{aid}/sync')
        mail = client.get('/api/email-messages').json()[0]
        cid = mail['cleaning_result_id']
        assert client.post(f'/api/email-cleaning/{cid}/reprocess').status_code == 200
        detail = client.get(f"/api/email-messages/{mail['id']}").json()
        assert len(detail['job_entries']) == len(detail['events']) == 1
        assert detail['job_entries'][0]['job_title'] == '完全不同职位'
        assert detail['job_entries'][0]['match_status'] == 'auto_created'
        event_id = detail['event_ids'][0]
        with app.state.session_factory() as db:
            notification_ids = db.scalars(select(Notification.id).order_by(Notification.id)).all()
        assert client.post(f'/api/email-cleaning/{cid}/reprocess').status_code == 200
        assert client.get(f"/api/email-messages/{mail['id']}").json()['event_ids'] == [event_id]
        assert len(notifier.calls) == 2
        with app.state.session_factory() as db:
            assert db.scalars(select(Notification.id).order_by(Notification.id)).all() == notification_ids
        assert counts(app)['jobs'] == 2
        assert client.post(f'/api/email-cleaning/{cid}/retry').status_code == 200
        assert len(calls) == 3


def test_manual_api_and_legacy_dismissal_are_preserved(tmp_path):
    payload = cleaning_payload([job_entry('原公司', '原岗位')])
    app, _, _, _ = make_client(tmp_path, [make_envelope(1)], [payload])
    with TestClient(app) as client:
        aid = configure_account(client)
        client.post(f'/api/email-accounts/{aid}/sync')
        mail = client.get('/api/email-messages').json()[0]
        entry = mail['job_entries'][0]
        company = client.post('/api/companies', json={'name': '纠错公司'}).json()
        job = client.post('/api/jobs', json={'company_id': company['id'], 'title': '纠错岗位'}).json()
        application = client.post('/api/applications', json={'job_id': job['id'], 'status': '已投递'}).json()
        path = f"/api/email-job-entries/{entry['id']}/association"
        assert client.patch(path, json={'job_id': entry['job_id'], 'application_id': application['id']}).status_code == 422
        assert client.patch(path, json={'job_id': job['id']}).json()['application_id'] == application['id']
        cid = mail['cleaning_result_id']
        assert client.post(f'/api/email-cleaning/{cid}/reprocess').status_code == 200
        assert client.post(f"/api/email-job-entries/{entry['id']}/rematch").json()['job_id'] == job['id']
        assert client.get(f"/api/applications/{application['id']}").json()['status'] == '已投递'
        assert client.patch(f"/api/recruitment-events/{entry['recruitment_event_id']}/association", json={'dismiss': True}).status_code == 200
        assert client.post(f'/api/email-cleaning/{cid}/reprocess').status_code == 200
        updated = client.get('/api/email-messages').json()[0]['job_entries'][0]
        assert updated['job_id'] is None and updated['manually_associated']


def test_link_match_and_location_conflict_and_missing_dates(tmp_path):
    payload = cleaning_payload([
        job_entry('链接公司', '岗位新名称', link='https://jobs.example.test/42?utm_source=email'),
        job_entry('地点公司', '分析师', location='上海', link=None),
    ])
    app, _, _, _ = make_client(tmp_path, [make_envelope(1)], [payload])
    with TestClient(app) as client:
        for name, title, location, url in [('链接公司', '旧名称', '厦门', 'https://jobs.example.test/42'), ('地点公司', '分析师', '北京', None)]:
            company = client.post('/api/companies', json={'name': name}).json()
            client.post('/api/jobs', json={'company_id': company['id'], 'title': title, 'location': location, 'source_url': url})
        aid = configure_account(client)
        client.post(f'/api/email-accounts/{aid}/sync')
        entries = client.get('/api/email-messages').json()[0]['job_entries']
        assert [item['match_status'] for item in entries] == ['auto_bound', 'needs_review']
        assert entries[1]['candidates']
        assert all(job['published_at'] is None for job in client.get('/api/jobs').json())
        assert counts(app)['jobs'] == 2


def test_concurrent_archives_create_one_job(tmp_path):
    payload = cleaning_payload([job_entry('并发公司', '并发岗位', link=None)])
    app, _, _, _ = make_client(tmp_path, [make_envelope(1), make_envelope(2)], [payload])
    with TestClient(app) as client:
        aid = configure_account(client)
        service = app.state.email_sync_coordinator.service
        archive = service.archive_cleaning
        service.archive_cleaning = lambda *args: None
        client.post(f'/api/email-accounts/{aid}/sync')
        service.archive_cleaning = archive
        ids = [item['cleaning_result_id'] for item in client.get('/api/email-messages').json()]
        def run(cid):
            with app.state.session_factory() as db:
                return service.archive_cleaning(db, cid).status
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(run, ids)) == ['archived', 'archived']
        assert counts(app)['jobs'] == counts(app)['applications'] == 1


def test_busy_archive_lease_is_not_marked_failed(tmp_path):
    from datetime import timedelta
    from app.database.models import now
    from app.email_sync.archiving import ArchiveBusy
    app, _, _, _ = make_client(tmp_path, [make_envelope(1)], [cleaning_payload([job_entry('租约公司', '岗位')])])
    with TestClient(app) as client:
        aid = configure_account(client)
        client.post(f'/api/email-accounts/{aid}/sync')
        with app.state.session_factory() as db:
            record = db.scalar(select(EmailArchive))
            record.status = 'archiving'
            record.lease_until = now() + timedelta(minutes=5)
            db.commit()
            with pytest.raises(ArchiveBusy):
                app.state.email_sync_coordinator.service.archive_cleaning(db, record.cleaning_result_id)
            assert db.get(EmailArchive, record.id).status == 'archiving'
