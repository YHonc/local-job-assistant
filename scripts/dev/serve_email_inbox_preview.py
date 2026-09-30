"""Serve a real, isolated email inbox preview with fake provider/model I/O.

This helper intentionally creates a temporary SQLite database and an app from
``create_app``.  It never reads a real mailbox, keyring, model endpoint, or
the formal database.  The browser can therefore exercise the same FastAPI
routes and response models used by ``/events`` and ``/settings``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database.models import (
    Company,
    EmailAccount,
    EmailMessage,
    LLMConfiguration,
    LLMRoutingSettings,
    Job,
    Setting,
)
from app.email_sync.provider import EmailEnvelope
from app.llm.service import LLMError
from app.main import create_app

class FakeCredentialStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, credential_id: str) -> str | None:
        return self.values.get(credential_id)

    def set(self, credential_id: str, secret: str) -> None:
        self.values[credential_id] = secret

    def delete(self, credential_id: str) -> None:
        self.values.pop(credential_id, None)


class FakeProvider:
    def __init__(self, envelopes: list[EmailEnvelope]) -> None:
        self.envelopes = envelopes

    def test_connection(self) -> None:
        return None

    def fetch_since(self, last_uid: int, **_kwargs):
        return [envelope for envelope in self.envelopes if envelope.uid > last_uid]


class FakeNotifier:
    def send(self, _target_url: str, _title: str, _body: str) -> bool:
        return True


def job_payload(company: str, title: str, *, link: str | None, summary: str) -> dict:
    return {
        "company": company,
        "job": title,
        "work_location": "厦门",
        "links": [link] if link else [],
        "event_type": "INTERVIEW",
        "event_time": "2026-09-20T14:00:00+08:00",
        "deadline": "2026-09-19T20:00:00+08:00",
        "action_required": True,
        "actions": ["回复 HR"],
        "summary": summary,
    }


def build_preview_app(db_path: Path):
    store = FakeCredentialStore()
    provider = FakeProvider(
        [
            EmailEnvelope(
                uid=1,
                message_id="<preview-1@example.test>",
                sender="hr@example.test",
                subject="面试邀请 · 数据分析实习",
                received_at=datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc),
                body_text="请确认周五上午的面试时间。",
            ),
            EmailEnvelope(
                uid=2,
                message_id="<preview-2@example.test>",
                sender="campus@example.test",
                subject="校招岗位更新",
                received_at=datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc),
                body_text="新的数据产品岗位已发布。",
            ),
            EmailEnvelope(
                uid=3,
                message_id="<preview-3@example.test>",
                sender="recruit@example.test",
                subject="岗位信息需要确认",
                received_at=datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc),
                body_text="请补充岗位地点。",
            ),
            EmailEnvelope(
                uid=4,
                message_id="<preview-4@example.test>",
                sender="broken@example.test",
                subject="邮件解析失败",
                received_at=datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc),
                body_text="解析稍后重试。",
            ),
            EmailEnvelope(
                uid=5,
                message_id="<preview-5@example.test>",
                sender="marketing@example.test",
                subject="本周优惠活动",
                received_at=datetime(2026, 9, 13, 9, 0, tzinfo=timezone.utc),
                body_text="商业广告内容。",
            ),
        ]
    )
    outputs = [
        {
            "is_recruitment": True,
            "body": "清洗后的面试正文。",
            "summary": "示例科技邀请参加数据分析实习生面试。",
            "jobs": [job_payload("示例科技", "数据分析实习生", link="https://jobs.example.test/21", summary="请确认面试时间。")],
        },
        {
            "is_recruitment": True,
            "body": "清洗后的岗位更新正文。",
            "summary": "新公司发布数据产品实习岗位。",
            "jobs": [job_payload("新公司", "数据产品实习生", link="https://jobs.example.test/22", summary="岗位已开放申请。")],
        },
        {
            "is_recruitment": True,
            "body": "清洗后的待确认正文。",
            "summary": "待确认公司提供分析岗位，候选岗位存在冲突。",
            "jobs": [job_payload("待确认公司", "分析岗位", link=None, summary="请人工确认对应岗位。")],
        },
        LLMError("preview model failure"),
    ]
    failed_once = False

    def completion(**_kwargs):
        nonlocal failed_once
        prompt = " ".join(
            str(message.get("content", ""))
            for message in _kwargs.get("messages", [])
            if isinstance(message, dict)
        )
        if "解析稍后重试" in prompt and not failed_once:
            failed_once = True
            raise LLMError("preview model failure")
        if "数据产品" in prompt:
            outcome = outputs[1]
        elif "岗位信息需要确认" in prompt or "请补充岗位地点" in prompt:
            outcome = outputs[2]
        elif "解析稍后重试" in prompt:
            outcome = {
                "is_recruitment": True,
                "body": "重试后的清洗正文。",
                "summary": "邮件解析已恢复，可继续处理。",
                "jobs": [job_payload("修复公司", "待重试岗位", link=None, summary="重试结果。")],
            }
        else:
            outcome = outputs[0]
        return {"choices": [{"message": {"content": json.dumps(outcome, ensure_ascii=False)}}]}

    app = create_app(
        f"sqlite:///{db_path}",
        create_tables=True,
        enable_scheduler=False,
        credential_store=store,
        llm_completion=completion,
        email_provider_factory=lambda _account, _secret: provider,
        notification_adapter=FakeNotifier(),
    )
    with app.state.session_factory() as db:
        company = Company(name="示例科技", aliases=[])
        db.add(company)
        db.flush()
        existing_job = Job(
            company_id=company.id,
            title="数据分析实习生",
            location="厦门",
            source_url="https://jobs.example.test/21",
            source_website="预览数据",
        )
        db.add(existing_job)
        db.flush()
        from app.database.models import Application

        db.add(Application(job_id=existing_job.id, status="已投递"))

        ambiguous_company = Company(name="待确认公司", aliases=[])
        db.add(ambiguous_company)
        db.flush()
        db.add_all(
            [
                Job(company_id=ambiguous_company.id, title="分析岗位", location="厦门"),
                Job(company_id=ambiguous_company.id, title="分析岗位", location="厦门"),
            ]
        )
        config = LLMConfiguration(
            display_name="隔离预览模型",
            provider="ollama",
            model="email-preview",
            api_base="http://127.0.0.1:1",
            enabled=True,
        )
        db.add(config)
        db.flush()
        db.add(LLMRoutingSettings(id=1, default_config_id=config.id, max_parallel_calls=1))
        account = EmailAccount(
            email_address="preview@qq.com",
            provider_type="qq",
            imap_host="imap.preview.test",
            imap_port=993,
            use_ssl=True,
            username="preview@qq.com",
            enabled=True,
            check_interval_minutes=15,
            sync_lookback_days=None,
            uid_validity=0,
            last_uid=0,
            credential_id="preview-mail-code",
        )
        db.add(account)
        db.add(Setting(key="email_regex", value="面试|校招|岗位|邮件解析"))
        db.add(Setting(key="email_keywords", value=json.dumps(["招聘"], ensure_ascii=False)))
        db.commit()
        store.set("preview-mail-code", "fake-preview-code")
        account_id = account.id

    # Run one real service sync against the fake provider and fake completion.
    # This creates the same cleaning, entry, event, archive, and failure rows
    # that the production API serializes, while remaining fully isolated.
    with app.state.session_factory() as db:
        result = app.state.email_sync_coordinator.service.sync_account(db, db.get(EmailAccount, account_id))
        old_message = EmailMessage(
            email_account_id=account_id,
            message_id="<preview-old-unprocessed@example.test>",
            uid_validity=0,
            uid=99,
            sender="legacy@example.test",
            subject="旧邮件待处理入口",
            body_text="这封旧邮件尚未形成 cleaning 记录。",
            pre_filtered=True,
            llm_processed=False,
        )
        db.add(old_message)
        db.commit()
        app.state.preview_sync_result = result.model_dump()

    print(f"Email inbox preview DB: {db_path}", flush=True)
    print(f"Email inbox preview: http://127.0.0.1:18768/events", flush=True)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="serve an isolated real email inbox preview")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18768)
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="optional new database path; must be under the workspace .tmp directory and must not exist",
    )
    args = parser.parse_args(argv)
    if args.host != "127.0.0.1":
        parser.error("preview must bind to 127.0.0.1")
    import app.main as app_main

    original_origin_allowed = app_main.origin_allowed
    preview_origin = f"http://127.0.0.1:{args.port}"
    app_main.origin_allowed = lambda origin: origin == preview_origin or original_origin_allowed(origin)
    workspace_tmp = (PROJECT_ROOT / ".tmp").resolve()
    workspace_tmp.mkdir(parents=True, exist_ok=True)
    if args.db is None:
        run_dir = Path(mkdtemp(prefix="email-inbox-preview-", dir=workspace_tmp))
        db_path = run_dir / "email-inbox.db"
    else:
        db_path = args.db.resolve()
        if not db_path.is_relative_to(workspace_tmp):
            parser.error("--db must be inside the workspace .tmp directory")
        db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        parser.error(f"refusing to overwrite existing preview database: {db_path}")
    app = build_preview_app(db_path)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
