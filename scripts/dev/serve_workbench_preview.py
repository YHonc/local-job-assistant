"""Serve an isolated Workbench preview with synthetic fixtures and fake I/O.

The preview binds only to loopback, uses a fresh SQLite database under .tmp,
and replaces every credential/provider boundary with in-memory fakes. It is
intended for local UI acceptance, not for production data or integrations.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# app.main creates a module-level app during import. Keep that incidental app
# away from the user's configured database and keyring as well.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"


class FakeCredentialStore:
    """Process-local synthetic secrets; values are never read from keyring."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, credential_id: str) -> str | None:
        return self.values.get(credential_id)

    def set(self, credential_id: str, secret: str) -> None:
        self.values[credential_id] = secret

    def delete(self, credential_id: str) -> None:
        self.values.pop(credential_id, None)


# create_app's module-level app normally attempts to construct a keyring
# adapter. Swap the constructor before importing app.main so even that unused
# in-memory app cannot touch the operating-system credential store.
import app.credentials as credential_module  # noqa: E402

credential_module.KeyringCredentialStore = FakeCredentialStore

from app.database.models import (  # noqa: E402
    Application,
    Company,
    EmailAccount,
    EmailArchive,
    EmailCleaningResult,
    EmailJobEntry,
    EmailMessage,
    Job,
    LLMConfiguration,
    LLMRoutingSettings,
    LLMUsageEvent,
    Notification,
    NotificationPolicy,
    RecruitmentEvent,
    Setting,
)
from app.email_sync.progress_models import EmailProgressSuggestion  # noqa: E402
from app.imports.draft_models import ImportDraft  # noqa: E402
from app.main import create_app  # noqa: E402


PREVIEW_ORIGIN = "http://127.0.0.1:18769"
FAKE_LLM_SECRET = "preview-only-synthetic-llm-secret"
FAKE_EMAIL_SECRET = "preview-only-synthetic-mail-code"
FAKE_PUSH_TARGET = "ntfy://preview-only.invalid/workbench-preview"


def _dt(day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


def _job_fields(
    *,
    company_id: int,
    title: str,
    created_at: datetime,
    location: str | None = "厦门",
    category: str = "数据分析",
    category_review_status: str = "confirmed",
    organize_status: str = "organized",
    tags: list[str] | None = None,
    source_url: str | None = None,
    source_website: str = "预览合成数据",
    description: str | None = "仅用于本地界面验收的合成岗位描述。",
) -> Job:
    return Job(
        company_id=company_id,
        title=title,
        location=location,
        employment_type="实习",
        job_category=category,
        category_review_status=category_review_status,
        organize_status=organize_status,
        tags=tags or [],
        salary_text="150-200 元/天（合成）",
        notes="preview fixture",
        description=description,
        source_url=source_url,
        source_website=source_website,
        original_title=title,
        published_at=date(2026, 9, 10),
        created_at=created_at,
        updated_at=created_at,
    )


class FakeEmailProvider:
    """A deterministic provider that never opens a socket or reads a mailbox."""

    def test_connection(self) -> None:
        return None

    def fetch_since(self, _last_uid: int, **_kwargs: Any) -> list[Any]:
        return []


class FakeNotifier:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def send(self, target_url: str, title: str, body: str) -> bool:
        self.calls.append({"target_url": target_url, "title": title, "body": body})
        return True


def _fake_completion(**kwargs: Any) -> dict[str, Any]:
    messages = kwargs.get("messages", [])
    user_prompt = "\n".join(
        str(item.get("content", ""))
        for item in messages
        if isinstance(item, dict) and item.get("role") == "user"
    )
    if 'status field is exactly "ok"' in user_prompt:
        content: dict[str, Any] = {"status": "ok"}
    elif "<untrusted_page_data>" in user_prompt:
        content = {
            "company_name": "预览合成公司",
            "title": "运营分析实习生",
            "location": "厦门",
            "employment_type": "实习",
            "description": "由本地 fake completion 返回的演示岗位事实。",
            "source_website": "预览合成数据",
            "published_at": "2026-09-18",
            "confidence": 0.96,
            "warnings": ["仅为隔离预览生成的合成建议"],
        }
    elif "<untrusted_email_data>" in user_prompt:
        source_text = user_prompt
        content = {
            "is_recruitment": True,
            "body": "合成邮件清洗正文：诚邀您参加数据分析实习生一面面试。",
            "summary": "隔离预览 fake completion 返回的邮件事实。",
            "confidence": 0.98,
            "jobs": [
                {
                    "company": "远岚科技",
                    "job": "数据分析实习生",
                    "work_location": "厦门",
                    "links": ["https://preview.example.test/jobs/1"],
                    "event_type": "INTERVIEW",
                    "event_round": "一面",
                    "field_evidence": {
                        "event": "诚邀您参加数据分析实习生一面面试。"
                    },
                    "summary": "合成的一面邀请。",
                }
            ],
        }
        del source_text
    else:
        content = {
            "is_recruitment": True,
            "body": "合成邮件事实。",
            "summary": "fake completion fallback",
            "confidence": 0.9,
            "jobs": [
                {
                    "company": "远岚科技",
                    "job": "数据分析实习生",
                    "work_location": "厦门",
                    "links": ["https://preview.example.test/jobs/1"],
                    "event_type": "OTHER",
                    "field_evidence": {"source": "synthetic preview fixture"},
                    "summary": "合成邮件事实。",
                }
            ],
        }
    return {
        "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 91, "completion_tokens": 37},
    }


class EmptyKeywordRejectOnce:
    """Return one deliberate 422 for a first empty-keyword save, without writes."""

    def __init__(self, app) -> None:
        self.app = app
        self.rejected = False

    async def __call__(self, scope, receive, send) -> None:
        if not (
            scope["type"] == "http"
            and scope.get("method") == "PUT"
            and scope.get("path") == "/api/email-filter"
        ):
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunks.append(message.get("body", b""))
            more_body = bool(message.get("more_body", False))
        body = b"".join(chunks)
        try:
            payload = json.loads(body or b"{}")
        except (TypeError, ValueError):
            payload = None

        if not self.rejected and isinstance(payload, dict) and payload.get("keywords") == []:
            self.rejected = True
            response = json.dumps(
                {
                    "detail": {
                        "code": "preview_empty_keywords_rejected",
                        "message": "预览专用：首次清空关键词保存返回 422；未修改 fixture。再次明确保存会走真实 API。",
                    }
                },
                ensure_ascii=False,
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 422,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(response)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": response})
            return

        delivered = False

        async def replay_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)


def seed_preview(app, store: FakeCredentialStore, notifier: FakeNotifier) -> None:
    """Write deterministic synthetic records into the fresh preview database."""
    with app.state.session_factory() as db:
        company_one = Company(
            id=1,
            name="远岚科技",
            aliases=["远岚", "Yuanlan Labs"],
            notes="合成 fixture 公司；别名供邮件匹配验收。",
        )
        company_two = Company(
            id=2,
            name="海岬数据",
            aliases=["海岬", "Cape Data"],
            notes="第二家合成 fixture 公司。",
        )
        db.add_all([company_one, company_two])
        db.flush()

        # 56 jobs (55 active + one archived), with the first active rows carrying
        # the acceptance cases. The active library therefore spans two pages.
        titles = {
            1: "数据分析实习生",
            2: "产品运营实习生",
            3: "商业分析实习生",
            4: "已归档岗位验收样例",
            5: "人工智能实习生",
        }
        jobs: dict[int, Job] = {}
        for job_id in range(1, 57):
            title = titles.get(job_id, f"合成岗位 {job_id:02d} · 数据运营实习生")
            company_id = 1 if job_id in {1, 3, 4} or job_id >= 6 and job_id % 2 else 2
            created_at = _dt(24, 8, 0) - timedelta(minutes=job_id)
            job = _job_fields(
                company_id=company_id,
                title=title,
                created_at=created_at,
                location=None if job_id == 3 else ("厦门" if job_id % 2 else "泉州"),
                category="AI/算法" if job_id == 5 else ("运营" if job_id == 2 else "数据分析"),
                category_review_status="pending" if job_id == 3 else "confirmed",
                organize_status="archived" if job_id == 4 else ("pending" if job_id == 3 else "organized"),
                tags=["预览", "数据", f"fixture-{job_id}"],
                source_url=f"https://preview.example.test/jobs/{job_id}",
            )
            job.id = job_id
            jobs[job_id] = job
        db.add_all(jobs.values())
        db.flush()

        application_one = Application(
            id=1,
            job_id=1,
            status="已投递",
            notes="合成申请记录；供进度建议验收。",
            status_updated_at=_dt(20, 2),
            version=1,
        )
        application_two = Application(
            id=2,
            job_id=5,
            status="测评",
            notes="第二条合成申请。",
            status_updated_at=_dt(19, 3),
            version=1,
        )
        db.add_all([application_one, application_two])
        db.flush()

        config = LLMConfiguration(
            id=1,
            display_name="预览隔离模型",
            provider="openai",
            model="preview-model-a",
            api_base="http://127.0.0.1:1/v1",
            temperature=0.2,
            timeout_seconds=15,
            credential_id="llm:preview-workbench",
            enabled=True,
            call_count=9,
            success_count=8,
            failure_count=1,
            total_duration_ms=4320,
            total_prompt_tokens=740,
            total_completion_tokens=321,
            last_used_at=_dt(23, 8),
            created_at=_dt(12),
            updated_at=_dt(23, 8),
        )
        db.add(config)
        db.flush()
        db.add(
            LLMRoutingSettings(
                id=1,
                auto_select_enabled=False,
                default_config_id=1,
                email_config_id=1,
                max_parallel_calls=1,
            )
        )
        for usage_id, day, success, prompt, completion in [
            (1, 19, True, 90, 36),
            (2, 19, True, 92, 34),
            (3, 20, True, 88, 39),
            (4, 20, False, 75, 0),
            (5, 21, True, 96, 42),
            (6, 22, True, 86, 31),
            (7, 23, True, 91, 37),
            (8, 23, True, 80, 29),
        ]:
            db.add(
                LLMUsageEvent(
                    id=usage_id,
                    config_id=1,
                    recorded_at=_dt(day, 10 + usage_id % 8),
                    success=success,
                    duration_ms=400 + usage_id * 17,
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                )
            )

        account = EmailAccount(
            id=1,
            email_address="preview-inbox@example.test",
            provider_type="custom",
            imap_host="mail.preview.invalid",
            imap_port=993,
            use_ssl=True,
            username="preview-inbox@example.test",
            enabled=True,
            check_interval_minutes=20,
            sync_lookback_days=30,
            uid_validity=77,
            last_uid=102,
            last_sync_status="success",
            credential_id="email:preview-workbench",
            created_at=_dt(18),
            updated_at=_dt(23),
        )
        db.add(account)
        db.flush()

        email_one = EmailMessage(
            id=1,
            email_account_id=1,
            message_id="<workbench-preview-1@example.test>",
            uid_validity=77,
            uid=101,
            sender="招聘组 <hr@preview.example.test>",
            subject="远岚科技：数据分析实习生一面邀请与岗位信息",
            received_at=_dt(23, 3),
            body_text=(
                "您好，感谢关注远岚科技。诚邀您参加数据分析实习生一面面试，"
                "请于 2026-09-25 10:00 在线参加。另有海岬数据产品运营实习岗位信息供参考。"
            ),
            pre_filtered=True,
            llm_processed=True,
            llm_classification="recruitment",
            llm_attempt_count=1,
            synced_at=_dt(23, 3, 1),
        )
        email_two = EmailMessage(
            id=2,
            email_account_id=1,
            message_id="<workbench-preview-2@example.test>",
            uid_validity=77,
            uid=102,
            sender="招聘运营 <ops@preview.example.test>",
            subject="（正文未保留）手工补充：海岬数据产品运营",
            received_at=_dt(24, 1),
            body_text=None,
            pre_filtered=True,
            llm_processed=False,
            llm_classification=None,
            llm_attempt_count=0,
            synced_at=_dt(24, 1, 1),
        )
        db.add_all([email_one, email_two])
        db.flush()

        cleaning = EmailCleaningResult(
            id=1,
            email_message_id=1,
            status="completed",
            version=1,
            cleaned_body=(
                "您好，感谢关注远岚科技。诚邀您参加数据分析实习生一面面试，"
                "请于 2026-09-25 10:00 在线参加。另有海岬数据产品运营实习岗位信息供参考。"
            ),
            summary="一封邮件包含一面邀请与第二条岗位信息。",
            company_name="远岚科技",
            job_title="数据分析实习生",
            work_location="厦门",
            links=[
                "https://preview.example.test/jobs/1",
                "https://preview.example.test/jobs/2",
            ],
            field_evidence={"event": "诚邀您参加数据分析实习生一面面试。"},
            event_time=_dt(25, 2),
            action_required=True,
            actions=["确认面试安排"],
            structured_data={"fixture": "synthetic-only", "entry_count": 2},
            attempt_count=1,
            ai_processed_at=_dt(23, 3, 2),
            created_at=_dt(23, 3, 2),
            updated_at=_dt(23, 3, 2),
        )
        db.add(cleaning)
        db.flush()

        entry_one = EmailJobEntry(
            id=1,
            identity_key="preview-entry-1-interview",
            cleaning_result_id=1,
            source_email_id=1,
            facts_source="ai",
            version=1,
            company_id=1,
            job_id=1,
            application_id=1,
            company_name="远岚科技",
            job_title="数据分析实习生",
            work_location="厦门",
            links=["https://preview.example.test/jobs/1"],
            field_evidence={"event": "诚邀您参加数据分析实习生一面面试。"},
            event_type="INTERVIEW",
            event_round="一面",
            event_time=_dt(25, 2),
            action_required=True,
            actions=["确认面试安排"],
            summary="合成的一面邀请；匹配到申请1。",
            match_status="matched",
            match_reason="synthetic exact company/title association",
            active=True,
            manually_associated=False,
            created_at=_dt(23, 3, 2),
            updated_at=_dt(23, 3, 2),
        )
        entry_two = EmailJobEntry(
            id=2,
            identity_key="preview-entry-2-manual-facts",
            cleaning_result_id=1,
            source_email_id=1,
            facts_source="manual",
            version=1,
            company_name="海岬数据",
            job_title="产品运营实习生",
            work_location="泉州",
            links=["https://preview.example.test/jobs/2"],
            field_evidence={"job": "邮件中提及海岬数据产品运营实习岗位。"},
            event_type="OTHER",
            action_required=False,
            actions=[],
            summary="保留为待人工关联的第二条岗位事实。",
            match_status="unmatched",
            match_reason="synthetic fixture: candidate needs human review",
            active=True,
            manually_associated=False,
            created_at=_dt(23, 3, 3),
            updated_at=_dt(23, 3, 3),
        )
        entry_three = EmailJobEntry(
            id=3,
            identity_key="preview-entry-3-manual-no-body",
            cleaning_result_id=None,
            source_email_id=2,
            facts_source="manual",
            version=1,
            company_id=2,
            job_id=5,
            application_id=2,
            company_name="海岬数据",
            job_title="人工智能实习生",
            work_location="厦门",
            links=["https://preview.example.test/jobs/5"],
            field_evidence={"manual_note": "用户基于邮件主题人工核对，正文未保留。"},
            event_type="OTHER",
            action_required=False,
            actions=[],
            summary="人工事实入口：邮件正文缺失，需尊重人工核对。",
            match_status="manual",
            match_reason="explicit synthetic manual association",
            active=True,
            manually_associated=True,
            created_at=_dt(24, 1, 2),
            updated_at=_dt(24, 1, 2),
        )
        db.add_all([entry_one, entry_two, entry_three])
        db.flush()

        event_one = RecruitmentEvent(
            id=1,
            company_id=1,
            job_id=1,
            application_id=1,
            suggested_application_id=1,
            source_email_id=1,
            email_job_entry_id=1,
            active=True,
            company_name="远岚科技",
            job_title="数据分析实习生",
            event_type="INTERVIEW",
            event_time=_dt(25, 2),
            location="线上",
            meeting_url="https://preview.example.test/meeting/1",
            action_required=True,
            confidence=0.98,
            ai_summary="合成一面邀请，供邮件事件与申请进度建议验收。",
            suggested_application_status="一面",
            association_status="confirmed",
            created_at=_dt(23, 3, 2),
            updated_at=_dt(23, 3, 2),
        )
        event_two = RecruitmentEvent(
            id=2,
            company_id=None,
            job_id=None,
            application_id=None,
            suggested_application_id=None,
            source_email_id=1,
            email_job_entry_id=2,
            active=True,
            company_name="海岬数据",
            job_title="产品运营实习生",
            event_type="OTHER",
            action_required=False,
            confidence=0.72,
            ai_summary="第二条合成岗位信息，当前未人工确认关联。",
            association_status="unmatched",
            created_at=_dt(23, 3, 3),
            updated_at=_dt(23, 3, 3),
        )
        db.add_all([event_one, event_two])
        db.flush()
        entry_one.recruitment_event_id = 1
        entry_two.recruitment_event_id = 2
        # Entries use SQLAlchemy optimistic versions. Flush event links before
        # capturing the suggestion snapshot so its entry_version is current.
        db.flush()

        db.add(
            EmailArchive(
                id=1,
                cleaning_result_id=1,
                status="archived",
                requested_at=_dt(23, 3, 3),
                archived_at=_dt(23, 3, 4),
                created_at=_dt(23, 3, 3),
                updated_at=_dt(23, 3, 4),
            )
        )

        from app.email_sync.progress import _get_or_create_suggestion

        suggestion = _get_or_create_suggestion(db, entry_one)
        suggestion.id = 1
        if suggestion.status != "pending" or suggestion.target_status != "一面":
            raise RuntimeError("preview progress suggestion fixture is not safely pending")

        # Draft ids 1 and 2 are stable browser-contract fixtures; 22 rows ensure
        # the pending queue has a second page with its normal page size.
        draft_one = ImportDraft(
            id=1,
            source_url="https://preview.example.test/drafts/1",
            source_website="预览合成数据",
            original_title="产品运营实习生（待补充）",
            captured_text="合成页面文本：海岬数据招聘产品运营实习生，地点泉州。",
            user_fields={"company_name": "海岬数据", "title": "产品运营实习生"},
            suggested_fields=None,
            status="draft",
            version=1,
            idempotency_key="preview-workbench-draft-1",
            created_at=_dt(24, 5),
            updated_at=_dt(24, 5),
        )
        draft_two = ImportDraft(
            id=2,
            source_url="https://preview.example.test/drafts/2",
            source_website="预览合成数据",
            original_title="AI 数据运营校招信息",
            captured_text="合成页面文本：远岚科技招募运营分析实习生，厦门，负责指标分析。",
            user_fields={"company_name": "远岚科技"},
            suggested_fields={
                "company_name": "远岚科技",
                "title": "运营分析实习生",
                "location": "厦门",
                "employment_type": "实习",
                "job_category": "数据分析",
                "description": "合成的运营指标分析工作内容。",
                "source_website": "预览合成数据",
                "published_at": "2026-09-18",
                "confidence": 0.96,
                "warnings": ["仅为隔离预览生成的合成建议"],
            },
            status="extracted",
            version=2,
            idempotency_key="preview-workbench-draft-2",
            created_at=_dt(24, 4),
            updated_at=_dt(24, 4),
        )
        db.add_all([draft_one, draft_two])
        for draft_id in range(3, 23):
            created_at = _dt(23, 18) - timedelta(minutes=draft_id)
            db.add(
                ImportDraft(
                    id=draft_id,
                    source_url=f"https://preview.example.test/drafts/{draft_id}",
                    source_website="预览合成数据",
                    original_title=f"合成待整理岗位 {draft_id:02d}",
                    captured_text=f"合成页面文本第 {draft_id} 条；仅用于分页验收。",
                    user_fields={},
                    status="draft",
                    version=1,
                    idempotency_key=f"preview-workbench-draft-{draft_id}",
                    created_at=created_at,
                    updated_at=created_at,
                )
            )

        db.add_all(
            [
                Setting(key="email_keywords", value=json.dumps(["招聘", "面试", "校招"], ensure_ascii=False)),
                Setting(key="email_regex", value="面试|校招|Offer"),
                Setting(key="theme_mode", value="system"),
                Setting(key="density", value="comfortable"),
                Setting(key="seed_color", value="#3659b8"),
                Setting(key="visible_job_fields", value=json.dumps(["category", "tags", "application", "organization", "location", "salary"], ensure_ascii=False)),
                Setting(key="dismissed_page_hints", value=json.dumps({"pending": False, "follow": False, "mail": False})),
                Setting(key="reduce_motion", value="false"),
                Setting(key="poetry_preset", value="random"),
            ]
        )

        policy = NotificationPolicy(
            id=1,
            name="预览通知策略",
            enabled=True,
            desktop_enabled=True,
            push_enabled=True,
            push_provider="ntfy",
            push_credential_id="notification:push",
            important_event_types=["INTERVIEW", "OFFER"],
            notify_actionable_deadlines=True,
            created_at=_dt(17),
            updated_at=_dt(23),
        )
        db.add(policy)
        db.flush()
        db.add_all(
            [
                Notification(
                    id=1,
                    event_id=1,
                    policy_id=1,
                    provider="desktop",
                    title="已送达 · 预览一面邀请",
                    content="合成通知历史；不代表真实外发。",
                    status="sent",
                    success=True,
                    attempt_count=1,
                    sent_at=_dt(23, 4),
                    idempotency_key="preview-workbench-notification-sent",
                    created_at=_dt(23, 4),
                    updated_at=_dt(23, 4),
                ),
                Notification(
                    id=2,
                    event_id=2,
                    policy_id=1,
                    provider="push",
                    title="待重试 · 合成岗位待人工关联",
                    content="仅用于点击重试后验证 fake notifier。",
                    status="failed",
                    success=False,
                    error="preview fixture: previous synthetic delivery failed",
                    attempt_count=1,
                    next_retry_at=_dt(22, 4),
                    idempotency_key="preview-workbench-notification-failed",
                    created_at=_dt(22, 4),
                    updated_at=_dt(22, 4),
                ),
            ]
        )
        db.commit()

    store.set("llm:preview-workbench", FAKE_LLM_SECRET)
    store.set("email:preview-workbench", FAKE_EMAIL_SECRET)
    store.set("notification:push", FAKE_PUSH_TARGET)
    app.state.preview_fixture_ids = {
        "companies": [1, 2],
        "jobs": "1-56 (55 active; 4 archived)",
        "applications": [1, 2],
        "drafts": "1-22",
        "email_account": 1,
        "emails": [1, 2],
        "cleaning": 1,
        "email_entries": [1, 2, 3],
        "recruitment_events": [1, 2],
        "progress_suggestion": suggestion.id,
        "llm_config": 1,
        "llm_routing": 1,
        "notification_policy": 1,
        "notifications": [1, 2],
    }
    app.state.preview_notifier = notifier


def build_preview_app(db_path: Path):
    store = FakeCredentialStore()
    notifier = FakeNotifier()
    app = create_app(
        database_url=f"sqlite:///{db_path.as_posix()}",
        create_tables=True,
        credential_store=store,
        llm_completion=_fake_completion,
        email_provider_factory=lambda _account, _secret: FakeEmailProvider(),
        notification_adapter=notifier,
        enable_scheduler=False,
    )

    import app.main as app_main
    import app.llm.routes as llm_routes

    # Only this process/app accepts the exact preview origin. The model-list
    # probe normally uses HTTPX; replace it with an in-process deterministic fake.
    app_main.origin_allowed = lambda origin: origin == PREVIEW_ORIGIN
    llm_routes._list_remote_models = lambda _provider, _api_base, _api_key, _timeout, proxy_url=None: [
        "preview-model-a"
    ]
    app.add_middleware(EmptyKeywordRejectOnce)
    seed_preview(app, store, notifier)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="serve an isolated Workbench UI preview with synthetic data"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18769)
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="optional new database path; must be under workspace .tmp and must not exist",
    )
    args = parser.parse_args(argv)
    if args.host != "127.0.0.1":
        parser.error("preview must bind only to 127.0.0.1")
    if args.port != 18769:
        parser.error("the preview origin is fixed to http://127.0.0.1:18769")

    workspace_tmp = (PROJECT_ROOT / ".tmp").resolve()
    workspace_tmp.mkdir(parents=True, exist_ok=True)
    if args.db is None:
        run_dir = Path(mkdtemp(prefix="workbench-preview-", dir=workspace_tmp))
        db_path = run_dir / "workbench-preview.db"
    else:
        db_path = args.db.resolve()
        if not db_path.is_relative_to(workspace_tmp):
            parser.error("--db must be inside the workspace .tmp directory")
        db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        parser.error(f"refusing to overwrite existing preview database: {db_path}")

    app = build_preview_app(db_path)
    print(f"Workbench preview DB: {db_path}", flush=True)
    print(f"Workbench preview: {PREVIEW_ORIGIN}/workbench", flush=True)
    print(f"Mail view: {PREVIEW_ORIGIN}/workbench?view=mail", flush=True)
    print(f"Fixture IDs: {json.dumps(app.state.preview_fixture_ids, ensure_ascii=False)}", flush=True)
    print("All external services are fake; server shutdown leaves only this .tmp fixture DB.", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=18769, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
