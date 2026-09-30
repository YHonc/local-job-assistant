from __future__ import annotations

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm.exc import StaleDataError

from app.credentials import UnavailableCredentialStore
from app.database.models import (
    Application,
    Company,
    Job,
    WorkbenchIdempotencyRecord,
)
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'workbench.sqlite3').as_posix()}"
    app = create_app(
        database_url,
        create_tables=True,
        credential_store=UnavailableCredentialStore(),
        enable_scheduler=False,
    )
    with TestClient(app) as test_client:
        yield test_client


def _company(client, name="Acme"):
    response = client.post("/api/companies", json={"name": name})
    assert response.status_code == 201
    return response.json()


def _workbench_job(client, company_id, title, **fields):
    application = fields.pop("application", None)
    response = client.post(
        "/api/workbench/jobs",
        json={
            "company_id": company_id,
            "idempotency_key": fields.pop("idempotency_key", f"create-{title}"),
            "job": {"title": title, **fields},
            "application": application,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["item"]


def _session(client):
    return client.app.state.session_factory()


def test_aggregate_query_pagination_facets_and_deep_link(client):
    company = _company(client)
    first = _workbench_job(
        client,
        company["id"],
        "数据分析师",
        location="厦门",
        employment_type="全职",
        job_category="数据分析",
        tags=["SQL"],
    )
    second = _workbench_job(
        client,
        company["id"],
        "产品分析师",
        location="泉州",
        employment_type="实习",
        job_category="产品",
        application={"status": "已投递", "notes": "申请记录备注"},
    )

    response = client.get(
        "/api/workbench/jobs",
        params=[
            ("locations", "厦门"),
            ("locations", "泉州"),
            ("sort", "created_at_asc"),
            ("limit", "1"),
        ],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert body["items"][0]["application"] is None
    assert body["counts"] == {"active": 2, "pending": 2, "legacy_status": 0}
    assert {entry["value"] for entry in body["facets"]["categories"]} >= {"数据分析", "产品"}
    assert body["facets"]["categories"]

    detail = client.get(f"/api/workbench/jobs/{second['id']}")
    assert detail.status_code == 200
    assert detail.json()["application"]["notes"] == "申请记录备注"
    assert detail.json()["company"]["id"] == company["id"]
    assert client.get(f"/api/workbench/jobs/{first['id']}").json()["version"] == 1


def test_atomic_create_idempotency_company_selection_and_validation(client):
    created = client.post(
        "/api/workbench/jobs",
        json={
            "company": {"name": "新公司", "aliases": ["新司"]},
            "job": {"title": "数据分析师", "notes": "岗位备注", "salary_text": "面议"},
            "application": {"status": "收藏", "notes": "申请备注"},
            "idempotency_key": "same-request",
        },
    )
    assert created.status_code == 201, created.text
    first = created.json()
    assert first["idempotent_replay"] is False
    assert first["item"]["notes"] == "岗位备注"
    assert first["item"]["application"]["notes"] == "申请备注"
    replay = client.post(
        "/api/workbench/jobs",
        json={
            "company": {"name": "新公司", "aliases": ["新司"]},
            "job": {"title": "数据分析师", "notes": "岗位备注", "salary_text": "面议"},
            "application": {"status": "收藏", "notes": "申请备注"},
            "idempotency_key": "same-request",
        },
    )
    assert replay.status_code == 201
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["item"]["id"] == first["item"]["id"]
    conflict = client.post(
        "/api/workbench/jobs",
        json={
            "company_id": first["item"]["company_id"],
            "job": {"title": "其他岗位"},
            "idempotency_key": "same-request",
        },
    )
    assert conflict.status_code == 409

    duplicate_name = client.post(
        "/api/workbench/jobs",
        json={
            "company": {"name": "新司"},
            "job": {"title": "运营专员"},
            "idempotency_key": "duplicate-alias",
        },
    )
    assert duplicate_name.status_code == 409
    assert duplicate_name.json()["detail"]["candidates"][0]["id"] == first["item"]["company_id"]

    company_a = _company(client, "别名候选A")
    company_b = _company(client, "别名候选B")
    for company_id in (company_a["id"], company_b["id"]):
        assert client.patch(
            f"/api/companies/{company_id}", json={"aliases": ["公共别名"]}
        ).status_code == 200
    ambiguous = client.post(
        "/api/workbench/jobs",
        json={
            "company": {"name": "公共别名"},
            "job": {"title": "存在歧义的公司"},
            "idempotency_key": "ambiguous-alias",
        },
    )
    assert ambiguous.status_code == 409
    assert {
        candidate["id"] for candidate in ambiguous.json()["detail"]["candidates"]
    } == {company_a["id"], company_b["id"]}

    before = len(client.get("/api/workbench/jobs", params={"organize_status": "all"}).json()["items"])
    invalid = client.post(
        "/api/workbench/jobs",
        json={"company_id": 99999, "job": {"title": "不能保存"}, "idempotency_key": "bad-company"},
    )
    assert invalid.status_code == 404
    after = len(client.get("/api/workbench/jobs", params={"organize_status": "all"}).json()["items"])
    assert after == before


def test_cross_entity_patch_versions_and_conflicts_leave_no_partial_write(client):
    company = _company(client)
    item = _workbench_job(
        client,
        company["id"],
        "数据分析师",
        application={"status": "收藏", "notes": "old"},
    )
    app = item["application"]
    updated = client.patch(
        f"/api/workbench/jobs/{item['id']}",
        json={
            "expected_job_version": item["version"],
            "job": {"notes": "岗位备注"},
            "expected_application_version": app["version"],
            "application": {"status": "已投递", "notes": "新申请备注"},
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["version"] == 2
    assert body["application"]["version"] == 2
    assert body["application"]["status"] == "已投递"

    stale_job = client.patch(
        f"/api/workbench/jobs/{item['id']}",
        json={"expected_job_version": 1, "job": {"notes": "不能覆盖"}},
    )
    assert stale_job.status_code == 409
    stale_app = client.patch(
        f"/api/workbench/jobs/{item['id']}",
        json={
            "expected_job_version": body["version"],
            "job": {"notes": "不得留下部分改动"},
            "expected_application_version": 1,
            "application": {"notes": "stale"},
        },
    )
    assert stale_app.status_code == 409
    assert client.get(f"/api/workbench/jobs/{item['id']}").json()["notes"] == "岗位备注"

    null_title = client.patch(
        f"/api/workbench/jobs/{item['id']}",
        json={"expected_job_version": body["version"], "job": {"title": None}},
    )
    assert null_title.status_code == 422

    other_company = _company(client, "岗位编辑后公司")
    move = client.patch(
        f"/api/workbench/jobs/{item['id']}",
        json={"expected_job_version": body["version"], "job": {"company_id": other_company["id"]}},
    )
    assert move.status_code == 200, move.text
    assert move.json()["company"]["id"] == other_company["id"]
    assert client.patch(
        f"/api/workbench/jobs/{item['id']}",
        json={"expected_job_version": move.json()["version"], "job": {"company_id": None}},
    ).status_code == 422


def test_bulk_organize_is_atomic_preserves_application_and_limits_tags(client):
    company = _company(client)
    first = _workbench_job(
        client,
        company["id"],
        "岗位一",
        tags=[f"t{index}" for index in range(49)],
        application={"status": "已投递"},
    )
    second = _workbench_job(client, company["id"], "岗位二")
    stale = client.post(
        "/api/workbench/jobs/bulk-organize",
        json={
            "items": [
                {"job_id": first["id"], "expected_version": 1},
                {"job_id": second["id"], "expected_version": 99},
            ],
            "organize_status": "archived",
        },
    )
    assert stale.status_code == 409
    assert client.get(f"/api/workbench/jobs/{first['id']}").json()["organize_status"] == "pending"

    too_many = client.post(
        "/api/workbench/jobs/bulk-organize",
        json={"items": [{"job_id": first["id"], "expected_version": 1}], "tags_to_add": ["extra-1", "extra-2"]},
    )
    assert too_many.status_code == 422
    assert client.get(f"/api/workbench/jobs/{first['id']}").json()["tags"] == first["tags"]

    updated = client.post(
        "/api/workbench/jobs/bulk-organize",
        json={
            "items": [
                {"job_id": first["id"], "expected_version": 1},
                {"job_id": second["id"], "expected_version": 1},
            ],
            "job_category": "其他",
            "tags_to_add": ["new-tag"],
            "organize_status": "archived",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["updated_count"] == 2
    archived = client.get("/api/workbench/jobs", params={"organize_status": "archived"}).json()
    assert archived["total"] == 2
    assert client.get("/api/workbench/jobs", params={"organize_status": "active"}).json()["total"] == 0
    first_after = client.get(f"/api/workbench/jobs/{first['id']}").json()
    assert first_after["application"]["status"] == "已投递"
    assert first_after["job_category"] == "其他"
    assert first_after["category_review_status"] == "confirmed"
    assert "new-tag" in first_after["tags"]


def test_job_and_application_orm_writes_increment_versions_and_legacy_stale_maps_409(client):
    company = _company(client)
    legacy_job = client.post(
        "/api/jobs", json={"company_id": company["id"], "title": "旧接口岗位"}
    ).json()
    application = client.post("/api/applications", json={"job_id": legacy_job["id"]}).json()
    stale_session = _session(client)
    stale_job = stale_session.get(Job, legacy_job["id"])
    assert stale_job.version == 1

    assert client.patch(f"/api/jobs/{legacy_job['id']}", json={"description": "new"}).status_code == 200
    app_session = _session(client)
    assert app_session.get(Job, legacy_job["id"]).version == 2
    app_session.close()
    assert client.patch(f"/api/applications/{application['id']}", json={"notes": "note"}).status_code == 200
    app_session = _session(client)
    assert app_session.get(Application, application["id"]).version == 2
    app_session.close()

    from app.api.endpoints.jobs import job_patch
    from app.api.schemas import JobPatch
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        job_patch(legacy_job["id"], JobPatch(title="stale"), stale_session)
    assert error.value.status_code == 409
    stale_session.close()


def test_workbench_preferences_merge_and_legacy_compatibility(client):
    first = client.put(
        "/api/workbench/preferences",
        json={
            "theme_mode": "system",
            "seed_color": "#4d6f98",
            "reduce_motion": True,
            "visible_job_fields": ["category", "salary", "category"],
            "dismissed_page_hints": {"pending": True},
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["visible_job_fields"] == ["category", "salary"]
    assert first.json()["dismissed_page_hints"] == {
        "pending": True,
        "follow": False,
        "mail": False,
    }

    legacy = client.put(
        "/api/settings/preferences",
        json={"theme": "dark", "density": "compact", "poetry_preset": "random"},
    )
    assert legacy.status_code == 200
    after = client.get("/api/workbench/preferences").json()
    assert after["theme_mode"] == "dark"
    assert after["seed_color"] == "#4d6f98"
    assert after["reduce_motion"] is True
    assert after["visible_job_fields"] == ["category", "salary"]
    assert after["dismissed_page_hints"]["pending"] is True
    assert after["density"] == "compact"


def test_explicit_null_category_remains_unconfirmed(client):
    company = _company(client)
    item = _workbench_job(client, company["id"], "数据分析师", job_category=None)
    assert item["job_category"] == "数据分析"
    assert item["category_review_status"] == "pending"


def test_needs_review_filter_unifies_organization_and_category_review(client):
    company = _company(client)
    organize_pending = _workbench_job(client, company["id"], "待整理岗位")
    category_pending = _workbench_job(
        client,
        company["id"],
        "待确认分类岗位",
        job_category=None,
        organize_status="organized",
    )
    archived = _workbench_job(
        client,
        company["id"],
        "已归档岗位",
        job_category=None,
        organize_status="archived",
    )
    response = client.get("/api/workbench/jobs", params={"needs_review": "true"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {item["id"] for item in body["items"]} == {
        organize_pending["id"],
        category_pending["id"],
    }
    assert archived["id"] not in {item["id"] for item in body["items"]}


def test_atomic_create_rolls_back_company_job_application_and_key_then_retries(client):
    failed_once = {"value": False}

    def fail_application_insert(_mapper, _connection, _application):
        if not failed_once["value"]:
            failed_once["value"] = True
            from sqlalchemy.exc import IntegrityError

            raise IntegrityError("injected application failure", {}, RuntimeError("fixture"))

    event.listen(Application, "before_insert", fail_application_insert)
    payload = {
        "company": {"name": "应整体回滚的公司"},
        "job": {"title": "应整体回滚的岗位"},
        "application": {"status": "收藏"},
        "idempotency_key": "rollback-and-retry",
    }
    try:
        failed = client.post("/api/workbench/jobs", json=payload)
    finally:
        event.remove(Application, "before_insert", fail_application_insert)
    assert failed.status_code == 409

    session = _session(client)
    try:
        assert session.scalar(select(func.count(Company.id)).where(Company.name == "应整体回滚的公司")) == 0
        assert session.scalar(select(func.count(Job.id)).where(Job.title == "应整体回滚的岗位")) == 0
        assert session.scalar(select(func.count(Application.id))) == 0
        assert session.scalar(select(func.count(WorkbenchIdempotencyRecord.id))) == 0
    finally:
        session.close()

    retry = client.post("/api/workbench/jobs", json=payload)
    assert retry.status_code == 201, retry.text
    assert retry.json()["idempotent_replay"] is False
