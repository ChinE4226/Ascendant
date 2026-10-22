"""Check that the foreground macOS launcher stops its worker with Terminal."""

import json
import os
from pathlib import Path
import shutil
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.name == "posix", "The .command launcher is for macOS/POSIX")
class WebLauncherTests(unittest.TestCase):
    def test_foreground_launcher_stops_worker_on_stop(self):
        for stop_signal in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
            with self.subTest(signal=stop_signal), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for name in ("start-web.command", "web_server.py"):
                    shutil.copy2(ROOT / name, root / name)
                (root / 'scripts').mkdir()
                shutil.copy2(ROOT / 'scripts/mac_launcher.sh', root / 'scripts/mac_launcher.sh')
                (root / '.test-env/bin').mkdir(parents=True)
                interpreter = root / '.test-env/bin/python'
                interpreter.write_text('#!/bin/bash\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
                interpreter.chmod(0o755)
                for name in ("ascendant", "static", "templates"):
                    shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
                (root / "objects").mkdir()
                (root / "objects/creators.json").write_text('{"creators": []}')
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", 0))
                    port = probe.getsockname()[1]
                env = {key: value for key, value in os.environ.items() if not key.startswith("BILI")}
                env.update(
                    PATH=str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", ""),
                    MPLCONFIGDIR=str(root / ".runtime/matplotlib"),
                    XDG_CACHE_HOME=str(root / ".runtime/cache"),
                    BILIBILI_ENV_DIR=str(root / '.test-env'),
                    BILIBILI_RUNTIME_DIR=str(root / '.runtime'),
                )
                log_path = root / "server.log"
                with log_path.open("w") as log:
                    process = subprocess.Popen(
                        [str(root / "start-web.command"), "--port", str(port), "--port-retries", "0", "--browser", "none"],
                        cwd=root.parent, env=env, stdin=subprocess.DEVNULL,
                        stdout=log, stderr=log, start_new_session=True,
                    )
                    try:
                        deadline = time.monotonic() + 30
                        while time.monotonic() < deadline:
                            self.assertIsNone(process.poll(), log_path.read_text())
                            try:
                                with urlopen(f"http://127.0.0.1:{port}/api/dev-version", timeout=1) as response:
                                    self.assertTrue(json.load(response)["token"])
                                break
                            except OSError:
                                time.sleep(0.1)
                        else:
                            self.fail("Launcher did not become ready: " + log_path.read_text())
                        self.assertIn("press Control+C", log_path.read_text())
                        # Signal only the launcher, not its process group: cleanup
                        # must stop the worker even without a terminal-wide signal.
                        process.send_signal(stop_signal)
                        self.assertEqual(process.wait(timeout=10), 0, log_path.read_text())
                        self.assertIn("Stopping dashboard and file watcher", log_path.read_text())
                        with socket.socket() as probe:
                            self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)
                    finally:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
