import importlib.util
import json
from http.server import HTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


@pytest.fixture
def preview():
    filename = Path(__file__).parents[1] / 'scripts/dev/workspace_layout_preview.py'
    spec = importlib.util.spec_from_file_location('layout_preview', filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = HTTPServer(('127.0.0.1', 0), module.Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_default_preview_loads_real_event_renderer_with_synthetic_data(preview):
    _, base = preview
    with urlopen(base + '/events') as response:
        html = response.read().decode('utf-8')
    assert '<script src="/static/js/pages/events.js" defer></script>' in html
    assert '/static/app.js' not in html
    with urlopen(base + '/static/js/pages/events.js') as response:
        assert 'association-status' in response.read().decode('utf-8')
    with urlopen(base + '/api/recruitment-events') as response:
        events = json.load(response)
    assert {event['association_status'] for event in events} == {'unmatched', 'suggested', 'confirmed', 'dismissed'}


def test_preview_is_read_only_and_confines_static_files(preview):
    server, base = preview
    server.interactive = True
    with urlopen(base + '/') as response:
        assert '/static/js/pages/tracker.js' in response.read().decode('utf-8')
    with urlopen(base + '/static/js/shared/api.js') as response:
        assert response.status == 200
    for path in ('/static/../../README.md', '/static/%2e%2e/%2e%2e/README.md', '/api/llm/configs'):
        with pytest.raises(HTTPError) as error:
            urlopen(base + path)
        assert error.value.code == 404
    with pytest.raises(HTTPError) as error:
        urlopen(Request(base + '/api/recruitment-events', data=b'{}', method='POST'))
    assert error.value.code == 501
