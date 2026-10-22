"""Launch source copies through Mac commands; no browser or Bilibili requests."""

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from threading import Thread
import time
from types import SimpleNamespace
import unittest
from urllib.request import Request, urlopen

from ascendant.node.server import NodeGUIHandler, NodeGUIServer


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.name == "posix", "The Mac commands require a POSIX shell")
class MacLauncherIntegrationTests(unittest.TestCase):
    def source_copy(self, directory):
        root = Path(directory) / "Source with spaces"
        root.mkdir()
        (root / "scripts").mkdir()
        for name in ("start-main.command", "start-node.command"):
            shutil.copy2(ROOT / name, root / name)
        shutil.copy2(ROOT / "scripts/mac_launcher.sh", root / "scripts/mac_launcher.sh")
        for name in ("ascendant", "static", "templates"):
            shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        # These process-lifecycle tests run offline, without a physical cable.
        network = root / 'ascendant/distributed/network.py'
        with network.open('a') as fixture:
            fixture.write("\n# Offline integration fixture: use loopback for the connection service.\ndef thunderbolt_address():\n    return '127.0.0.1'\n")
        (root / "objects").mkdir()
        (root / "objects/creators.json").write_text('{"creators": []}')
        (root / ".venv/bin").mkdir(parents=True)
        # Invoke the test runtime in its original environment rather than copying
        # a virtual environment or downloading dependencies for each fixture.
        interpreter = root / ".venv/bin/python"
        interpreter.write_text("#!/bin/bash\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
        interpreter.chmod(0o755)
        # Capture the macOS browser-opening command without opening a real window.
        (root / ".test-bin").mkdir()
        browser_command = root / ".test-bin/open"
        browser_command.write_text("#!/bin/bash\nprintf '%s\\n' \"$@\" > " + shlex.quote(str(root / "browser-args.txt")) + "\n")
        browser_command.chmod(0o755)
        return root

    def environment(self, root):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("BILI") and key not in {"PYTHONPATH", "PYTHONHOME"}}
        env.update(PYTHONDONTWRITEBYTECODE="1", MPLCONFIGDIR=str(root / ".runtime/matplotlib"),
                   BILIBILI_ENV_DIR=str(root / '.venv'), BILIBILI_RUNTIME_DIR=str(root / '.runtime'),
                   XDG_CACHE_HOME=str(root / ".runtime/cache"),
                   PATH=str(root / ".test-bin") + os.pathsep + env.get("PATH", ""))
        return env

    def request(self, url, data=None):
        request = Request(url, data=json.dumps(data).encode() if data is not None else None,
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=2) as response:
            if response.headers.get_content_type() == "application/json":
                return json.load(response)
            return response.read().decode()

    def assert_port_closed(self, port):
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)

    def test_main_and_node_stop_cleanly_with_terminal_signals(self):
        for mode in ("main", "node"):
            for stop_signal in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
                with self.subTest(mode=mode, signal=stop_signal), tempfile.TemporaryDirectory(prefix="bilibili mac launch ") as directory:
                    root = self.source_copy(directory)
                    log_path = root / "server.log"
                    args = ["--port", "0"]
                    if mode == "main":
                        args += ["--port-retries", "0"]
                    if stop_signal != signal.SIGINT:
                        args += ["--browser", "none"] if mode == "main" else ["--no-browser"]
                    with log_path.open("w") as log:
                        process = subprocess.Popen([str(root / f"start-{mode}.command"), *args],
                                                   cwd=root.parent, env=self.environment(root),
                                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                                   start_new_session=True)
                        try:
                            prefix = "Serving Ascendant web UI at" if mode == "main" else "Ascendant fetching node web GUI:"
                            deadline = time.monotonic() + 30
                            base = None
                            while time.monotonic() < deadline:
                                output = log_path.read_text()
                                self.assertIsNone(process.poll(), output)
                                match = re.search(re.escape(prefix) + r"\s+(http://127\.0\.0\.1:(\d+))", output)
                                if match:
                                    base, port = match.group(1), int(match.group(2))
                                    try:
                                        state = self.request(base + ("/api/nodes" if mode == "main" else "/api/status"))
                                        break
                                    except OSError:
                                        pass
                                time.sleep(.1)
                            else:
                                self.fail("Launcher did not become ready: " + log_path.read_text())
                            self.assertEqual(state["protocol"], 1)
                            if stop_signal == signal.SIGINT:
                                self.assertEqual((root / "browser-args.txt").read_text().splitlines(),
                                                 ["-a", "Google Chrome", base])
                            else:
                                self.assertFalse((root / "browser-args.txt").exists())
                            html = self.request(base + "/")
                            self.assertIn("Fetching nodes" if mode == "main" else "Ascendant fetching node", html)
                            connection_port = None
                            if mode == "main":
                                self.assertFalse(state["running"])
                                with socket.socket() as probe:
                                    probe.bind(("127.0.0.1", 0))
                                    connection_port = probe.getsockname()[1]
                                state = self.request(base + "/api/nodes/service", {"action": "start", "port": connection_port})
                                self.assertTrue(state["running"])
                                self.assertEqual(self.request(f"http://127.0.0.1:{connection_port}/node/health")["protocol"], 1)
                            else:
                                self.assertEqual(state["connection"], "disconnected")
                                self.assertFalse((root / ".runtime").exists())
                            # Signal only the foreground launcher's PID. A wrapper
                            # that left its server detached would fail this check.
                            process.send_signal(stop_signal)
                            self.assertEqual(process.wait(timeout=10), 0, log_path.read_text())
                            self.assert_port_closed(port)
                            if connection_port is not None:
                                self.assert_port_closed(connection_port)
                            self.assertEqual(list(root.rglob("*.pyc")), [])
                        finally:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            process.wait(timeout=10)

    def test_node_entry_point_does_not_import_plotting_dependencies(self):
        with tempfile.TemporaryDirectory(prefix="bilibili mac import ") as directory:
            root = self.source_copy(directory)
            result = subprocess.run([sys.executable, "-B", "-c",
                                     "import sys; import ascendant.node.__main__; import ascendant.web.videos; "
                                     "assert not any(name == 'matplotlib' or name.startswith('matplotlib.') for name in sys.modules); "
                                     "print('Node imports do not need Matplotlib')"],
                                    cwd=root, env=self.environment(root), capture_output=True,
                                    text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("do not need Matplotlib", result.stdout)
            self.assertFalse((root / ".runtime").exists())
            self.assertEqual(list(root.rglob("*.pyc")), [])

    def test_repeated_node_launch_opens_existing_gui_and_preserves_connection(self):
        class LegacyHandler(NodeGUIHandler):
            def send_json(self, data, status=200):
                data.pop('service', None)
                super().send_json(data, status)

        for handler in (NodeGUIHandler, LegacyHandler):
            with self.subTest(handler=handler.__name__), tempfile.TemporaryDirectory(prefix='bilibili node reuse ') as directory:
                root = self.source_copy(directory)
                state = {'protocol': 1, 'connection': 'connected', 'coordinator_enabled': True,
                         'completed': 7, 'node_id': 'existing-node'}
                server = NodeGUIServer(('127.0.0.1', 0), handler)
                server.worker = SimpleNamespace(snapshot=lambda: dict(state))
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f'http://127.0.0.1:{server.server_port}'
                try:
                    result = subprocess.run([str(root / 'start-node.command'), '--port', str(server.server_port),
                                             '--port-retries', '0'], cwd=root.parent, env=self.environment(root),
                                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('already running', result.stdout)
                    self.assertEqual((root / 'browser-args.txt').read_text().splitlines(),
                                     ['-a', 'Google Chrome', base])
                    current = self.request(base + '/api/status')
                    self.assertEqual((current['connection'], current['completed']), ('connected', 7))
                    self.assertFalse((root / '.runtime').exists())
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=3)

    def test_unrelated_busy_port_starts_node_on_another_port(self):
        class UnrelatedHandler(NodeGUIHandler):
            def do_GET(self):
                self.send_json({'protocol': 1, 'service': 'another application'})

        with tempfile.TemporaryDirectory(prefix='bilibili node occupied ') as directory:
            root = self.source_copy(directory)
            blocker = NodeGUIServer(('127.0.0.1', 0), UnrelatedHandler)
            thread = Thread(target=blocker.serve_forever, daemon=True)
            thread.start()
            log_path = root / 'node-start.log'
            log = log_path.open('w')
            process = subprocess.Popen([str(root / 'start-node.command'), '--port', str(blocker.server_port),
                                        '--no-browser'], cwd=root.parent, env=self.environment(root),
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       text=True, start_new_session=True)
            try:
                deadline = time.monotonic() + 15
                base = None
                while time.monotonic() < deadline:
                    output = log_path.read_text()
                    match = re.search(r'Ascendant fetching node web GUI: (http://127\.0\.0\.1:(\d+))', output)
                    if match:
                        base, port = match.group(1), int(match.group(2))
                        break
                    if process.poll() is not None:
                        self.fail('Node stopped while choosing a free port: ' + output)
                    time.sleep(.05)
                self.assertIsNotNone(base)
                self.assertNotEqual(port, blocker.server_port)
                self.assertEqual(self.request(base + '/api/status')['service'], 'Bilibili fetching node')
                self.assertEqual(self.request(f'http://127.0.0.1:{blocker.server_port}/api/status')['service'],
                                 'another application')
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=10), 0)
                self.assert_port_closed(port)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
                log.close()
                blocker.shutdown()
                blocker.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
