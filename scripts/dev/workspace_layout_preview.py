"""Serve a static layout preview without importing or starting the app."""

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote

from jinja2 import Environment, FileSystemLoader


# scripts/dev/workspace_layout_preview.py -> project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREVIEW_HOST = "127.0.0.1"
PREVIEW_PORT = 18766
templates = Environment(
    loader=FileSystemLoader(PROJECT_ROOT / "app/templates"), autoescape=True
)
routes = {
    "/": "tracker.html",
    "/legacy/tracker": "tracker.html",
    "/settings": "settings.html",
    "/events": "events.html",
    "/notifications": "notifications.html",
    "/jobs/174": "job_detail.html",
}

EVENT_FIXTURES = [
    {
        "id": index,
        "association_status": status,
        "event_type": "INTERVIEW",
        "company_name": "示例公司",
        "job_title": "界面检查岗位",
        "ai_summary": "仅用于隔离预览的模拟事件。",
        "candidates": [],
        "application_id": None,
        "confidence": 0.9,
    }
    for index, status in enumerate(("unmatched", "suggested", "confirmed", "dismissed"), 1)
]

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = unquote(self.path.split('?')[0])
        interactive = getattr(self.server, 'interactive', False)
        if path.startswith('/static/'):
            static_root = (PROJECT_ROOT / 'app/static').resolve()
            resource = (static_root / path.removeprefix('/static/')).resolve()
            allowed = interactive or path in {'/static/app.css', '/static/js/pages/events.js'}
            if not allowed or not resource.is_relative_to(static_root) or resource.suffix not in {'.js', '.css'} or not resource.is_file():
                self.send_error(404)
                return
            body = resource.read_bytes()
            content_type = 'text/javascript' if resource.suffix == '.js' else 'text/css'
        elif path == '/api/recruitment-events':
            body = json.dumps(EVENT_FIXTURES, ensure_ascii=False).encode('utf-8')
            content_type = 'application/json; charset=utf-8'
        elif path in routes:
            html = templates.get_template(routes[path]).render(job_id=174)
            if interactive:
                self.send_response(200)
                body = html.encode('utf-8')
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            html = re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S)
            html = html.replace('id="jobDetail" hidden', 'id="jobDetail"')
            for field in ('detailCompany', 'detailTitle', 'detailLocation', 'detailSourceName', 'detailDescription'):
                html = html.replace(f'id="{field}">', f'id="{field}">' + ('布局预览内容' if field != 'detailLocation' else '城市甲、城市乙、城市丙、城市丁'))
            for field in ('detailType', 'detailStatus'):
                html = re.sub(rf'(<select id="{field}"[^>]*>)', r'\1<option>校园招聘（应届校招）</option>', html)
            for field in ('llmConfigBadge', 'emailConfigBadge', 'notificationConfigBadge'):
                html = re.sub(rf'(<span id="{field}"[^>]*>).*?(</span>)', r'\1预览状态\2', html)
            if path == '/events':
                html = html.replace('</body>', '<script src="/static/js/pages/events.js" defer></script></body>')
            html = html.replace('<section id="notificationList" class="notification-list" aria-live="polite"></section>', '<section id="notificationList" class="notification-list"><article class="notification-row"><div>布局预览通知</div><span class="tag">已发送</span></article></section>')
            body = html.encode('utf-8')
            content_type = 'text/html; charset=utf-8'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the isolated Jinja/CSS layout preview")
    parser.add_argument("--host", default=PREVIEW_HOST)
    parser.add_argument("--port", type=int, default=PREVIEW_PORT)
    parser.add_argument("--interactive", action="store_true", help="Keep page scripts for browser tests with intercepted fixture APIs")
    args = parser.parse_args(argv)

    print(f"Layout preview: http://{args.host}:{args.port}/")
    print("Isolated templates/assets and synthetic events only; no FastAPI, SQLite or external services.")
    server = HTTPServer((args.host, args.port), Handler)
    server.interactive = args.interactive
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
