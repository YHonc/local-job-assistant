from __future__ import annotations

import re
from enum import Enum


class JobCategory(str, Enum):
    DATA_ANALYSIS = "数据分析"
    OPERATIONS = "运营"
    PRODUCT = "产品"
    AI_ALGORITHM = "AI/算法"
    TECHNOLOGY = "技术研发"
    MARKETING_SALES = "市场/销售"
    FINANCE = "财务/金融"
    CONSULTING_STRATEGY = "咨询/战略"
    SUPPLY_CHAIN_MANUFACTURING = "供应链/制造"
    CORPORATE_SUPPORT = "职能/支持"
    OTHER = "其他"


JOB_CATEGORY_VALUES = tuple(category.value for category in JobCategory)

# Rules are intentionally deterministic: every write path can classify a job
# without making another model call. More specific role families come first so
# titles such as “数据产品经理” or “产品运营” do not fall into a generic bucket.
_CATEGORY_RULES: tuple[tuple[JobCategory, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        JobCategory.PRODUCT,
        (
            "产品经理",
            "产品助理",
            "产品专员",
            "产品实习",
            "产品策划",
            "product manager",
            "product owner",
            "product intern",
            "product specialist",
        ),
        ("pm",),
    ),
    (
        JobCategory.OPERATIONS,
        (
            "运营",
            "用户增长",
            "增长策略",
            "内容编辑",
            "内容策划",
            "新媒体",
            "社群",
            "电商",
            "operations",
            "operation",
            "growth",
        ),
        (),
    ),
    (
        JobCategory.DATA_ANALYSIS,
        (
            "数据分析",
            "商业分析",
            "业务分析",
            "经营分析",
            "行业分析",
            "用户分析",
            "市场分析",
            "统计分析",
            "策略分析",
            "分析师",
            "数据科学",
            "数据挖掘",
            "商业智能",
            "data analyst",
            "data scientist",
            "business analyst",
            "business intelligence",
            "analytics",
            "bi analyst",
        ),
        ("bi",),
    ),
    (
        JobCategory.AI_ALGORITHM,
        (
            "算法",
            "人工智能",
            "机器学习",
            "深度学习",
            "大模型",
            "自然语言",
            "计算机视觉",
            "推荐系统",
            "搜索推荐",
            "智能体",
            "aigc",
            "machine learning",
            "deep learning",
            "computer vision",
            "natural language",
            "ai engineer",
            "ai research",
        ),
        ("ai", "ml", "nlp", "llm", "agent"),
    ),
    (
        JobCategory.FINANCE,
        (
            "财务",
            "会计",
            "审计",
            "金融",
            "银行",
            "证券",
            "保险",
            "投资",
            "投行",
            "投研",
            "风控",
            "精算",
            "税务",
            "资产管理",
            "finance",
            "financial",
            "accounting",
            "audit",
            "banking",
            "investment",
            "risk control",
        ),
        (),
    ),
    (
        JobCategory.SUPPLY_CHAIN_MANUFACTURING,
        (
            "供应链",
            "采购",
            "物流",
            "生产",
            "制造",
            "工艺",
            "质量",
            "品质",
            "仓储",
            "材料",
            "供应商",
            "supply chain",
            "procurement",
            "logistics",
            "manufacturing",
            "quality",
        ),
        (),
    ),
    (
        JobCategory.MARKETING_SALES,
        (
            "销售",
            "市场营销",
            "市场推广",
            "营销",
            "商务",
            "客户经理",
            "渠道",
            "品牌",
            "公关",
            "广告",
            "account manager",
            "business development",
            "sales",
            "marketing",
        ),
        ("bd",),
    ),
    (
        JobCategory.CONSULTING_STRATEGY,
        (
            "咨询",
            "战略",
            "行业研究",
            "管理培训生",
            "管培生",
            "consultant",
            "consulting",
            "strategy",
            "research analyst",
        ),
        (),
    ),
    (
        JobCategory.CORPORATE_SUPPORT,
        (
            "人力资源",
            "人事",
            "招聘专员",
            "招聘经理",
            "招聘顾问",
            "招聘实习",
            "招聘岗",
            "人才招聘",
            "行政",
            "法务",
            "合规",
            "客服",
            "客户服务",
            "秘书",
            "翻译",
            "技术支持",
            "human resources",
            "customer service",
            "legal",
            "compliance",
        ),
        ("hr",),
    ),
    (
        JobCategory.TECHNOLOGY,
        (
            "研发",
            "开发",
            "工程师",
            "程序员",
            "软件",
            "后端",
            "前端",
            "客户端",
            "全栈",
            "测试",
            "运维",
            "数据库",
            "数据工程",
            "数据开发",
            "架构师",
            "信息技术",
            "网络安全",
            "安全工程",
            "嵌入式",
            "硬件",
            "云计算",
            "developer",
            "engineer",
            "backend",
            "frontend",
            "full stack",
            "software",
            "devops",
        ),
        ("etl", "qa", "sre"),
    ),
)


def _normalized(*values: str | None) -> str:
    return " ".join(" ".join((value or "").casefold().split()) for value in values if value)


def _matches(text: str, phrases: tuple[str, ...], tokens: tuple[str, ...]) -> bool:
    if any(phrase.casefold() in text for phrase in phrases):
        return True
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(token.casefold())}(?![a-z0-9])", text)
        for token in tokens
    )


def _classify(text: str) -> JobCategory | None:
    for category, phrases, tokens in _CATEGORY_RULES:
        if _matches(text, phrases, tokens):
            return category
    return None


def infer_job_category(
    title: str,
    description: str | None = None,
    original_title: str | None = None,
) -> JobCategory:
    """Infer one stable category, preferring title evidence over the full JD."""
    category = _classify(_normalized(title, original_title))
    if category is not None:
        return category
    return _classify(_normalized(description)) or JobCategory.OTHER


def resolve_job_category(
    value: JobCategory | str | None,
    *,
    title: str,
    description: str | None = None,
    original_title: str | None = None,
) -> str:
    """Keep an explicit valid category; otherwise run deterministic inference."""
    if value is not None and str(value).strip():
        if isinstance(value, JobCategory):
            return value.value
        return JobCategory(str(value).strip()).value
    return infer_job_category(title, description, original_title).value
