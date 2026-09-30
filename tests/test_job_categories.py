import pytest

from app.job_categories import JobCategory, infer_job_category, resolve_job_category


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("数据分析实习生", JobCategory.DATA_ANALYSIS),
        ("用户运营专员", JobCategory.OPERATIONS),
        ("AI 产品经理", JobCategory.PRODUCT),
        ("大模型算法工程师", JobCategory.AI_ALGORITHM),
        ("Software Engineer", JobCategory.TECHNOLOGY),
        ("供应链采购专员", JobCategory.SUPPLY_CHAIN_MANUFACTURING),
        ("财务审计", JobCategory.FINANCE),
        ("战略咨询顾问", JobCategory.CONSULTING_STRATEGY),
        ("人力资源实习生", JobCategory.CORPORATE_SUPPORT),
    ],
)
def test_infer_job_category_prefers_specific_title_rules(title, expected):
    assert infer_job_category(title) is expected


def test_infer_job_category_uses_description_then_other():
    assert infer_job_category("项目实习生", "负责商业分析和经营报表") is JobCategory.DATA_ANALYSIS
    assert infer_job_category("未命名岗位", "暂无明确职责") is JobCategory.OTHER


def test_resolve_job_category_keeps_manual_override_or_infers():
    assert resolve_job_category("运营", title="数据分析师") == "运营"
    assert resolve_job_category(None, title="数据分析师") == "数据分析"
