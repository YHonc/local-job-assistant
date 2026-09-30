"""add stable job categories and classify existing jobs"""

from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa


revision = "0010_job_category"
down_revision = "0009_llm_tokens"
branch_labels = None
depends_on = None


# Frozen migration-time copy of the initial classifier. Keeping this here makes
# a fresh historical upgrade independent of later application-rule changes.
_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "产品",
        (
            "产品经理", "产品助理", "产品专员", "产品实习", "产品策划",
            "product manager", "product owner", "product intern", "product specialist",
        ),
        ("pm",),
    ),
    (
        "运营",
        (
            "运营", "用户增长", "增长策略", "内容编辑", "内容策划", "新媒体",
            "社群", "电商", "operations", "operation", "growth",
        ),
        (),
    ),
    (
        "数据分析",
        (
            "数据分析", "商业分析", "业务分析", "经营分析", "行业分析", "用户分析",
            "市场分析", "统计分析", "策略分析", "分析师", "数据科学", "数据挖掘",
            "商业智能", "data analyst", "data scientist", "business analyst",
            "business intelligence", "analytics", "bi analyst",
        ),
        ("bi",),
    ),
    (
        "AI/算法",
        (
            "算法", "人工智能", "机器学习", "深度学习", "大模型", "自然语言",
            "计算机视觉", "推荐系统", "搜索推荐", "智能体", "aigc", "machine learning",
            "deep learning", "computer vision", "natural language", "ai engineer", "ai research",
        ),
        ("ai", "ml", "nlp", "llm", "agent"),
    ),
    (
        "财务/金融",
        (
            "财务", "会计", "审计", "金融", "银行", "证券", "保险", "投资", "投行",
            "投研", "风控", "精算", "税务", "资产管理", "finance", "financial",
            "accounting", "audit", "banking", "investment", "risk control",
        ),
        (),
    ),
    (
        "供应链/制造",
        (
            "供应链", "采购", "物流", "生产", "制造", "工艺", "质量", "品质", "仓储",
            "材料", "供应商", "supply chain", "procurement", "logistics", "manufacturing", "quality",
        ),
        (),
    ),
    (
        "市场/销售",
        (
            "销售", "市场营销", "市场推广", "营销", "商务", "客户经理", "渠道", "品牌",
            "公关", "广告", "account manager", "business development", "sales", "marketing",
        ),
        ("bd",),
    ),
    (
        "咨询/战略",
        (
            "咨询", "战略", "行业研究", "管理培训生", "管培生", "consultant",
            "consulting", "strategy", "research analyst",
        ),
        (),
    ),
    (
        "职能/支持",
        (
            "人力资源", "人事", "招聘专员", "招聘经理", "招聘顾问", "招聘实习", "招聘岗",
            "人才招聘", "行政", "法务", "合规", "客服", "客户服务",
            "秘书", "翻译", "技术支持", "human resources", "customer service", "legal", "compliance",
        ),
        ("hr",),
    ),
    (
        "技术研发",
        (
            "研发", "开发", "工程师", "程序员", "软件", "后端", "前端", "客户端", "全栈",
            "测试", "运维", "数据库", "数据工程", "数据开发", "架构师", "信息技术",
            "网络安全", "安全工程", "嵌入式", "硬件", "云计算", "developer", "engineer",
            "backend", "frontend", "full stack", "software", "devops",
        ),
        ("etl", "qa", "sre"),
    ),
)


def _normalized(*values: str | None) -> str:
    return " ".join(" ".join((value or "").casefold().split()) for value in values if value)


def _classify(text: str) -> str | None:
    for category, phrases, tokens in _RULES:
        if any(phrase.casefold() in text for phrase in phrases):
            return category
        if any(
            re.search(rf"(?<![a-z0-9]){re.escape(token.casefold())}(?![a-z0-9])", text)
            for token in tokens
        ):
            return category
    return None


def _infer(title: str, description: str | None, original_title: str | None) -> str:
    return (
        _classify(_normalized(title, original_title))
        or _classify(_normalized(description))
        or "其他"
    )


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {column["name"] for column in inspector.get_columns("jobs")}
    if "job_category" not in columns:
        with op.batch_alter_table("jobs") as batch:
            batch.add_column(
                sa.Column(
                    "job_category",
                    sa.String(length=50),
                    nullable=False,
                    server_default="其他",
                )
            )

    indexes = {index["name"] for index in sa.inspect(conn).get_indexes("jobs")}
    if "ix_jobs_job_category" not in indexes:
        op.create_index("ix_jobs_job_category", "jobs", ["job_category"], unique=False)

    rows = conn.execute(
        sa.text("SELECT id, title, description, original_title, job_category FROM jobs ORDER BY id")
    ).mappings().all()
    for row in rows:
        if row["job_category"] not in (None, "", "其他"):
            continue
        conn.execute(
            sa.text("UPDATE jobs SET job_category = :category WHERE id = :job_id"),
            {
                "category": _infer(row["title"], row["description"], row["original_title"]),
                "job_id": row["id"],
            },
        )


def downgrade():
    conn = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(conn).get_indexes("jobs")}
    if "ix_jobs_job_category" in indexes:
        op.drop_index("ix_jobs_job_category", table_name="jobs")
    columns = {column["name"] for column in sa.inspect(conn).get_columns("jobs")}
    if "job_category" in columns:
        with op.batch_alter_table("jobs") as batch:
            batch.drop_column("job_category")
