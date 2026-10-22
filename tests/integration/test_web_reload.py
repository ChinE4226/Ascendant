import json
import os
from pathlib import Path
import py_compile
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import Request, urlopen

from ascendant.web.reload import source_snapshot


ROOT = Path(__file__).resolve().parents[2]


class ReloadTests(unittest.TestCase):
    def test_watched_sources_and_ignored_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ("ascendant", "static", "templates", ".runtime", "objects", ".venv"):
                (root / folder).mkdir()
            for name in ("web_server.py", "ascendant/analysis.py", "static/app.js", "templates/page.html"):
                (root / name).write_text("initial")
            before = source_snapshot(root)
            backend = source_snapshot(root, backend_only=True)
            self.assertEqual(len(backend), 2)
            self.assertEqual(len(before), 4)
            for name in (".runtime/cache.py", ".venv/lib.py", "objects/creators.json"):
                (root / name).write_text("ignored")
            self.assertEqual(source_snapshot(root), before)
            (root / "static/app.js").write_text("changed content")
            self.assertNotEqual(source_snapshot(root), before)
            self.assertEqual(source_snapshot(root, backend_only=True), backend)
            (root / "ascendant/analysis.py").unlink()
            self.assertNotIn("ascendant/analysis.py", source_snapshot(root))

    def test_real_server_reload_and_error_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "objects").mkdir()
            for name in ("web_server.py", "web_reload.py"):
                shutil.copy2(ROOT / name, root / name)
            shutil.copytree(ROOT / "ascendant", root / "ascendant", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(ROOT / "templates", root / "templates")
            shutil.copytree(ROOT / "static", root / "static")
            # Exercise reload/lifecycle independently of Thunderbolt hardware.
            with (root / 'ascendant/distributed/network.py').open('a') as fixture:
                fixture.write("\n# Offline integration fixture: use loopback for the connection service.\ndef thunderbolt_address():\n    return '127.0.0.1'\n")
            config_path = root / "ascendant/config.py"
            stamp = config_path.stat()
            py_compile.compile(str(config_path), doraise=True)
            config_path.write_text(config_path.read_text().replace("DEFAULT_REQUEST_FREQUENCY = 4.0", "DEFAULT_REQUEST_FREQUENCY = 8.0"))
            os.utime(config_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            (root / "objects/creators.json").write_text('{"creators": []}')
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            base = f"http://127.0.0.1:{port}"

            def request(path, payload=None):
                body = json.dumps(payload).encode() if payload is not None else None
                req = Request(base + path, data=body, headers={'Content-Type': 'application/json'})
                with urlopen(req, timeout=1) as response:
                    return response.read().decode()

            def wait_for(check):
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    try:
                        result = check()
                        if result:
                            return result
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.1)
                self.fail("Timed out waiting for local reload: " + log_path.read_text())

            log_path = root / "server.log"
            with log_path.open("w") as log:
                process = subprocess.Popen(
                    [sys.executable, "-u", str(root / "web_server.py"), "--port", str(port), "--port-retries", "0"],
                    cwd=root, stdout=log, stderr=log, start_new_session=True,
                )
                try:
                    first = wait_for(lambda: json.loads(request("/api/dev-version")))
                    self.assertTrue(first["token"])
                    self.assertEqual(json.loads(request("/api/health"))["request_frequency"], 8.0)
                    self.assertIn(first["token"], request("/"))
                    request('/api/request-frequency', {'value': 2})
                    with socket.socket() as probe:
                        probe.bind(('127.0.0.1', 0))
                        node_port = probe.getsockname()[1]
                    request('/api/nodes/service', {'action': 'start', 'port': node_port})
                    template = root / "templates/dashboard.html"
                    source = template.read_text()
                    template.write_text(source.replace("Single Video Lookup", "Live Reload Verified"))
                    wait_for(lambda: json.loads(request("/api/dev-version"))["ui_revision"] != first["ui_revision"])
                    self.assertIn("Live Reload Verified", request("/"))
                    second = json.loads(request("/api/dev-version"))
                    self.assertEqual(second['token'], first['token'], 'HTML edits must preserve the worker')
                    stylesheet = root / 'static/css/dashboard.css'
                    stylesheet.write_text(stylesheet.read_text() + '\n/* Live stylesheet test. */\n')
                    wait_for(lambda: json.loads(request('/api/dev-version'))['css_revision'] != first['css_revision'])
                    self.assertEqual(json.loads(request('/api/dev-version'))['token'], first['token'])
                    self.assertEqual(json.loads(request('/api/health'))['request_frequency'], 2)
                    self.assertTrue(json.loads(request('/api/nodes'))['running'], 'Interface edits keep node connections')
                    (root / "objects/creators.json").write_text('{"creators": [], "changed": true}')
                    updated = json.loads(request("/api/dev-version"))
                    self.assertEqual(updated["token"], second["token"])
                    self.assertNotEqual(updated["creators_revision"], second["creators_revision"])
                    config_path = root / "ascendant/config.py"
                    config_path.write_text(config_path.read_text() + "\n# Reload extracted modules too.\n")
                    wait_for(lambda: json.loads(request("/api/dev-version"))["token"] != second["token"])
                    self.assertEqual(json.loads(request('/api/health'))['request_frequency'], 8)
                    self.assertFalse(json.loads(request('/api/nodes'))['running'], 'Python development reload resets the worker')
                    second = json.loads(request("/api/dev-version"))
                    worker_source = (root / "ascendant/web/server.py").read_text()
                    (root / "ascendant/web/server.py").write_text("def broken(:\n")
                    wait_for(lambda: "SyntaxError" in log_path.read_text())
                    self.assertIsNone(process.poll(), "Watcher must survive a broken worker")
                    (root / "ascendant/web/server.py").write_text(worker_source)
                    template.write_text(source)
                    wait_for(lambda: json.loads(request("/api/dev-version"))["token"] != second["token"])
                    self.assertIn("Single Video Lookup", request("/"))
                finally:
                    process.send_signal(signal.SIGINT)
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                with socket.socket() as probe:
                    self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)


if __name__ == "__main__":
    unittest.main()
