"""Proxy routing checks use synthetic credentials and loopback-only servers."""
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.llm.proxy import completion_client
from app.llm.routes import _list_remote_models
from app.llm.schemas import ProxySettings
from app.llm.service import LLMConfigurationError, LLMError, LLMService
from app.main import create_app
from test_v1_llm import FakeCredentialStore


class Answer(BaseModel):
    status: str


@pytest.fixture
def client(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'proxy.db'}", create_tables=True,
                     credential_store=FakeCredentialStore(), enable_scheduler=False,
                     llm_completion=lambda **kw: {"choices": [{"message": {"content": '{"status":"ok"}'}}]})
    with TestClient(app) as client:
        yield client


def test_proxy_settings_and_model_lifecycle(client, monkeypatch):
    assert client.get('/api/llm/proxy').json() == {'proxy_url': None}
    payload = dict(provider='openai', model='gpt-test', use_proxy=True)
    assert client.post('/api/llm/configs', json=payload).status_code == 422
    assert client.put('/api/llm/proxy', json={'proxy_url': ' http://127.0.0.1:7890/ '}).json() == {'proxy_url': 'http://127.0.0.1:7890'}
    config = client.post('/api/llm/configs', json=payload).json()
    path = f"/api/llm/configs/{config['id']}"
    assert config['use_proxy'] is True
    assert client.put(path, json={'display_name': 'Keep proxy'}).json()['use_proxy'] is True
    assert client.put(path, json={'use_proxy': None}).status_code == 422
    assert client.put(path, json={'provider': 'custom'}).status_code == 422
    assert client.put('/api/llm/config', json={'provider': 'openai', 'model': 'gpt-test'}).json()['use_proxy'] is True
    assert client.post(path + '/test').status_code == 200  # snapshot keeps opt-in
    assert client.put('/api/llm/proxy', json={'proxy_url': None}).status_code == 409
    captured = []
    monkeypatch.setattr('app.llm.routes._list_remote_models', lambda *a, proxy_url=None: captured.append(proxy_url) or ['gpt-test'])
    probe = {'config_id': config['id'], 'api_base': 'http://127.0.0.1:12345/v1'}
    assert client.post('/api/llm/models-probe', json=probe).status_code == 200
    assert captured[-1] == 'http://127.0.0.1:7890'
    assert client.post('/api/llm/models-probe', json={**probe, 'use_proxy': False}).status_code == 200
    assert captured[-1] is None
    client.put('/api/llm/proxy', json={'proxy_url': 'socks5://127.0.0.1:1080'})
    client.post('/api/llm/models-probe', json=probe)
    assert captured[-1] == 'socks5://127.0.0.1:1080'
    assert client.put(path, json={'use_proxy': False}).json()['use_proxy'] is False
    assert client.put('/api/llm/proxy', json={'proxy_url': None}).status_code == 200


@pytest.mark.parametrize('url', ['ftp://127.0.0.1:8', 'http://127.0.0.1', 'http://127.0.0.1:0', 'http://127.0.0.1:65536',
                                'http://u:p@localhost:9', 'http://localhost:9/path', 'http://localhost:9?x=1',
                                'http://localhost:9#x', 'http://local host:9', 'http://localhost:\n9'])
def test_invalid_proxy_urls(url):
    with pytest.raises(ValidationError):
        ProxySettings(proxy_url=url)


@pytest.mark.parametrize('url', ['http://127.0.0.1:7890', 'https://localhost:7890', 'socks5://127.0.0.1:1080', 'socks5h://[::1]:1080'])
def test_valid_proxy_urls_and_transport_construction(url):
    assert ProxySettings(proxy_url=url).proxy_url == url
    with completion_client('anthropic', url, {'timeout': 5}) as client:
        http = client.client
        assert not http.is_closed
    assert http.is_closed


@contextmanager
def fake_server():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, body):
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            calls.append(('GET', self.path))
            self.respond({'data': [{'id': 'local-test-model'}]})

        def do_CONNECT(self):
            calls.append(('CONNECT', self.path))
            self.send_error(502, 'Synthetic tunnel rejection')

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            calls.append(('POST', self.path))
            answer = '{"status":"ok"}'
            if 'generateContent' in self.path:
                self.respond({'candidates': [{'content': {'role': 'model', 'parts': [{'text': answer}]}, 'finishReason': 'STOP', 'index': 0}], 'usageMetadata': {'promptTokenCount': 3, 'candidatesTokenCount': 4, 'totalTokenCount': 7}})
            elif '/messages' in self.path:
                self.respond({'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'model': 'claude-3-haiku-20240307', 'content': [{'type': 'text', 'text': answer}], 'stop_reason': 'end_turn', 'usage': {'input_tokens': 3, 'output_tokens': 4}})
            else:
                self.respond({'id': 'chatcmpl_fixture', 'object': 'chat.completion', 'created': 1, 'model': 'gpt-test', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': answer}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 3, 'completion_tokens': 4, 'total_tokens': 7}})
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def local_network(monkeypatch):
    monkeypatch.setenv('LITELLM_LOCAL_MODEL_COST_MAP', 'True')
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY', '*')
    original = socket.socket.connect
    def connect(sock, address):
        if isinstance(address, tuple) and address[0] not in {'127.0.0.1', '::1'}:
            raise AssertionError('External network is forbidden in proxy tests')
        return original(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', connect)


@pytest.mark.parametrize('provider,model', [('openai', 'gpt-test'), ('anthropic', 'claude-3-haiku-20240307'), ('gemini', 'gemini-2.0-flash')])
def test_real_litellm_routes_concurrent_models_independently(provider, model, local_network):
    # The local proxy responds itself. It never forwards to an external service.
    with fake_server() as (target, direct_calls), fake_server() as (proxy, proxy_calls):
        def run(use_proxy):
            config = SimpleNamespace(provider=provider, model=model, api_base=target + '/v1',
                                     temperature=0.2, timeout_seconds=5, credential_id='fixture',
                                     use_proxy=use_proxy, proxy_url=proxy)
            store = FakeCredentialStore()
            store.set('fixture', 'synthetic-key')
            return LLMService(config, store).structured_extract('Return JSON', 'status ok', Answer)
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert all(result.status == 'ok' for result in pool.map(run, [False, True]))
        assert len(direct_calls) == len(proxy_calls) == 1
        assert direct_calls[0][1].startswith('/v1')
        assert proxy_calls[0][1].startswith(target + '/v1')
        assert _list_remote_models(provider, target + '/v1', 'synthetic-key', 5, proxy) == ['local-test-model']
        assert _list_remote_models(provider, target + '/v1', 'synthetic-key', 5) == ['local-test-model']
        assert len(direct_calls) == len(proxy_calls) == 2


def test_missing_or_failed_proxy_does_not_fall_back(local_network):
    with fake_server() as (target, calls):
        config = SimpleNamespace(provider='openai', model='gpt-test', api_base=target + '/v1', temperature=0.2,
                                 timeout_seconds=1, credential_id=None, use_proxy=True, proxy_url=None)
        with pytest.raises(LLMConfigurationError):
            LLMService(config, FakeCredentialStore()).structured_extract('JSON', 'status ok', Answer)
        del config.proxy_url
        with pytest.raises(LLMConfigurationError):
            LLMService(config, FakeCredentialStore()).structured_extract('JSON', 'status ok', Answer)
        config.proxy_url = 'http://127.0.0.1:1'
        with pytest.raises(LLMError):
            LLMService(config, FakeCredentialStore()).structured_extract('JSON', 'status ok', Answer)
        assert calls == []


def test_https_uses_proxy_connect_tunnel(local_network):
    with fake_server() as (proxy, calls):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            _list_remote_models('openai', 'https://127.0.0.1:1/v1', 'synthetic-key', 1, proxy)
        assert calls == [('CONNECT', '127.0.0.1:1')]


def test_migration_preserves_existing_models_and_defaults_direct(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setattr(settings, 'database_url', url)
    config = Config('alembic.ini')
    command.upgrade(config, '0017_status_and_model_health')
    engine = create_engine(url)
    try:
        with engine.begin() as db:
            db.execute(text("INSERT INTO llm_configurations (id, provider, model, temperature, timeout_seconds, updated_at, display_name, enabled, created_at) VALUES (1, 'openai', 'existing', 0.2, 60, CURRENT_TIMESTAMP, 'Existing', 1, CURRENT_TIMESTAMP)"))
        command.upgrade(config, 'head')
        with engine.begin() as db:
            assert db.execute(text('SELECT model, use_proxy FROM llm_configurations')).one() == ('existing', 0)
            db.execute(text("INSERT INTO llm_proxy_settings (id, proxy_url) VALUES (1, 'http://127.0.0.1:7890')"))
        with pytest.raises(RuntimeError, match='Clear proxy'):
            command.downgrade(config, '0017_status_and_model_health')
        with engine.begin() as db:
            db.execute(text('UPDATE llm_proxy_settings SET proxy_url = NULL'))
        command.downgrade(config, '0017_status_and_model_health')
        command.upgrade(config, 'head')
    finally:
        engine.dispose()
