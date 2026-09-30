import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session as SQLAlchemySession

from app.credentials import CredentialStoreError
from app.database.models import LLMConfiguration
from app.database.models import LLMRoutingSettings
from app.llm.service import record_usage
from app.llm.service import select_config
from app.llm.service import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMModelNotFoundError,
    LLMOutputError,
    LLMRateLimitError,
    LLMRequestRejectedError,
    LLMService,
    LLMTimeoutError,
)
from app.main import create_app


class FakeCredentialStore:
    def __init__(self, *, fail: bool = False):
        self.values: dict[str, str] = {}
        self.fail = fail

    def _check(self):
        if self.fail:
            raise CredentialStoreError("unavailable")

    def get(self, credential_id: str) -> str | None:
        self._check()
        return self.values.get(credential_id)

    def set(self, credential_id: str, secret: str) -> None:
        self._check()
        self.values[credential_id] = secret

    def delete(self, credential_id: str) -> None:
        self._check()
        self.values.pop(credential_id, None)


class StructuredAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


def config_payload(**overrides):
    payload = {
        "provider": "openai",
        "model": "gpt-test",
        "api_base": "https://api.example.test/v1/",
        "temperature": 0.1,
        "timeout_seconds": 30,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def llm_client(tmp_path):
    store = FakeCredentialStore()
    app = create_app(
        f"sqlite:///{tmp_path / 'llm.db'}",
        create_tables=True,
        credential_store=store,
        llm_completion=lambda **kwargs: None,
    )
    with TestClient(app) as client:
        yield client, store, app


def test_config_lifecycle_never_returns_secret(llm_client):
    client, store, app = llm_client
    assert client.get("/api/llm/config").json() == {
        "configured": False,
        "provider": None,
        "model": None,
        "api_base": None,
        "temperature": None,
        "timeout_seconds": None,
        "has_api_key": False,
        "use_proxy": False,
        "structured_output_mode": None,
        "updated_at": None,
    }

    response = client.put(
        "/api/llm/config",
        json=config_payload(api_key="top-secret-value"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["has_api_key"] is True
    assert body["api_base"] == "https://api.example.test/v1"
    assert body["structured_output_mode"] == "json_schema"
    assert "api_key" not in body
    assert "top-secret-value" not in response.text
    assert store.values["llm:default"] == "top-secret-value"

    preserved = client.put(
        "/api/llm/config",
        json=config_payload(model="gpt-updated"),
    )
    assert preserved.status_code == 200
    assert preserved.json()["model"] == "gpt-updated"
    assert preserved.json()["has_api_key"] is True
    assert store.values["llm:default"] == "top-secret-value"

    cleared = client.put(
        "/api/llm/config",
        json=config_payload(model="gpt-updated", clear_api_key=True),
    )
    assert cleared.status_code == 200
    assert cleared.json()["has_api_key"] is False
    assert store.values == {}
    with app.state.session_factory() as db:
        assert db.get(LLMConfiguration, 1).credential_id is None

    assert client.delete("/api/llm/config").status_code == 204
    assert client.get("/api/llm/config").json()["configured"] is False


def test_get_config_follows_routing_default(llm_client):
    client, _, app = llm_client
    with app.state.session_factory() as db:
        first = LLMConfiguration(id=1, display_name="disabled", enabled=False, provider="openai", model="old")
        chosen = LLMConfiguration(display_name="chosen", enabled=True, provider="ollama", model="new")
        db.add_all([first, chosen]); db.flush()
        db.add(LLMRoutingSettings(id=1, default_config_id=chosen.id)); db.commit()
    response = client.get("/api/llm/config")
    assert response.status_code == 200
    assert response.json()["model"] == "new"


def test_routing_email_pin_roundtrip_and_validation(llm_client):
    client, store, app = llm_client
    created_a = client.post("/api/llm/configs", json=config_payload(display_name="主力", model="model-a"))
    created_b = client.post(
        "/api/llm/configs",
        json=config_payload(display_name="邮件专用", model="model-b", api_key="key-b"),
    )
    assert created_a.status_code == 201 and created_b.status_code == 201
    pinned_id = created_b.json()["id"]

    saved = client.put(
        "/api/llm/routing",
        json={"email_config_id": pinned_id, "max_parallel_calls": 2},
    )
    assert saved.status_code == 200
    assert saved.json()["email_config_id"] == pinned_id
    assert client.get("/api/llm/routing").json()["email_config_id"] == pinned_id

    missing = client.put("/api/llm/routing", json={"email_config_id": 999999})
    assert missing.status_code == 422
    disabled = client.put(f"/api/llm/configs/{pinned_id}", json={"enabled": False})
    assert disabled.status_code == 200
    # Deleting the pinned configuration clears the pin instead of dangling.
    removed = client.delete(f"/api/llm/configs/{pinned_id}")
    assert removed.status_code == 204
    routing = client.get("/api/llm/routing").json()
    assert routing["email_config_id"] is None


def test_current_config_ignores_disabled_fallback(llm_client):
    client, _, app = llm_client
    with app.state.session_factory() as db:
        db.add(LLMConfiguration(id=1, display_name="disabled", enabled=False, provider="openai", model="old"))
        db.commit()
    assert client.get("/api/llm/config").json()["configured"] is False


def test_select_config_falls_back_when_pinned_default_is_paused(llm_client):
    """A paused default must not break sync/extraction while another model is enabled."""
    client, _, app = llm_client
    with app.state.session_factory() as db:
        default = LLMConfiguration(display_name="default", enabled=True, provider="openai", model="model-default")
        backup = LLMConfiguration(display_name="backup", enabled=True, provider="openai", model="model-backup")
        db.add_all([default, backup]); db.flush()
        db.add(LLMRoutingSettings(id=1, auto_select_enabled=False, default_config_id=default.id)); db.commit()
        assert select_config(db).model == "model-default"
        default.enabled = False
        db.commit()
        assert select_config(db).model == "model-backup"


def test_legacy_put_reuses_disabled_singleton_row(llm_client):
    """Saving via the legacy endpoint must update the paused id=1 row, not 500 on a PK clash."""
    client, _, app = llm_client
    with app.state.session_factory() as db:
        db.add(LLMConfiguration(id=1, display_name="paused", enabled=False, provider="openai", model="old"))
        db.commit()
    saved = client.put("/api/llm/config", json=config_payload(model="legacy-new"))
    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert saved.json()["model"] == "legacy-new"
    with app.state.session_factory() as db:
        rows = db.query(LLMConfiguration).all()
        assert len(rows) == 1
        assert rows[0].id == 1
        assert rows[0].enabled is True
        assert rows[0].model == "legacy-new"


def test_record_usage_failure_does_not_rollback_caller_session(llm_client, monkeypatch):
    _, _, app = llm_client
    with app.state.session_factory() as db:
        db.add(LLMConfiguration(id=1, display_name="cfg", enabled=True, provider="openai", model="m"))
        db.commit()
        pending = LLMConfiguration(display_name="pending", enabled=True, provider="openai", model="m")
        db.add(pending)
        monkeypatch.setattr("app.llm.service.update", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("write failed")))
        record_usage(db, 1, True, 1)
        db.commit()
        assert db.query(LLMConfiguration).filter_by(display_name="pending").one()


def test_response_usage_supports_object_and_dict_shapes():
    config = SimpleNamespace(
        provider="openai", model="m", api_base=None, temperature=0,
        timeout_seconds=20, credential_id=None,
    )
    object_style = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"name":"A"}'))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
    )
    service = LLMService(config, FakeCredentialStore(), lambda **kwargs: object_style)
    assert service.structured_extract("s", "u", StructuredAnswer).name == "A"
    assert service.last_usage == {"prompt_tokens": 11, "completion_tokens": 7}

    dict_style = {
        "choices": [{"message": {"content": '{"name":"B"}'}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 0},
    }
    service2 = LLMService(config, FakeCredentialStore(), lambda **kwargs: dict_style)
    assert service2.structured_extract("s", "u", StructuredAnswer).name == "B"
    assert service2.last_usage == {"prompt_tokens": 3, "completion_tokens": 0}

    bare = {"choices": [{"message": {"content": '{"name":"C"}'}}]}
    service3 = LLMService(config, FakeCredentialStore(), lambda **kwargs: bare)
    assert service3.structured_extract("s", "u", StructuredAnswer).name == "C"
    assert service3.last_usage is None


def test_request_usage_preserves_zero_and_nullable_fields():
    config = SimpleNamespace(
        provider="openai", model="m", api_base=None, temperature=0,
        timeout_seconds=20, credential_id=None,
    )
    zero = {
        "choices": [{"message": {"content": '{"name":"zero"}'}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
    }
    service = LLMService(config, FakeCredentialStore(), lambda **kwargs: zero)
    assert service.structured_extract("s", "u", StructuredAnswer).name == "zero"
    # Legacy last_usage retains its historical no-usage shape for an all-zero
    # response; request_usage preserves the legal zeros for new telemetry.
    assert service.last_usage is None
    assert service.request_usage == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "provider_calls": 1,
        "usage_complete": True,
    }

    partial = {
        "choices": [{"message": {"content": '{"name":"partial"}'}}],
        "usage": {"prompt_tokens": 4},
    }
    service = LLMService(config, FakeCredentialStore(), lambda **kwargs: partial)
    assert service.structured_extract("s", "u", StructuredAnswer).name == "partial"
    assert service.last_usage == {"prompt_tokens": 4, "completion_tokens": 0}
    assert service.request_usage == {
        "prompt_tokens": 4,
        "completion_tokens": None,
        "provider_calls": 1,
        "usage_complete": False,
    }


def test_request_usage_sums_json_schema_fallback_calls_and_keeps_last_usage():
    class RejectedByGateway(Exception):
        status_code = 400
        usage = {"prompt_tokens": 2, "completion_tokens": 3}

    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        if "response_format" in kwargs:
            raise RejectedByGateway()
        return {
            "choices": [{"message": {"content": '{"name":"fallback"}'}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        }

    config = SimpleNamespace(
        provider="openai", model="m", api_base="https://fallback-usage.example.test/v1",
        temperature=0, timeout_seconds=20, credential_id=None,
    )
    service = LLMService(config, FakeCredentialStore(), completion)
    assert service.structured_extract("s", "u", StructuredAnswer).name == "fallback"
    assert len(calls) == 2
    assert service.last_usage == {"prompt_tokens": 5, "completion_tokens": 7}
    assert service.request_usage == {
        "prompt_tokens": 7,
        "completion_tokens": 10,
        "provider_calls": 2,
        "usage_complete": True,
    }


def test_request_usage_does_not_hide_unknown_first_fallback_call():
    class RejectedByGateway(Exception):
        status_code = 400

    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        if "response_format" in kwargs:
            raise RejectedByGateway()
        return {
            "choices": [{"message": {"content": '{"name":"partial-total"}'}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        }

    config = SimpleNamespace(
        provider="openai", model="m", api_base="https://fallback-missing.example.test/v1",
        temperature=0, timeout_seconds=20, credential_id=None,
    )
    service = LLMService(config, FakeCredentialStore(), completion)
    assert service.structured_extract("s", "u", StructuredAnswer).name == "partial-total"
    assert len(calls) == 2
    assert service.last_usage == {"prompt_tokens": 5, "completion_tokens": 7}
    assert service.request_usage == {
        "prompt_tokens": None,
        "completion_tokens": None,
        "provider_calls": 2,
        "usage_complete": False,
    }


def test_reused_service_clears_usage_before_credential_failure():
    config = SimpleNamespace(
        provider="openai", model="m", api_base=None, temperature=0,
        timeout_seconds=20, credential_id="llm:test",
    )
    store = FakeCredentialStore()
    store.set("llm:test", "configured")
    service = LLMService(
        config,
        store,
        lambda **kwargs: {
            "choices": [{"message": {"content": '{"name":"first"}'}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        },
    )
    assert service.structured_extract("s", "u", StructuredAnswer).name == "first"
    assert service.last_usage == {"prompt_tokens": 2, "completion_tokens": 1}
    assert service.request_usage is not None

    store.delete("llm:test")
    with pytest.raises(LLMConfigurationError):
        service.structured_extract("s", "u", StructuredAnswer)
    assert service.last_usage is None
    assert service.request_usage is None


@pytest.mark.parametrize("invalid", [-1, 3.5, True, float("inf"), "²", "9.5"])
def test_request_usage_rejects_non_integer_token_shapes(invalid):
    config = SimpleNamespace(
        provider="openai", model="m", api_base=None, temperature=0,
        timeout_seconds=20, credential_id=None,
    )
    response = {
        "choices": [{"message": {"content": '{"name":"strict"}'}}],
        "usage": {"prompt_tokens": invalid, "completion_tokens": 6},
    }
    service = LLMService(config, FakeCredentialStore(), lambda **kwargs: response)
    assert service.structured_extract("s", "u", StructuredAnswer).name == "strict"
    assert service.request_usage == {
        "prompt_tokens": None,
        "completion_tokens": 6,
        "provider_calls": 1,
        "usage_complete": False,
    }


def test_usage_summary_counts_tokens_including_invalid_output_failures(llm_client):
    client, _, app = llm_client
    assert client.put("/api/llm/config", json=config_payload(api_key="k")).status_code == 200

    app.state.llm_completion = lambda **kwargs: {
        "choices": [{"message": {"content": '{"status":"ok"}'}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }
    assert client.post("/api/llm/test").status_code == 200

    # 响应已返回但结构校验失败：token 同样消耗，必须计入失败调用的统计。
    app.state.llm_completion = lambda **kwargs: {
        "choices": [{"message": {"content": "not-json"}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 5},
    }
    assert client.post("/api/llm/test").status_code == 502

    summary = client.get("/api/llm/configs").json()[0]
    assert summary["call_count"] == 2
    assert summary["success_count"] == 1
    assert summary["failure_count"] == 1
    assert summary["total_prompt_tokens"] == 150
    assert summary["total_completion_tokens"] == 25
    assert summary["last_used_at"] is not None


def test_no_key_configuration_does_not_create_credential_reference(llm_client):
    client, store, app = llm_client
    response = client.put(
        "/api/llm/config",
        json=config_payload(provider="ollama", model="llama3", api_base="http://127.0.0.1:11434"),
    )
    assert response.status_code == 200
    assert response.json()["has_api_key"] is False
    assert store.values == {}
    with app.state.session_factory() as db:
        assert db.get(LLMConfiguration, 1).credential_id is None


@pytest.mark.parametrize(
    "payload",
    [
        config_payload(api_key="secret", clear_api_key=True),
        config_payload(api_base="file:///tmp/provider"),
        config_payload(api_base="https://user:password@example.test"),
        config_payload(provider="   "),
    ],
)
def test_config_validation_rejects_ambiguous_or_unsafe_input(llm_client, payload):
    client, _, _ = llm_client
    assert client.put("/api/llm/config", json=payload).status_code == 422


def test_credential_backend_failure_is_visible(tmp_path):
    app = create_app(
        f"sqlite:///{tmp_path / 'unavailable.db'}",
        create_tables=True,
        credential_store=FakeCredentialStore(fail=True),
        llm_completion=lambda **kwargs: None,
    )
    with TestClient(app) as client:
        response = client.put(
            "/api/llm/config",
            json=config_payload(api_key="secret"),
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "credential store unavailable"


def test_database_failure_restores_previous_secret(llm_client, monkeypatch):
    client, store, _ = llm_client
    assert client.put(
        "/api/llm/config",
        json=config_payload(api_key="old-secret"),
    ).status_code == 200

    def fail_commit(self):
        raise RuntimeError("database write failed")

    monkeypatch.setattr(SQLAlchemySession, "commit", fail_commit)
    response = client.put(
        "/api/llm/config",
        json=config_payload(api_key="new-secret"),
    )
    assert response.status_code == 500
    assert store.values["llm:default"] == "old-secret"
    assert "new-secret" not in response.text


def test_structured_output_contract_and_local_provider_without_key():
    captured = {}

    def completion(**kwargs):
        captured.update(kwargs)
        return {"choices": [{"message": {"content": json.dumps({"name": "Alice"})}}]}

    config = SimpleNamespace(
        provider="ollama",
        model="llama3",
        api_base="http://127.0.0.1:11434",
        temperature=0,
        timeout_seconds=20,
        credential_id=None,
    )
    result = LLMService(config, FakeCredentialStore(), completion).structured_extract(
        "system",
        "user",
        StructuredAnswer,
    )
    assert result.name == "Alice"
    assert captured["model"] == "ollama/llama3"
    assert "api_key" not in captured
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["response_format"]["json_schema"]["name"] == "StructuredAnswer"


def test_structured_output_accepts_message_parsed_object():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed={"name": "Parsed"}))]
    )
    config = SimpleNamespace(
        provider="openai",
        model="openai/gpt-test",
        api_base=None,
        temperature=0,
        timeout_seconds=20,
        credential_id="llm:default",
    )
    store = FakeCredentialStore()
    store.set("llm:default", "secret")
    result = LLMService(config, store, lambda **kwargs: response).structured_extract(
        "system",
        "user",
        StructuredAnswer,
    )
    assert result.name == "Parsed"


def test_sensenova_uses_prompt_json_compatibility_without_response_format():
    captured = {}

    def completion(**kwargs):
        captured.update(kwargs)
        return {
            "choices": [
                {"message": {"content": '```json\n{"name":"SenseNova"}\n```'}}
            ]
        }

    config = SimpleNamespace(
        provider="openai",
        model="deepseek-v4-flash",
        api_base="https://token.sensenova.cn/v1",
        temperature=0.1,
        timeout_seconds=30,
        credential_id="llm:default",
    )
    store = FakeCredentialStore()
    store.set("llm:default", "test-secret")

    result = LLMService(config, store, completion).structured_extract(
        "system",
        "user",
        StructuredAnswer,
        max_tokens=64,
    )

    assert result.name == "SenseNova"
    assert captured["model"] == "openai/deepseek-v4-flash"
    assert captured["api_base"] == "https://token.sensenova.cn/v1"
    assert captured["max_tokens"] == 64
    assert "response_format" not in captured
    assert "Return exactly one JSON object" in captured["messages"][0]["content"]
    assert '"name"' in captured["messages"][0]["content"]


@pytest.mark.parametrize(
    ("status_code", "expected_error", "expected_code"),
    [
        (401, LLMAuthenticationError, "llm_auth_failed"),
        (404, LLMModelNotFoundError, "llm_model_not_found"),
        (429, LLMRateLimitError, "llm_rate_limited"),
        (400, LLMRequestRejectedError, "llm_request_rejected"),
    ],
)
def test_provider_errors_are_classified_without_exposing_raw_message(
    status_code,
    expected_error,
    expected_code,
):
    class ProviderFailure(Exception):
        def __init__(self):
            super().__init__("provider-secret-response")
            self.status_code = status_code

    config = SimpleNamespace(
        provider="openai",
        model="gpt-test",
        api_base="https://api.example.test/v1",
        temperature=0,
        timeout_seconds=20,
        credential_id=None,
    )

    def completion(**kwargs):
        raise ProviderFailure()

    with pytest.raises(expected_error) as captured:
        LLMService(config, FakeCredentialStore(), completion).structured_extract(
            "system",
            "user",
            StructuredAnswer,
        )
    assert captured.value.code == expected_code
    assert "provider-secret-response" not in str(captured.value)


def test_connection_test_uses_saved_config_and_returns_only_safe_metadata(llm_client):
    client, _, app = llm_client
    missing = client.post("/api/llm/test")
    assert missing.status_code == 409
    assert missing.json()["detail"]["code"] == "llm_not_configured"

    captured = {}

    def completion(**kwargs):
        captured.update(kwargs)
        return {"choices": [{"message": {"content": '{"status":"ok"}'}}]}

    app.state.llm_completion = completion
    saved = client.put(
        "/api/llm/config",
        json=config_payload(
            model="deepseek-v4-flash",
            api_base="https://token.sensenova.cn/v1",
            api_key="test-secret-value",
        ),
    )
    assert saved.status_code == 200
    assert saved.json()["structured_output_mode"] == "prompt_json"

    rejected = client.post(
        "/api/llm/test",
        headers={"Origin": "https://attacker.example"},
    )
    assert rejected.status_code == 403

    response = client.post(
        "/api/llm/test",
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["model"] == "deepseek-v4-flash"
    assert body["structured_output_mode"] == "prompt_json"
    assert body["elapsed_ms"] >= 0
    assert "test-secret-value" not in response.text
    assert "choices" not in response.text
    assert captured["model"] == "openai/deepseek-v4-flash"
    assert captured["max_tokens"] == 64
    assert "response_format" not in captured


def test_connection_test_returns_redacted_stable_provider_error(llm_client):
    client, _, app = llm_client
    assert client.put(
        "/api/llm/config",
        json=config_payload(api_key="test-secret-value"),
    ).status_code == 200

    class AuthenticationFailure(Exception):
        status_code = 401

    def completion(**kwargs):
        raise AuthenticationFailure("raw-provider-body test-secret-value")

    app.state.llm_completion = completion
    response = client.post("/api/llm/test")
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "llm_auth_failed"
    assert "raw-provider-body" not in response.text
    assert "test-secret-value" not in response.text


def test_invalid_output_and_timeout_are_distinct():
    config = SimpleNamespace(
        provider="ollama",
        model="llama3",
        api_base=None,
        temperature=0,
        timeout_seconds=20,
        credential_id=None,
    )
    invalid = lambda **kwargs: {"choices": [{"message": {"content": "not-json"}}]}
    with pytest.raises(LLMOutputError):
        LLMService(config, FakeCredentialStore(), invalid).structured_extract(
            "system",
            "user",
            StructuredAnswer,
        )

    def timeout(**kwargs):
        raise TimeoutError("late")

    with pytest.raises(LLMTimeoutError):
        LLMService(config, FakeCredentialStore(), timeout).structured_extract(
            "system",
            "user",
            StructuredAnswer,
        )


def test_models_probe_lists_remote_models_without_storing_key(llm_client, monkeypatch):
    import app.llm.routes as llm_routes

    client, store, app = llm_client
    captured = {}

    def fake_list(provider, api_base, api_key, timeout, proxy_url=None):
        captured.update(provider=provider, api_base=api_base, api_key=api_key, timeout=timeout)
        return ["glm-5.2", "deepseek-v4-flash"]

    monkeypatch.setattr(llm_routes, "_list_remote_models", fake_list)
    response = client.post(
        "/api/llm/models-probe",
        json={
            "provider": "sensenova",
            "api_base": "https://token.sensenova.cn/v1/",
            "api_key": "probe-secret",
            "timeout_seconds": 9,
        },
    )
    assert response.status_code == 200
    assert response.json() == {"models": ["glm-5.2", "deepseek-v4-flash"]}
    assert captured == {
        "provider": "sensenova",
        "api_base": "https://token.sensenova.cn/v1",
        "api_key": "probe-secret",
        "timeout": 9,
    }
    assert store.values == {}, "probe key must not touch the credential store"


def test_models_probe_reuses_saved_credential_from_config(llm_client, monkeypatch):
    import app.llm.routes as llm_routes

    client, store, app = llm_client
    created = client.post(
        "/api/llm/configs",
        json={
            "provider": "sensenova",
            "model": "deepseek-v4-flash",
            "api_base": "https://token.sensenova.cn/v1",
            "api_key": "saved-secret",
        },
    ).json()
    captured = {}

    def fake_list(provider, api_base, api_key, timeout, proxy_url=None):
        captured.update(api_key=api_key)
        return ["deepseek-v4-flash"]

    monkeypatch.setattr(llm_routes, "_list_remote_models", fake_list)
    response = client.post(
        "/api/llm/models-probe",
        json={
            "provider": "sensenova",
            "api_base": "https://token.sensenova.cn/v1",
            "config_id": created["id"],
        },
    )
    assert response.status_code == 200
    assert response.json() == {"models": ["deepseek-v4-flash"]}
    assert captured["api_key"] == "saved-secret"


def test_json_schema_rejection_downgrades_to_prompt_json_and_is_sticky(tmp_path):
    class RejectedByGateway(Exception):
        status_code = 400

    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        if "response_format" in kwargs:
            raise RejectedByGateway("This response_format type is unavailable now")
        assert "validate against this schema" in kwargs["messages"][0]["content"]
        return {"choices": [{"message": {"content": '{"status": "ok"}'}}]}

    store = FakeCredentialStore()
    app = create_app(
        f"sqlite:///{tmp_path / 'downgrade.db'}",
        create_tables=True,
        credential_store=store,
        llm_completion=completion,
    )
    from app.llm import service as llm_service

    host = "gateway.example.test"
    llm_service._STRUCTURED_MODE_OVERRIDES.pop(host, None)
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/llm/configs",
                json={
                    "provider": "openai",
                    "model": "m",
                    "api_base": f"https://{host}/v1",
                    "temperature": 0,
                    "timeout_seconds": 20,
                },
            )
            assert created.status_code == 201
            config_id = created.json()["id"]

            tested = client.post(f"/api/llm/configs/{config_id}/test")
            assert tested.status_code == 200
            assert tested.json()["ok"] is True
            assert tested.json()["structured_output_mode"] == "prompt_json"
            # First call: doomed json_schema attempt + prompt_json retry.
            assert len(calls) == 2

            # The downgrade is sticky: the next call goes straight to prompt_json.
            again = client.post(f"/api/llm/configs/{config_id}/test")
            assert again.status_code == 200
            assert len(calls) == 3
            assert "response_format" not in calls[-1]
            assert again.json()["structured_output_mode"] == "prompt_json"
    finally:
        llm_service._STRUCTURED_MODE_OVERRIDES.pop(host, None)
