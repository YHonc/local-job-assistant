from datetime import datetime, timedelta, timezone
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.database.models import Application, LLMHealthState
from app.email_sync.progress_models import ApplicationStatusHistory


class MemoryCredentials:
    def __init__(self): self.values = {}
    def get(self, key): return self.values.get(key)
    def set(self, key, value): self.values[key] = value
    def delete(self, key): self.values.pop(key, None)


@pytest.fixture
def client(tmp_path):
    def completion(**kwargs):
        assert kwargs['max_tokens'] == 64
        assert 0 < kwargs['timeout'] <= 30
        assert kwargs['num_retries'] == 0
        return {'choices': [{'message': {'content': '{"status":"ok"}'}}], 'usage': {'prompt_tokens': 7, 'completion_tokens': 3}}
    app = create_app(f"sqlite:///{(tmp_path / 'checks.db').as_posix()}", create_tables=True,
                     credential_store=MemoryCredentials(), llm_completion=completion, enable_scheduler=False)
    with TestClient(app) as test_client:
        yield test_client


def job(client, name='软件开发', application=None, **fields):
    response = client.post('/api/workbench/jobs', json={'company': {'name': name}, 'job': {'title': name, **fields},
                                                      'application': application, 'idempotency_key': name})
    assert response.status_code == 201, response.text
    return response.json()['item']


def status(client, item, value):
    return client.post('/api/workbench/jobs/status', json={'items': [{'job_id': item['id'], 'expected_application_version': item['application']['version'] if item['application'] else None}], 'status': value})


def model(client, **kwargs):
    response = client.post('/api/llm/configs', json={'provider': 'openai', 'model': 'synthetic-test', 'display_name': '合成模型', **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def test_status_create_conflict_undo_and_lossless_legacy(client):
    item = job(client)
    saved = status(client, item, '1面')
    assert saved.status_code == 200, saved.text
    saved = saved.json()
    assert saved['items'][0]['application']['status'] == '1面'
    assert status(client, item, '已投递').status_code == 409
    undone = client.post(f"/api/workbench/status-changes/{saved['changes'][0]['id']}/undo")
    assert undone.json()['items'][0]['application']['status'] == '待投递'
    assert client.post(f"/api/workbench/status-changes/{saved['changes'][0]['id']}/undo").status_code == 409
    with client.app.state.session_factory() as db:
        history = list(db.scalars(select(ApplicationStatusHistory).where(ApplicationStatusHistory.application_id == saved['items'][0]['application']['id'])))
        assert len(history) == 2  # Initial explicit progress plus exactly one undo.
        assert history[-1].source == 'undo'
    legacy = job(client, '历史Offer', {'status': 'Offer'})
    assert legacy['application']['status'] == 'Offer'
    reviewed = status(client, legacy, '拒绝').json()
    undone = client.post(f"/api/workbench/status-changes/{reviewed['changes'][0]['id']}/undo")
    assert undone.json()['items'][0]['application']['status'] == 'Offer'
    alias = job(client, '历史一面', {'status': '一面'})
    assert alias['application']['status'] == '1面'
    assert alias['application']['legacy_status'] == '一面'
    assert client.get('/api/workbench/jobs?application_status=1面').json()['total'] == 1
    assert client.get('/api/workbench/jobs?legacy_status=true').json()['total'] == 1
    with client.app.state.session_factory() as db:
        assert db.get(Application, alias['application']['id']).status == '一面'


def test_bulk_validation_is_atomic_and_all_eight_states_supported(client):
    first, second = job(client, '岗位一'), job(client, '岗位二')
    targets = [{'job_id': first['id'], 'expected_application_version': None}, {'job_id': second['id'], 'expected_application_version': 99}]
    assert client.post('/api/workbench/jobs/status', json={'items': targets, 'status': '已投递'}).status_code == 409
    assert client.get(f"/api/workbench/jobs/{first['id']}").json()['application'] is None
    targets[1]['expected_application_version'] = None
    assert client.post('/api/workbench/jobs/status', json={'items': targets, 'status': '待投递'}).status_code == 200
    item = client.get(f"/api/workbench/jobs/{first['id']}").json()
    for value in client.get('/api/workbench/statuses').json()['values']:
        result = status(client, item, value)
        assert result.status_code == 200, result.text
        item = result.json()['items'][0]
    assert status(client, item, '收藏').status_code == 422


def test_click_rules_never_regress_or_duplicate_and_respect_preferences(client):
    item = job(client)
    payload = {'job_id': item['id'], 'expected_application_version': None, 'action': 'detail'}
    client.put('/api/workbench/delivery-preferences', json={'on_detail': False, 'on_apply': True})
    assert not client.post('/api/workbench/jobs/status-action', json=payload).json()['changes']
    payload['action'] = 'apply'
    result = client.post('/api/workbench/jobs/status-action', json=payload).json()
    assert result['items'][0]['application']['status'] == '已投递'
    assert not client.post('/api/workbench/jobs/status-action', json=payload).json()['changes']
    updated = status(client, result['items'][0], '3面').json()['items'][0]
    payload['expected_application_version'] = updated['application']['version']
    assert client.post('/api/workbench/jobs/status-action', json=payload).json()['items'][0]['application']['status'] == '3面'


def test_direction_facets_and_explicit_override(client):
    first = job(client, '软件开发')
    second = job(client, '软件运维', tags=['方向:业务分析'])
    assert first['direction'] == '后端开发'
    assert second['direction'] == '业务分析'
    filtered = client.get('/api/workbench/jobs?direction=业务分析').json()
    assert [item['id'] for item in filtered['items']] == [second['id']]
    assert {'value': '后端开发', 'count': 1} in filtered['facets']['directions']


def test_health_defaults_persistence_and_usage(client):
    config = model(client, health={'mode': 'custom', 'interval_minutes': 5})
    assert not client.get('/api/llm/health-settings').json()['enabled']
    assert config['health']['mode'] == 'custom'
    response = client.post(f"/api/llm/configs/{config['id']}/test")
    assert response.status_code == 200, response.text
    summary = client.get(f"/api/llm/configs/{config['id']}").json()
    assert summary['health']['status'] == 'passed'
    assert summary['health']['next_test_at'] is None
    assert summary['call_count'] == 1 and summary['total_prompt_tokens'] == 7
    usage = client.get(f"/api/llm/configs/{config['id']}/usage").json()
    assert usage['by_purpose']['test']['total_tokens'] == usage['summary']['total_tokens'] == 10
    assert client.put('/api/llm/health-settings', json={'enabled': True, 'interval_minutes': 0}).status_code == 422
    assert client.put('/api/llm/health-settings', json={'enabled': True, 'interval_minutes': 30}).status_code == 200
    state = client.get(f"/api/llm/configs/{config['id']}").json()['health']
    assert state['next_test_at'] and state['interval_minutes'] == 5


def test_scheduler_skips_excluded_models_resets_restart_and_backs_off(client):
    active = model(client, health={'mode': 'custom', 'interval_minutes': 1})
    excluded = model(client, health={'mode': 'off', 'interval_minutes': 1})
    coordinator = client.app.state.model_health
    client.put('/api/llm/health-settings', json={'enabled': True, 'interval_minutes': 30})
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    with client.app.state.session_factory() as db:
        for id in (active['id'], excluded['id']): db.get(LLMHealthState, id).next_test_at = past
        db.commit()
    coordinator.reset_schedule()
    coordinator.run_once()
    assert client.get(f"/api/llm/configs/{active['id']}").json()['call_count'] == 0
    def failing(**kwargs): raise TimeoutError('private provider response')
    coordinator.completion = failing
    with client.app.state.session_factory() as db:
        db.get(LLMHealthState, active['id']).next_test_at = past
        db.commit()
    coordinator.run_once()
    state = client.get(f"/api/llm/configs/{active['id']}").json()
    assert state['enabled'] and state['health']['status'] == 'failed'
    assert 'private' not in state['health']['error']
    delay = datetime.fromisoformat(state['health']['next_test_at']) - datetime.fromisoformat(state['health']['last_test_at'])
    assert delay == timedelta(minutes=2)
    assert client.get(f"/api/llm/configs/{excluded['id']}").json()['call_count'] == 0


def test_manual_and_automatic_share_non_reentrant_lock(client):
    config = model(client)
    started, release = Event(), Event()
    coordinator = client.app.state.model_health
    original = coordinator.completion
    def slow(**kwargs):
        started.set()
        assert release.wait(5)
        return original(**kwargs)
    coordinator.completion = slow
    errors = []
    def first():
        try: coordinator.test(config['id'])
        except Exception as error: errors.append(error)
    thread = Thread(target=first)
    thread.start()
    try:
        assert started.wait(5)
        assert client.post(f"/api/llm/configs/{config['id']}/test").status_code == 409
    finally:
        release.set()
        thread.join(5)
    assert not errors and not thread.is_alive()
    assert client.get(f"/api/llm/configs/{config['id']}").json()['call_count'] == 1


def test_migration_preserves_legacy_history_and_blocks_lossy_downgrade(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text
    from app.core.config import settings
    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    previous = settings.database_url
    settings.database_url = url
    config = Config('alembic.ini')
    engine = create_engine(url)
    try:
        command.upgrade(config, '0016_email_progress_suggestions')
        with engine.begin() as db:
            db.execute(text("INSERT INTO companies (id, name, created_at) VALUES (1, 'fixture', CURRENT_TIMESTAMP)"))
            db.execute(text("INSERT INTO jobs (id, company_id, title, created_at, updated_at) VALUES (1, 1, 'fixture', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
            db.execute(text("INSERT INTO applications (id, job_id, status, status_updated_at) VALUES (1, 1, 'Offer', CURRENT_TIMESTAMP)"))
            db.execute(text("INSERT INTO application_status_history (application_id, before_status, after_status, source, created_at) VALUES (1, '一面', 'Offer', 'manual', CURRENT_TIMESTAMP)"))
        command.upgrade(config, 'head')
        with engine.connect() as db:
            assert db.scalar(text('SELECT status FROM applications')) == 'Offer'
            assert db.execute(text('SELECT before_status, after_status, result_version FROM application_status_history')).one() == ('一面', 'Offer', None)
            assert 'llm_health_states' in inspect(db).get_table_names()
        command.downgrade(config, '0016_email_progress_suggestions')
        command.upgrade(config, 'head')
        with engine.begin() as db:
            db.execute(text('UPDATE application_status_history SET result_version = 2'))
        with pytest.raises(RuntimeError, match='Cannot discard'):
            command.downgrade(config, '0016_email_progress_suggestions')
    finally:
        settings.database_url = previous
        engine.dispose()
