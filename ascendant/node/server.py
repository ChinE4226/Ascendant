"""Local-only node GUI; all fetching connections go outward to the coordinator."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlparse

from ascendant.distributed.protocol import json_request, local_operator, coordinator_url

ASSETS = Path(__file__).resolve().parent / 'assets'


class NodeGUIHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_bytes(self, body, content_type, status=200):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, status=200):
        self.send_bytes(json.dumps(data, ensure_ascii=False).encode(), 'application/json; charset=utf-8', status)

    def do_GET(self):
        try:
            local_operator(self)
            path = urlparse(self.path).path
            assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/node.css': ('node.css', 'text/css; charset=utf-8'),
                      '/node.js': ('node.js', 'text/javascript; charset=utf-8')}
            if path in assets:
                name, mime = assets[path]
                self.send_bytes((ASSETS / name).read_bytes(), mime)
            elif path == '/api/status':
                # Keep the service identifier compatible with existing node launchers.
                self.send_json({'service': 'Bilibili fetching node', **self.server.worker.snapshot()})
            elif path == '/favicon.ico':
                self.send_bytes(b'', 'image/x-icon', 204)
            else:
                self.send_json({'error': 'Not found.'}, 404)
        except (ValueError, OSError) as exc:
            self.send_json({'error': str(exc)}, getattr(exc, 'status', 400))

    def do_POST(self):
        try:
            local_operator(self)
            json_request(self)
            length = int(self.headers.get('Content-Length') or 0)
            if not 0 < length <= 65536:
                raise ValueError('The control request is too large or empty.')
            self.connection.settimeout(10)
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Use a JSON object for node controls.')
            path = urlparse(self.path).path
            if path == '/api/connect':
                result = self.server.worker.connect(data)
            elif path == '/api/check-connection':
                result = self.server.worker.health_checker(coordinator_url(data.get('url')))
            elif path == '/api/control':
                result = self.server.worker.control(data.get('action'))
            else:
                self.send_json({'error': 'Not found.'}, 404)
                return
            self.send_json(result)
        except (ValueError, OSError) as exc:
            self.send_json({'error': str(exc)}, getattr(exc, 'status', 400))


class NodeGUIServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32
