from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from app.database.models import (
    Application,
    Company,
    EmailAccount,
    EmailCleaningResult,
    EmailJobEntry,
    EmailMessage,
    Job,
    Notification,
)
from app.email_sync.schemas import EmailCleaningOutput
from app.main import create_app
from app.email_sync.progress_models import ApplicationStatusHistory, EmailProgressSuggestion


class FakeCredentialStore:
    def get(self, _credential_id):
        return None

    def set(self, _credential_id, _secret):
        raise AssertionError("tests must not write credentials")

    def delete(self, _credential_id):
        raise AssertionError("tests must not delete credentials")


class FakeNotificationAdapter:
    def __init__(self):
        self.calls = []

    def send(self, target_url, title, body):
        self.calls.append((target_url, title, body))
        return True


def _unexpected_external_call(*_args, **_kwargs):
    raise AssertionError("external email, model, and notification services are disabled in tests")


@pytest.fixture
def api(tmp_path):
    adapter = FakeNotificationAdapter()
    app = create_app(
        f"sqlite:///{(tmp_path / 'progress.sqlite3').as_posix()}",
        create_tables=True,
        credential_store=FakeCredentialStore(),
        llm_completion=_unexpected_external_call,
        email_provider_factory=_unexpected_external_call,
        notification_adapter=adapter,
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        yield client, adapter


def _session(client):
    return client.app.state.session_factory()


def _seed_job(client, *, name="云岚科技", aliases=None, title="数据分析实习生", location="厦门", status="已投递"):
    db = _session(client)
    company = Company(name=name, aliases=aliases or [])
    db.add(company)
    db.flush()
    job = Job(company_id=company.id, title=title, location=location, employment_type="实习")
    db.add(job)
    db.flush()
    application = Application(job_id=job.id, status=status)
    db.add(application)
    db.commit()
    result = (company.id, job.id, application.id)
    db.close()
    return result


def _seed_email(client, uid=1):
    db = _session(client)
    account = EmailAccount(
        email_address=f"fixture-{uid}@example.test",
        provider_type="custom",
        imap_host="imap.fixture.test",
        imap_port=993,
        use_ssl=True,
        username=f"fixture-{uid}@example.test",
        sync_lookback_days=30,
    )
    db.add(account)
    db.flush()
    message = EmailMessage(
        email_account_id=account.id,
        message_id=f"<fixture-{uid}@example.test>",
        uid_validity=77,
        uid=uid,
        sender="hr@example.test",
        subject="合成招聘邮件",
        body_text="仅用于隔离验收的合成邮件正文",
        pre_filtered=True,
        llm_processed=False,
    )
    db.add(message)
    db.commit()
    message_id = message.id
    db.close()
    return message_id


def _manual_facts(
    *,
    key,
    company="云岚科技",
    title="数据分析实习生",
    location="厦门",
    event_type="ASSESSMENT",
    evidence="请于周五前完成测评",
    event_round=None,
    event_time=None,
):
    return {
        "idempotency_key": key,
        "company_name": company,
        "job_title": title,
        "work_location": location,
        "event_type": event_type,
        "event_round": event_round,
        "event_time": event_time,
        "field_evidence": {"event": evidence},
        "summary": "用户核对后的合成事实",
    }


def _create_entry(client, email_id, payload):
    response = client.post(f"/api/email-messages/{email_id}/manual-entries", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _suggest(client, entry):
    response = client.post(
        f"/api/email-job-entries/{entry['id']}/progress-suggestions",
        json={"expected_entry_version": entry["version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _accept_body(suggestion, application_version=1, key="accept-once"):
    return {
        "expected_suggestion_version": suggestion["version"],
        "expected_application_version": application_version,
        "idempotency_key": key,
    }


def test_manual_entry_edit_reclean_alias_and_confirm_are_isolated(api, monkeypatch):
    client, adapter = api
    company_id, job_id, application_id = _seed_job(
        client, aliases=["云岚数据"], status="已投递"
    )
    email_id = _seed_email(client)
    created = _create_entry(
        client,
        email_id,
        _manual_facts(key="manual-1", company="云岚数据"),
    )
    assert created["facts_source"] == "manual"
    assert created["cleaning_result_id"] is None

    replay = client.post(
        f"/api/email-messages/{email_id}/manual-entries",
        json=_manual_facts(key="manual-1", company="云岚数据"),
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == created["id"]
    conflict = client.post(
        f"/api/email-messages/{email_id}/manual-entries",
        json=_manual_facts(key="manual-1", company="云岚数据", evidence="改过的内容"),
    )
    assert conflict.status_code == 409

    edited = client.post(
        f"/api/email-job-entries/{created['id']}/manual-facts",
        json={
            "expected_entry_version": created["version"],
            **{key: value for key, value in _manual_facts(key="unused", company="云岚数据").items() if key != "idempotency_key"},
            "summary": "人工二次核对",
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == created["version"] + 1

    # A fake, deterministic reprocess exercises the real email pipeline without
    # a model call; its AI snapshot must leave the independent manual row intact.
    fake_output = EmailCleaningOutput(
        is_recruitment=True,
        company="另一家公司",
        job="另一岗位",
        event_type="ASSESSMENT",
        field_evidence={"event": "请完成测评"},
        summary="模型生成的另一条合成事实",
    )
    monkeypatch.setattr(
        client.app.state.email_sync_coordinator.service,
        "_classify_message",
        lambda _db, _prompt: fake_output,
    )
    reprocessed = client.post(f"/api/email-messages/{email_id}/reprocess")
    assert reprocessed.status_code == 200, reprocessed.text
    legacy_entries = client.get("/api/email-job-entries").json()
    manual_after = next(item for item in legacy_entries if item["id"] == created["id"])
    assert manual_after["facts_source"] == "manual"
    assert manual_after["company_name"] == "云岚数据"
    assert manual_after["job_title"] == "数据分析实习生"
    assert manual_after["summary"] == "人工二次核对"
    assert manual_after["manually_associated"] is False

    associated = client.patch(
        f"/api/email-job-entries/{created['id']}/association",
        json={"company_id": company_id, "job_id": job_id, "application_id": application_id},
    )
    assert associated.status_code == 200, associated.text
    assert associated.json()["manually_associated"] is True
    edited_entry = client.get("/api/email-job-entries").json()
    current = next(item for item in edited_entry if item["id"] == created["id"])
    suggestion = _suggest(client, current)
    assert suggestion["target_status"] == "测评"
    assert suggestion["application_id"] == application_id
    assert suggestion["status"] == "pending"

    db = _session(client)
    before = db.get(Application, application_id)
    assert before.status == "已投递"
    assert db.scalar(select(func.count()).select_from(ApplicationStatusHistory)) == 0
    notification_count = db.scalar(select(func.count()).select_from(Notification))
    db.close()
    adapter_calls = list(adapter.calls)

    accepted = client.post(
        f"/api/progress-suggestions/{suggestion['id']}/accept",
        json=_accept_body(suggestion),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    replay_accept = client.post(
        f"/api/progress-suggestions/{suggestion['id']}/accept",
        json=_accept_body(suggestion),
    )
    assert replay_accept.status_code == 200
    assert replay_accept.json()["status"] == "accepted"
    different_key = client.post(
        f"/api/progress-suggestions/{suggestion['id']}/accept",
        json=_accept_body(suggestion, key="different-key"),
    )
    assert different_key.status_code == 409

    db = _session(client)
    after = db.get(Application, application_id)
    assert after.status == "测评"
    assert after.version == 2
    assert db.scalar(select(func.count()).select_from(ApplicationStatusHistory)) == 1
    assert db.scalar(select(func.count()).select_from(EmailProgressSuggestion)) == 1
    assert db.scalar(select(func.count()).select_from(Notification)) == notification_count
    db.close()
    assert adapter.calls == adapter_calls


@pytest.mark.parametrize(
    "event_type,event_round,evidence",
    [
        ("ASSESSMENT", None, "测评资料下载"),
        ("WRITTEN_TEST", None, "笔试练习题"),
        ("OFFER", None, "Offer攻略"),
        ("OFFER", None, "尚未录用"),
        ("REJECTED", None, "不是拒信"),
        ("REJECTED", None, "未通过测试环境"),
        ("ASSESSMENT", None, "请参加公司宣讲会"),
        ("ASSESSMENT", None, "请完成资料填写"),
        ("WRITTEN_TEST", None, "请完成测评"),
        ("INTERVIEW", "二面", "诚邀参加一面"),
        ("INTERVIEW", "二面", "二面时间待定，一面面试通知"),
        ("INTERVIEW", None, "邀请参加公司宣讲会"),
    ],
)
def test_weak_negated_or_mismatched_evidence_stays_review_only(api, event_type, event_round, evidence):
    client, _adapter = api
    email_id = _seed_email(client)
    payload = _manual_facts(
        key=f"weak-{event_type}-{event_round}-{abs(hash(evidence))}",
        event_type=event_type,
        event_round=event_round,
        evidence=evidence,
    )
    entry = _create_entry(client, email_id, payload)
    suggestion = _suggest(client, entry)
    assert suggestion["target_status"] is None
    assert suggestion["status"] == "needs_review"


def test_exact_company_job_location_duplicate_and_wrong_manual_association_are_reviewed(api):
    client, _adapter = api
    company_id, job_id, application_id = _seed_job(
        client, aliases=["云岚数据"], status="已投递"
    )
    db = _session(client)
    duplicate_job = Job(
        company_id=company_id,
        title="数据分析实习生",
        location="厦门",
        employment_type="实习",
    )
    wrong_job = Job(
        company_id=company_id,
        title="产品分析师",
        location="厦门",
        employment_type="实习",
    )
    db.add_all([duplicate_job, wrong_job])
    db.flush()
    alias_company = Company(name="岚叶科技", aliases=["共同别名"])
    db.add(alias_company)
    db.flush()
    alias_job = Job(
        company_id=alias_company.id,
        title="数据分析实习生",
        location="厦门",
        employment_type="实习",
    )
    alias_company_b = Company(name="云杉科技", aliases=["共同别名"])
    db.add(alias_company_b)
    db.flush()
    alias_job_b = Job(
        company_id=alias_company_b.id,
        title="数据分析实习生",
        location="厦门",
        employment_type="实习",
    )
    db.add_all([alias_job, alias_job_b])
    db.flush()
    duplicate_application = Application(job_id=duplicate_job.id, status="已投递")
    wrong_application = Application(job_id=wrong_job.id, status="已投递")
    alias_application = Application(job_id=alias_job.id, status="已投递")
    db.add_all([duplicate_application, wrong_application, alias_application])
    db.commit()
    duplicate_job_id = duplicate_job.id
    duplicate_application_id = duplicate_application.id
    wrong_job_id, wrong_application_id = wrong_job.id, wrong_application.id
    db.close()

    # An explicit human selection of the canonical job accepts the alias in
    # the entry facts and resolves to that exact application.
    email_id = _seed_email(client, uid=1)
    alias_entry = _create_entry(
        client,
        email_id,
        _manual_facts(key="alias-manual", company="云岚数据"),
    )
    association = client.patch(
        f"/api/email-job-entries/{alias_entry['id']}/association",
        json={"company_id": company_id, "job_id": job_id, "application_id": application_id},
    )
    assert association.status_code == 200
    alias_suggestion = _suggest(client, association.json())
    assert alias_suggestion["status"] == "pending"
    assert alias_suggestion["application_id"] == application_id

    cases = [
        ("same-company-other-job", "云岚科技", "运营专员", "厦门", False, None, None),
        ("location-conflict", "云岚科技", "数据分析实习生", "泉州", False, None, None),
        ("duplicate-candidate", "云岚科技", "数据分析实习生", "厦门", False, None, None),
        ("duplicate-company-alias", "共同别名", "数据分析实习生", "厦门", False, None, None),
        # The user-selected application belongs to a different job than the
        # manually entered facts; manual selection cannot waive fact checks.
        ("wrong-manual-link", "云岚科技", "数据分析实习生", "厦门", True, wrong_job_id, wrong_application_id),
    ]
    for offset, (key, company, title, location, manual, selected_job, selected_app) in enumerate(cases, start=2):
        email_id = _seed_email(client, uid=offset)
        entry = _create_entry(
            client,
            email_id,
            _manual_facts(key=key, company=company, title=title, location=location),
        )
        if manual:
            response = client.patch(
                f"/api/email-job-entries/{entry['id']}/association",
                json={
                    "company_id": company_id,
                    "job_id": selected_job,
                    "application_id": selected_app,
                },
            )
            assert response.status_code == 200
            entry = response.json()
        suggestion = _suggest(client, entry)
        assert suggestion["status"] == "needs_review", key
        assert suggestion["application_id"] is None, key
        assert suggestion["target_status"] == "测评", key
        if key == "wrong-manual-link":
            assert "不一致" in suggestion["reason"] or "不匹配" in suggestion["reason"]
    assert duplicate_job_id != job_id
    assert duplicate_application_id != application_id


@pytest.mark.parametrize(
    "event_type,evidence,target",
    [
        ("OFFER", "恭喜您，我们正式录用您", "Offer"),
        ("REJECTED", "很遗憾，您未通过本次面试", "拒绝"),
    ],
)
def test_explicit_offer_and_rejection_results_can_generate_suggestions(api, event_type, evidence, target):
    client, _adapter = api
    _seed_job(client, status="已投递")
    email_id = _seed_email(client)
    entry = _create_entry(
        client,
        email_id,
        _manual_facts(
            key=f"explicit-{event_type}",
            event_type=event_type,
            evidence=evidence,
        ),
    )
    suggestion = _suggest(client, entry)
    assert suggestion["target_status"] == target
    assert suggestion["status"] == "pending"


@pytest.mark.parametrize("terminal", ["Offer", "拒绝", "放弃"])
def test_terminal_application_never_accepts_an_older_event(api, terminal):
    client, _adapter = api
    _seed_job(client, status=terminal)
    email_id = _seed_email(client)
    entry = _create_entry(client, email_id, _manual_facts(key=f"terminal-{terminal}"))
    suggestion = _suggest(client, entry)
    assert suggestion["target_status"] is None
    assert suggestion["status"] == "needs_review"


def test_older_duplicate_stage_and_reassociation_invalidate_suggestions(api):
    client, _adapter = api
    company_id, job_id, application_id = _seed_job(client, status="一面")
    email_id = _seed_email(client, uid=1)
    old_event = _manual_facts(
        key="old-receipt",
        event_type="APPLICATION_RECEIVED",
        evidence="已收到您的申请",
    )
    entry = _create_entry(client, email_id, old_event)
    suggestion = _suggest(client, entry)
    assert suggestion["target_status"] is None
    assert suggestion["status"] == "needs_review"

    db = _session(client)
    second_job = Job(
        company_id=company_id,
        title="数据分析实习生",
        location="厦门",
        employment_type="实习",
    )
    db.add(second_job)
    db.flush()
    second_application = Application(job_id=second_job.id, status="已投递")
    db.add(second_application)
    db.commit()
    second_job_id, second_application_id = second_job.id, second_application.id
    db.close()

    interview_email = _seed_email(client, uid=2)
    interview = _create_entry(
        client,
        interview_email,
        _manual_facts(
            key="interview-two",
            event_type="INTERVIEW",
            event_round="二面",
            evidence="一面已完成；现诚邀您参加二面面试",
        ),
    )
    selected = client.patch(
        f"/api/email-job-entries/{interview['id']}/association",
        json={"company_id": company_id, "job_id": job_id, "application_id": application_id},
    )
    assert selected.status_code == 200
    second_round_suggestion = _suggest(client, selected.json())
    assert second_round_suggestion["target_status"] == "二面"
    assert second_round_suggestion["status"] == "pending"

    changed = client.patch(
        f"/api/email-job-entries/{interview['id']}/association",
        json={
            "company_id": company_id,
            "job_id": second_job_id,
            "application_id": second_application_id,
        },
    )
    assert changed.status_code == 200
    stale = client.post(
        f"/api/progress-suggestions/{second_round_suggestion['id']}/accept",
        json=_accept_body(second_round_suggestion),
    )
    assert stale.status_code == 409


@pytest.mark.parametrize("change", ["entry", "cleaning", "application"])
def test_any_entry_cleaning_or_application_change_invalidates_accept(api, change):
    client, _adapter = api
    _company_id, _job_id, application_id = _seed_job(client)
    email_id = _seed_email(client)
    entry = _create_entry(client, email_id, _manual_facts(key=f"stale-{change}"))
    suggestion = _suggest(client, entry)

    db = _session(client)
    if change == "entry":
        update = client.post(
            f"/api/email-job-entries/{entry['id']}/manual-facts",
            json={
                "expected_entry_version": entry["version"],
                **{
                    key: value
                    for key, value in _manual_facts(key="unused").items()
                    if key != "idempotency_key"
                },
                "summary": "条目已编辑",
            },
        )
        assert update.status_code == 200
    elif change == "cleaning":
        db.add(
            EmailCleaningResult(
                email_message_id=email_id,
                status="completed",
                version=1,
                summary="新建的清洗快照",
            )
        )
        db.commit()
    else:
        application = db.get(Application, application_id)
        application.notes = "只改备注，不改阶段"
        db.commit()
    db.close()

    response = client.post(
        f"/api/progress-suggestions/{suggestion['id']}/accept",
        json=_accept_body(suggestion),
    )
    assert response.status_code == 409, response.text


def test_sqlite_immediate_transaction_serializes_two_session_accepts(api):
    client, adapter = api
    _seed_job(client)
    email_id = _seed_email(client)
    entry = _create_entry(client, email_id, _manual_facts(key="race-entry"))
    suggestion = _suggest(client, entry)
    statements = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.upper())

    event.listen(client.app.state.engine, "before_cursor_execute", record_statement)
    payload = _accept_body(suggestion, key="same-race-key")
    barrier = Barrier(2)

    def accept_in_request():
        with TestClient(client.app) as parallel_client:
            barrier.wait(timeout=5)
            return parallel_client.post(
                f"/api/progress-suggestions/{suggestion['id']}/accept", json=payload
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _index: accept_in_request(), range(2)))
    finally:
        event.remove(client.app.state.engine, "before_cursor_execute", record_statement)

    assert [response.status_code for response in responses] == [200, 200]
    assert all(response.json()["status"] == "accepted" for response in responses)
    assert any("BEGIN IMMEDIATE" in statement for statement in statements)
    db = _session(client)
    assert db.scalar(select(func.count()).select_from(ApplicationStatusHistory)) == 1
    assert db.get(Application, 1).status == "测评"
    db.close()
    assert adapter.calls == []
