"""Deterministic quick filters; explicit direction tags override title inference."""
from sqlalchemy import case, func, or_, select

# Ordered rules are shared by display and SQL facets. No LLM or historical rewrite.
DIRECTIONS = (
    ("管培生", "职能/支持", ("管培", "培训生", "management trainee")),
    ("人力资源", "职能/支持", ("人力", "招聘专员", "hrbp")),
    ("行政", "职能/支持", ("行政", "综合管理")),
    ("运维", "技术研发", ("运维", "sre", "devops")),
    ("测试", "技术研发", ("测试", "qa engineer")),
    ("前端开发", "技术研发", ("前端", "frontend", "web开发")),
    ("后端开发", "技术研发", ("后端", "backend", "java", "软件开发", "服务端")),
    ("数据开发", "数据分析", ("数据开发", "数据工程", "数仓", "etl")),
    ("BI", "数据分析", ("商业智能", "bi分析", "bi analyst", "bi工程")),
    ("业务分析", "数据分析", ("业务分析", "商业分析", "经营分析", "数据分析", "business analyst")),
    ("用户运营", "运营", ("用户运营", "社群", "用户增长")),
    ("内容运营", "运营", ("内容", "新媒体", "编辑")),
    ("电商运营", "运营", ("电商", "淘宝", "跨境")),
    ("产品经理", "产品", ("产品经理", "产品助理", "product manager")),
    ("算法", "AI/算法", ("算法", "机器学习", "大模型", "算法工程")),
    ("销售", "市场/销售", ("销售", "客户经理", "sales")),
    ("市场", "市场/销售", ("市场", "品牌", "marketing")),
    ("财务", "财务/金融", ("财务", "会计", "审计")),
    ("供应链", "供应链/制造", ("供应链", "采购", "物流")),
)
VALUES = tuple(name for name, _, _ in DIRECTIONS)


def direction_for(title, tags):
    explicit = next((tag[3:] for tag in (tags or []) if tag.startswith("方向:") and tag[3:] in VALUES), None)
    if explicit:
        return explicit
    folded = (title or "").lower()
    return next((name for name, _, terms in DIRECTIONS if any(term in folded for term in terms)), "")


def direction_expression(Job):
    tags = func.json_each(Job.tags).table_valued('key', 'value')
    explicit = select(func.substr(tags.c.value, 4)).where(tags.c.value.in_(['方向:' + name for name in VALUES])).order_by(tags.c.key).limit(1).correlate(Job).scalar_subquery()
    inferred = [(or_(*(Job.title.ilike(f"%{term}%") for term in terms)), name) for name, _, terms in DIRECTIONS]
    return func.coalesce(explicit, case(*inferred, else_=""))
