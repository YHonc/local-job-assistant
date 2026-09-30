from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.database.models import ApplicationStatus


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "test.sqlite3"
    app = create_app(f"sqlite:///{db}", create_tables=True)
    with TestClient(app) as c:
        yield c


def assert_utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None and parsed.utcoffset() is not None


def company(client, name="Acme", **extra):
    payload = {"name": name, **extra}
    response = client.post("/api/companies", json=payload)
    assert response.status_code == 201
    return response.json()


def job(client, company_id, title="Data Analyst", **extra):
    payload = {"company_id": company_id, "title": title, **extra}
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 201
    return response.json()


def test_health_and_not_found_and_datetime_offset(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    for path in ("/api/companies/999", "/api/jobs/999", "/api/applications/999"):
        assert client.get(path).status_code == 404
    c = company(client, aliases=["ACME"])
    assert_utc(c["created_at"])


def test_sqlite_engine_enables_wal_and_busy_timeout(tmp_path):
    from app.database.session import make_engine

    engine = make_engine(f"sqlite:///{tmp_path / 'pragmas.db'}")
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
            assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 5000
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        engine.dispose()


def test_mutating_requests_share_one_origin_policy(client):
    evil = {"Origin": "https://attacker.example"}
    assert client.put("/api/settings/theme", json={"value": "dark"}, headers=evil).status_code == 403
    assert client.post("/api/companies", json={"name": "Evil"}, headers=evil).status_code == 403
    assert client.patch("/api/settings/theme", json={"value": "dark"}, headers=evil).status_code == 403
    assert client.delete("/api/settings/theme", headers=evil).status_code == 403
    # 读取接口没有副作用，不拦；本机页面与扩展来源照常放行。
    assert client.get("/api/settings", headers=evil).status_code == 200
    allowed = {"Origin": "http://127.0.0.1:8765"}
    assert client.put("/api/settings/theme", json={"value": "dark"}, headers=allowed).status_code == 200
    extension = {"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"}
    assert client.post("/api/companies", json={"name": "From Extension"}, headers=extension).status_code == 201


def test_company_crud_trim_blank_duplicate_alias_and_patch_semantics(client):
    c = company(client, name="  Acme  ", aliases=[" ACME ", "acme-cn"])
    assert c["name"] == "Acme"
    assert c["aliases"] == ["ACME", "acme-cn"]
    assert client.get(f"/api/companies/{c['id']}").status_code == 200
    assert client.get("/api/companies").json()[0]["id"] == c["id"]
    assert client.post("/api/companies", json={"name": "Acme"}).status_code == 409
    for payload in ({"name": "   "}, {"name": ""}):
        assert client.post("/api/companies", json=payload).status_code == 422
    assert client.post("/api/companies", json={"name": "Alias Blank", "aliases": ["ok", "   "]}).status_code == 422
    assert client.post("/api/companies", json={"name": "Alias Empty", "aliases": [""]}).status_code == 422
    assert client.post("/api/companies", json={"name": "Alias Dup", "aliases": [" x ", "x", " y ", "x"]}).json()["aliases"] == ["x", "y"]
    patched = client.patch(f"/api/companies/{c['id']}", json={"notes": "note"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Acme"
    assert patched.json()["aliases"] == ["ACME", "acme-cn"]
    assert client.patch(f"/api/companies/{c['id']}", json={"name": "  New Name "}).json()["name"] == "New Name"
    assert client.delete(f"/api/companies/{c['id']}").status_code == 204
    assert client.get(f"/api/companies/{c['id']}").status_code == 404


def test_job_crud_search_all_fields_filters_and_patch_semantics(client):
    c = company(client, name="  Acme Corp ", aliases=["ACME", "阿克米"])
    other = company(client, name="Other Co")
    j = job(client, c["id"], title="  Python Engineer ", location="Shenzhen", description="Build analytics", source_url="https://x", source_website="LinkedIn", employment_type="全职", published_at="2026-08-29")
    other_job = job(client, other["id"], title="Other", location="Beijing", description="Python", source_website="Boss", job_category="运营")
    assert j["title"] == "Python Engineer"
    assert j["job_category"] == "技术研发"
    assert j["published_at"] == "2026-08-29"
    assert other_job["job_category"] == "运营"
    for q in ("analytics", "Shenzhen", "LinkedIn", "https://x", "ACME", "阿克米", "Acme Corp", "Python Engineer", "技术研发"):
        assert client.get("/api/jobs", params={"q": q}).status_code == 200
        assert j["id"] in [x["id"] for x in client.get("/api/jobs", params={"q": q}).json()]
    assert [x["id"] for x in client.get("/api/jobs", params={"company_id": c["id"]}).json()] == [j["id"]]
    assert [x["id"] for x in client.get("/api/jobs", params={"employment_type": "全职"}).json()] == [j["id"]]
    assert [x["id"] for x in client.get("/api/jobs", params={"job_category": "技术研发"}).json()] == [j["id"]]
    assert client.get("/api/jobs", params={"job_category": "不存在"}).status_code == 422
    application = client.post("/api/applications", json={"job_id": j["id"], "status": "已投递"}).json()
    assert [x["id"] for x in client.get("/api/jobs", params={"application_status": "已投递"}).json()] == [j["id"]]
    patched = client.patch(f"/api/jobs/{j['id']}", json={"description": "updated"})
    assert patched.status_code == 200 and patched.json()["title"] == "Python Engineer"
    assert patched.json()["location"] == "Shenzhen"
    assert_utc(patched.json()["created_at"])
    assert_utc(patched.json()["updated_at"])
    assert client.patch(f"/api/jobs/{j['id']}", json={"company_id": 999}).status_code == 404
    assert client.patch(f"/api/jobs/{j['id']}", json={"title": None}).status_code == 422
    assert client.patch(f"/api/jobs/{j['id']}", json={"company_id": None}).status_code == 422
    manual = client.patch(f"/api/jobs/{j['id']}", json={"job_category": "运营"}).json()
    assert manual["job_category"] == "运营"
    assert client.patch(f"/api/jobs/{j['id']}", json={"title": "商业分析师"}).json()["job_category"] == "运营"
    recategorized = client.patch(f"/api/jobs/{j['id']}", json={"title": "商业分析师", "job_category": None})
    assert recategorized.status_code == 200
    assert recategorized.json()["job_category"] == "数据分析"
    assert client.patch(f"/api/jobs/{j['id']}", json={"job_category": "不存在"}).status_code == 422
    assert client.patch(f"/api/jobs/{j['id']}", json={"published_at": "not-a-date"}).status_code == 422
    assert client.patch(f"/api/jobs/{j['id']}", json={"published_at": None}).json()["published_at"] is None
    assert client.patch(f"/api/companies/{c['id']}", json={"name": None}).status_code == 422


def test_application_unique_statuses_invalid_patch_and_timestamps(client):
    c = company(client)
    j = job(client, c["id"])
    created = client.post("/api/applications", json={"job_id": j["id"], "status": "收藏", "notes": "first"})
    assert created.status_code == 201
    application = created.json()
    assert_utc(application["status_updated_at"])
    assert client.post("/api/applications", json={"job_id": j["id"]}).status_code == 409
    for index, status in enumerate(ApplicationStatus):
        extra_job = job(client, c["id"], title=f"Role {index}")
        response = client.post("/api/applications", json={"job_id": extra_job["id"], "status": status.value})
        assert response.status_code == 201
    assert client.get("/api/applications", params={"status": "已投递"}).status_code == 200
    assert client.post("/api/applications", json={"job_id": 999, "status": "not-a-status"}).status_code == 422
    assert client.patch(f"/api/applications/{application['id']}", json={"status": "not-a-status"}).status_code == 422
    assert client.patch(f"/api/applications/{application['id']}", json={"status": None}).status_code == 422
    original_time = application["status_updated_at"]
    notes_patch = client.patch(f"/api/applications/{application['id']}", json={"notes": "changed"})
    assert notes_patch.status_code == 200
    assert notes_patch.json()["status_updated_at"] == original_time
    status_patch = client.patch(f"/api/applications/{application['id']}", json={"status": "已投递"})
    assert status_patch.status_code == 200
    assert status_patch.json()["status"] == "已投递"
    assert status_patch.json()["status_updated_at"] != original_time


def test_application_delete_and_job_cascade_company_restriction(client):
    c = company(client)
    j = job(client, c["id"])
    a = client.post("/api/applications", json={"job_id": j["id"]}).json()
    assert client.delete(f"/api/companies/{c['id']}").status_code == 409
    assert client.delete(f"/api/jobs/{j['id']}").status_code == 204
    assert client.get(f"/api/applications/{a['id']}").status_code == 404

    direct_job = job(client, c["id"], title="Direct Delete")
    direct_application = client.post("/api/applications", json={"job_id": direct_job["id"]}).json()
    assert client.delete(f"/api/applications/{direct_application['id']}").status_code == 204


def test_job_bulk_delete_success_and_application_cascade(client):
    c = company(client)
    jobs = [job(client, c["id"], title=f"Bulk delete {index}") for index in range(2)]
    applications = [client.post("/api/applications", json={"job_id": item["id"]}).json() for item in jobs]
    requested = [jobs[1]["id"], jobs[0]["id"]]
    response = client.post("/api/jobs/bulk-delete", json={"job_ids": requested})
    assert response.status_code == 200
    assert response.json() == {"deleted_count": 2, "deleted_job_ids": requested}
    for item in jobs:
        assert client.get(f"/api/jobs/{item['id']}").status_code == 404
    for item in applications:
        assert client.get(f"/api/applications/{item['id']}").status_code == 404


def test_job_bulk_delete_validation(client):
    for payload in ({"job_ids": []}, {"job_ids": [0]}, {"job_ids": [-1]}, {"job_ids": [1, 1]}, {"job_ids": list(range(1, 202))}):
        assert client.post("/api/jobs/bulk-delete", json=payload).status_code == 422


def test_job_bulk_delete_missing_id_is_atomic(client):
    c = company(client)
    jobs = [job(client, c["id"], title=f"Atomic delete {index}") for index in range(2)]
    response = client.post("/api/jobs/bulk-delete", json={"job_ids": [jobs[0]["id"], 999999, jobs[1]["id"]]})
    assert response.status_code == 404
    assert [client.get(f"/api/jobs/{item['id']}").status_code for item in jobs] == [200, 200]


def test_settings_put_list_get_delete(client):
    assert client.put("/api/settings/%20", json={"value": "bad"}).status_code == 422
    assert client.put("/api/settings/api_key", json={"value": "must-not-persist"}).status_code == 422
    assert client.put("/api/settings/theme", json={"value": "neon"}).status_code == 422
    assert client.put("/api/settings/theme", json={"value": None}).status_code == 422
    put = client.put("/api/settings/theme", json={"value": "dark"})
    assert put.status_code in (200, 201)
    assert put.json()["key"] == "theme"
    assert put.json()["value"] == "dark"
    assert_utc(put.json()["updated_at"])
    assert client.put("/api/settings/theme", json={"value": "light"}).json()["value"] == "light"
    assert client.get("/api/settings/theme").json()["value"] == "light"
    assert client.get("/api/settings").json()[0]["key"] == "theme"
    assert client.delete("/api/settings/theme").status_code == 204
    assert client.get("/api/settings/theme").status_code == 404


def test_preferences_bundle_put_is_atomic_contract(client):
    response = client.put("/api/settings/preferences", json={"theme": "dark", "density": "compact"})
    assert response.status_code == 200
    assert [item["key"] for item in response.json()] == ["theme", "density", "poetry_preset"]
    assert [item["value"] for item in response.json()] == ["dark", "compact", "random"]
    assert {item["key"]: item["value"] for item in client.get("/api/settings").json()} == {
        "theme": "dark", "density": "compact", "poetry_preset": "random"
    }
    for payload in ({"theme": "neon", "density": "compact"}, {"theme": "light", "density": "spacious"}, {"theme": "light", "density": "comfortable", "poetry_preset": "invalid"}):
        assert client.put("/api/settings/preferences", json=payload).status_code == 422
    assert {item["key"]: item["value"] for item in client.get("/api/settings").json()} == {
        "theme": "dark", "density": "compact", "poetry_preset": "random"
    }
    preserved = client.put("/api/settings/preferences", json={"theme": "light", "density": "comfortable", "poetry_preset": "shuidiao"})
    assert [item["key"] for item in preserved.json()] == ["theme", "density", "poetry_preset"]
    omitted = client.put("/api/settings/preferences", json={"theme": "dark", "density": "compact"})
    assert [item["value"] for item in omitted.json()] == ["dark", "compact", "shuidiao"]
    assert client.put("/api/settings/theme", json={"value": "light"}).status_code == 200


def test_poetry_preset_generic_setting_api(client):
    for value in ("random", "shuidiao", "dielianhua", "qingpingle", "yulinling", "jiangchengzi", "nian"):
        response = client.put("/api/settings/poetry_preset", json={"value": value})
        assert response.status_code == 200
        assert response.json()["value"] == value
    assert client.put("/api/settings/poetry_preset", json={"value": "invalid"}).status_code == 422


def test_application_bulk_status_success_and_validation(client):
    c = company(client)
    applications = []
    for index in range(3):
        applications.append(client.post("/api/applications", json={"job_id": job(client, c["id"], title=f"Bulk {index}")["id"], "status": "收藏"}).json())
    requested = [item["id"] for item in reversed(applications)]
    response = client.patch("/api/applications/bulk-status", json={"application_ids": requested, "status": "已投递"})
    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body] == requested
    assert {item["status"] for item in body} == {"已投递"}
    assert len({item["status_updated_at"] for item in body}) == 1
    for payload in ({"application_ids": [], "status": "已投递"}, {"application_ids": [requested[0], requested[0]], "status": "已投递"}, {"application_ids": [0], "status": "已投递"}, {"application_ids": [-1], "status": "已投递"}, {"application_ids": [requested[0]], "status": "invalid"}):
        assert client.patch("/api/applications/bulk-status", json=payload).status_code == 422


def test_application_bulk_status_missing_id_is_atomic(client):
    c = company(client)
    apps = []
    for index in range(2):
        j = job(client, c["id"], title=f"Atomic {index}")
        apps.append(client.post("/api/applications", json={"job_id": j["id"], "status": "收藏"}).json())
    before = {item["id"]: client.get(f"/api/applications/{item['id']}").json() for item in apps}
    response = client.patch("/api/applications/bulk-status", json={"application_ids": [apps[0]["id"], 999999, apps[1]["id"]], "status": "已投递"})
    assert response.status_code == 404
    for item in apps:
        after = client.get(f"/api/applications/{item['id']}").json()
        assert after["status"] == before[item["id"]]["status"]
        assert after["status_updated_at"] == before[item["id"]]["status_updated_at"]
