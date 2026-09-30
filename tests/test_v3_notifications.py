from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database.models import Notification, NotificationPolicy, RecruitmentEvent
from app.main import create_app
from app.notifications.service import AppriseAdapter, ensure_policy


class FakeCredentialStore:
    def __init__(self):
        self.values = {}

    def get(self, credential_id):
        return self.values.get(credential_id)

    def set(self, credential_id, secret):
        self.values[credential_id] = secret

    def delete(self, credential_id):
        self.values.pop(credential_id, None)


class SequencedNotifier:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [True])
        self.calls = []

    def send(self, target_url, title, body):
        self.calls.append((target_url, title, body))
        return self.outcomes.pop(0) if self.outcomes else True


def notification_client(tmp_path, notifier=None):
    store = FakeCredentialStore()
    notifier = notifier or SequencedNotifier()
    app = create_app(
        f"sqlite:///{tmp_path / 'v3.db'}",
        create_tables=True,
        credential_store=store,
        notification_adapter=notifier,
    )
    return app, store, notifier


def add_event(app, **overrides):
    values = {
        "event_type": "INTERVIEW",
        "company_name": "腾讯",
        "job_title": "数据分析岗位",
        "event_time": datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc),
        "deadline": datetime(2026, 8, 24, 18, 0, tzinfo=timezone.utc),
        "action_required": True,
        "confidence": 0.95,
        "ai_summary": "面试邀请，请确认时间。",
        "association_status": "unmatched",
    }
    values.update(overrides)
    with app.state.session_factory() as db:
        event = RecruitmentEvent(**values)
        db.add(event)
        db.commit()
        db.refresh(event)
        return event.id


def test_multiple_policies_each_notify_with_separate_idempotency(tmp_path):
    app, _, notifier = notification_client(tmp_path)
    with TestClient(app) as client:
        event_id = add_event(app)
        first = client.post(f"/api/recruitment-events/{event_id}/notify").json()
        assert len(first) == 1

        created = client.post(
            "/api/notification-policies",
            json={
                "name": "第二策略",
                "desktop_enabled": True,
                "push_enabled": False,
                "important_event_types": ["INTERVIEW"],
                "notify_actionable_deadlines": True,
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["id"] != first[0].get("policy_id", 1)
        assert body["name"] == "第二策略"
        assert body["enabled"] is True

        second = client.post(f"/api/recruitment-events/{event_id}/notify").json()
        assert len(second) == 2
        assert {item["provider"] for item in second} == {"desktop"}
        assert second[1]["id"] != second[0]["id"]
        assert len(notifier.calls) == 2

        repeated = client.post(f"/api/recruitment-events/{event_id}/notify").json()
        assert {item["id"] for item in repeated} == {second[0]["id"], second[1]["id"]}
        assert len(notifier.calls) == 2


def test_policy_crud_with_per_policy_push_credential(tmp_path):
    app, store, _ = notification_client(tmp_path)
    with TestClient(app) as client:
        assert len(client.get("/api/notification-policies").json()) == 1
        created = client.post(
            "/api/notification-policies",
            json={
                "name": "Push 策略",
                "desktop_enabled": False,
                "push_enabled": True,
                "push_provider": "bark",
                "push_url": "bark://device-key-2",
                "important_event_types": ["OFFER"],
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["has_push_url"] is True
        assert "push_url" not in body
        assert "device-key-2" not in created.text
        assert store.values[f"notification:push:{body['id']}"] == "bark://device-key-2"
        assert "notification:push" not in store.values

        mismatch = client.put(
            f"/api/notification-policies/{body['id']}",
            json={
                "name": "改名",
                "desktop_enabled": False,
                "push_enabled": True,
                "push_provider": "ntfy",
                "push_url": "bark://still-bark",
                "important_event_types": ["OFFER"],
            },
        )
        assert mismatch.status_code == 422

        deleted = client.delete(f"/api/notification-policies/{body['id']}")
        assert deleted.status_code == 204
        assert f"notification:push:{body['id']}" not in store.values


def test_disabled_policy_does_not_notify(tmp_path):
    app, _, notifier = notification_client(tmp_path)
    with TestClient(app) as client:
        assert len(client.get("/api/notification-policies").json()) == 1
        updated = client.put(
            "/api/notification-policies/1",
            json={
                "name": "默认策略",
                "enabled": False,
                "desktop_enabled": True,
                "important_event_types": ["INTERVIEW"],
                "notify_actionable_deadlines": True,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["enabled"] is False

        event_id = add_event(app)
        result = client.post(f"/api/recruitment-events/{event_id}/notify")
        assert result.status_code == 200
        assert result.json() == []
        assert notifier.calls == []


def test_notification_policy_keeps_push_url_only_in_keyring(tmp_path):
    app, store, notifier = notification_client(tmp_path)
    with TestClient(app) as client:
        initial = client.get("/api/notification-policy").json()
        assert initial["configured"] is False
        assert initial["desktop_enabled"] is True
        assert initial["has_push_url"] is False

        response = client.put(
            "/api/notification-policy",
            json={
                "desktop_enabled": True,
                "push_enabled": True,
                "push_provider": "ntfy",
                "push_url": "ntfys://token-value@ntfy.example.test/jobs",
                "important_event_types": ["INTERVIEW", "OFFER"],
                "notify_actionable_deadlines": True,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["has_push_url"] is True
        assert "push_url" not in body
        assert "token-value" not in response.text
        assert store.values["notification:push"] == "ntfys://token-value@ntfy.example.test/jobs"

        with app.state.session_factory() as db:
            policy = db.get(NotificationPolicy, 1)
            assert policy.push_credential_id == "notification:push"
            assert "token-value" not in str(policy.__dict__)

        tested = client.post("/api/notifications/test", json={"provider": "push"})
        assert tested.status_code == 200
        assert tested.json()["status"] == "sent"
        assert notifier.calls[0][0] == "ntfys://token-value@ntfy.example.test/jobs"


def test_ntfy_and_bark_urls_are_provider_checked(tmp_path):
    app, _, _ = notification_client(tmp_path)
    with TestClient(app) as client:
        mismatch = client.put(
            "/api/notification-policy",
            json={
                "desktop_enabled": False,
                "push_enabled": True,
                "push_provider": "ntfy",
                "push_url": "bark://device-key",
                "important_event_types": ["INTERVIEW"],
            },
        )
        assert mismatch.status_code == 422
        bark = client.put(
            "/api/notification-policy",
            json={
                "desktop_enabled": False,
                "push_enabled": True,
                "push_provider": "bark",
                "push_url": "bark://device-key",
                "important_event_types": ["INTERVIEW"],
            },
        )
        assert bark.status_code == 200


def test_important_event_notification_is_idempotent_and_low_priority_is_quiet(tmp_path):
    app, _, notifier = notification_client(tmp_path)
    with TestClient(app) as client:
        important_id = add_event(app)
        first = client.post(f"/api/recruitment-events/{important_id}/notify")
        assert first.status_code == 200
        assert len(first.json()) == 1
        assert first.json()[0]["provider"] == "desktop"
        assert first.json()[0]["status"] == "sent"
        assert len(notifier.calls) == 1

        repeated = client.post(f"/api/recruitment-events/{important_id}/notify")
        assert repeated.status_code == 200
        assert repeated.json()[0]["id"] == first.json()[0]["id"]
        assert len(notifier.calls) == 1

        low_id = add_event(
            app,
            event_type="APPLICATION_RECEIVED",
            deadline=None,
            action_required=False,
        )
        low = client.post(f"/api/recruitment-events/{low_id}/notify")
        assert low.status_code == 200
        assert low.json() == []
        assert len(notifier.calls) == 1
        assert len(client.get("/api/notifications").json()) == 1


def test_failed_notification_has_redacted_history_and_manual_retry(tmp_path):
    notifier = SequencedNotifier([False, True])
    app, _, _ = notification_client(tmp_path, notifier)
    with TestClient(app) as client:
        event_id = add_event(app)
        failed = client.post(f"/api/recruitment-events/{event_id}/notify").json()[0]
        assert failed["status"] == "failed"
        assert failed["success"] is False
        assert failed["error"] == "notification provider failure"
        assert failed["attempt_count"] == 1
        assert failed["next_retry_at"] is not None

        retried = client.post(f"/api/notifications/{failed['id']}/retry")
        assert retried.status_code == 200
        assert retried.json()["status"] == "sent"
        assert retried.json()["attempt_count"] == 2
        assert retried.json()["next_retry_at"] is None
        history = client.get("/api/notifications?status=sent").json()
        assert [item["id"] for item in history] == [failed["id"]]


def test_event_notification_does_not_mutate_application_status(tmp_path):
    app, _, _ = notification_client(tmp_path)
    with TestClient(app) as client:
        company = client.post("/api/companies", json={"name": "腾讯"}).json()
        job = client.post(
            "/api/jobs",
            json={"company_id": company["id"], "title": "数据分析岗位"},
        ).json()
        application = client.post(
            "/api/applications",
            json={"job_id": job["id"], "status": "已投递"},
        ).json()
        event_id = add_event(
            app,
            company_id=company["id"],
            job_id=job["id"],
            application_id=application["id"],
            suggested_application_status="一面",
            association_status="confirmed",
        )
        response = client.post(f"/api/recruitment-events/{event_id}/notify")
        assert response.status_code == 200
        assert client.get(f"/api/applications/{application['id']}").json()["status"] == "已投递"


def test_scheduler_recovers_stale_sending_notification(tmp_path):
    app, _, notifier = notification_client(tmp_path)
    with TestClient(app):
        event_id = add_event(app)
        with app.state.session_factory() as db:
            ensure_policy(db)
            record = Notification(
                event_id=event_id,
                provider="desktop",
                title="stale",
                content="recover me",
                status="sending",
                success=False,
                attempt_count=1,
                idempotency_key="stale:test",
                delivery_token="expired-delivery",
                sending_until=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
            db.add(record)
            db.commit()
        with app.state.session_factory() as db:
            assert app.state.notification_service.retry_due(db) == 1
        with app.state.session_factory() as db:
            record = db.scalar(select(Notification).where(Notification.idempotency_key == "stale:test"))
            assert record.status == "sent"
            assert record.attempt_count == 2
        assert notifier.calls[-1][0] == "windows://"


def test_active_delivery_lease_is_not_sent_twice(tmp_path):
    app, _, notifier = notification_client(tmp_path)
    with TestClient(app):
        event_id = add_event(app)
        with app.state.session_factory() as db:
            ensure_policy(db)
            record = Notification(
                event_id=event_id,
                provider="desktop",
                title="active",
                content="do not duplicate",
                status="sending",
                success=False,
                attempt_count=1,
                idempotency_key="active:test",
                delivery_token="active-delivery",
                sending_until=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
            db.add(record)
            db.commit()
        with app.state.session_factory() as db:
            assert app.state.notification_service.retry_due(db) == 0
        assert notifier.calls == []


def test_real_apprise_adapter_parses_desktop_and_ntfy_without_network(monkeypatch):
    import apprise

    calls = []

    def fake_notify(self, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(apprise.Apprise, "notify", fake_notify)
    adapter = AppriseAdapter()
    assert adapter.send("windows://", "desktop title", "desktop body") is True
    assert adapter.send("ntfy://localhost/local-job-assistant-test", "push title", "push body") is True
    assert [item["title"] for item in calls] == ["desktop title", "push title"]
