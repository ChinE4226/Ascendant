"""Exercise double-click commands without downloading or installing anything."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
COMMANDS = ("start-main.command", "start-node.command", "start-web.command", "setup-main.command", "setup-node.command")


@unittest.skipUnless(os.name == "posix", "The Mac commands require a POSIX shell")
class MacLauncherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="bilibili launcher ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "Source with spaces"
        self.root.mkdir()
        (self.root / "scripts").mkdir()
        for name in COMMANDS:
            shutil.copy2(ROOT / name, self.root / name)
        shutil.copy2(ROOT / "scripts/mac_launcher.sh", self.root / "scripts/mac_launcher.sh")
        for name in ("requirements.txt", "requirements-node.txt"):
            shutil.copy2(ROOT / name, self.root / name)
        self.calls_file = self.root / "calls.jsonl"
        self.ready_file = self.root / "dependencies.ready"
        self.fake_bin = self.root / "fake bin"
        self.fake_bin.mkdir()
        self.fake_python = self.fake_bin / "python3"
        # A small interpreter stand-in lets us observe setup and launch behavior
        # while keeping every file and simulated installation inside this fixture.
        self.fake_python.write_text(f"#!{sys.executable}\n" + '''import json
import os
from pathlib import Path
import shutil
import sys

args = sys.argv[1:]
entry = {'python': sys.argv[0], 'args': args, 'cwd': os.getcwd(),
         'bytecode': os.environ.get('PYTHONDONTWRITEBYTECODE'), 'runtime': os.environ.get('BILIBILI_RUNTIME_DIR')}
if '-' in args:
    entry['stdin'] = sys.stdin.read()
with open(os.environ['FAKE_LAUNCH_LOG'], 'a') as output:
    output.write(json.dumps(entry) + '\\n')
ready = Path(os.environ['FAKE_DEPENDENCIES'])
if '-c' in args:
    sys.exit(int(os.environ.get('FAKE_VERSION_STATUS', '0')))
if '-' in args:
    sys.exit(0 if ready.exists() else 1)
if '-m' in args and args[args.index('-m') + 1] == 'venv':
    destination = Path(args[-1]) / 'bin/python'
    destination.parent.mkdir(parents=True)
    shutil.copy2(sys.argv[0], destination)
elif '-m' in args and args[args.index('-m') + 1] == 'pip':
    ready.touch()
''')
        self.fake_python.chmod(0o755)
        self.env = {**os.environ, "PATH": str(self.fake_bin) + os.pathsep + "/usr/bin:/bin",
                    "BILIBILI_ENV_DIR": str(self.root / '.venv'), "BILIBILI_RUNTIME_DIR": str(self.root / '.runtime'),
                    "FAKE_LAUNCH_LOG": str(self.calls_file), "FAKE_DEPENDENCIES": str(self.ready_file)}

    def environment(self, name):
        directory = self.root / name / "bin"
        directory.mkdir(parents=True)
        interpreter = directory / "python"
        shutil.copy2(self.fake_python, interpreter)
        return interpreter

    def run_command(self, name, *args):
        return subprocess.run([str(self.root / name), *args], cwd=self.root.parent,
                              env=self.env, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=10)

    def calls(self):
        return [json.loads(line) for line in self.calls_file.read_text().splitlines()]

    def install_calls(self):
        return [call for call in self.calls() if "pip" in call["args"] or "venv" in call["args"]]

    def test_launches_from_source_path_with_spaces_and_prefers_project_environment(self):
        preferred = self.environment(".venv")
        self.environment("venv")
        self.ready_file.touch()
        for mode, args in (("main", ("--port", "9123", "--browser", "none")),
                           ("node", ("--port", "9124", "--no-browser"))):
            with self.subTest(mode=mode):
                self.calls_file.unlink(missing_ok=True)
                result = self.run_command(f"start-{mode}.command", *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = self.calls()
                self.assertTrue(all(call["python"] == str(preferred) for call in calls))
                self.assertTrue(all(Path(call["cwd"]) == self.root.resolve() for call in calls))
                self.assertTrue(all(call["bytecode"] == "1" for call in calls))
                launch = calls[-1]["args"]
                self.assertEqual(launch[-len(args):], list(args))
                self.assertIn("ascendant.web" if mode == "main" else "ascendant.node", launch)
                self.assertIn("--open-browser", launch)
                self.assertEqual(launch[launch.index("--browser") + 1], "chrome")
                if mode == "main":
                    self.assertIn("--no-reload", launch)
                    self.assertEqual(launch[launch.index("--host") + 1], "127.0.0.1")
                self.assertIn("press Control+C", result.stdout)
                self.assertEqual(self.install_calls(), [])

    def test_main_and_development_share_interpreter_runtime_and_credentials(self):
        preferred = self.environment('.venv')
        self.ready_file.touch()
        launchers = []
        for command in ('start-main.command', 'start-web.command'):
            self.calls_file.unlink(missing_ok=True)
            result = self.run_command(command, '--browser', 'none')
            self.assertEqual(result.returncode, 0, result.stderr)
            launchers.append(self.calls()[-1])
        self.assertEqual([entry['python'] for entry in launchers], [str(preferred)] * 2)
        self.assertEqual([entry['runtime'] for entry in launchers], [str(self.root / '.runtime')] * 2)
        self.assertIn('--no-reload', launchers[0]['args'])
        self.assertNotIn('--no-reload', launchers[1]['args'])
        self.assertEqual(self.install_calls(), [])

    def test_existing_legacy_signin_is_reused_without_copying_secrets(self):
        self.environment('.venv')
        self.ready_file.touch()
        local = self.root / 'local runtime'
        script = self.root / 'scripts/mac_launcher.sh'
        script.write_text(script.read_text().replace('LAUNCH_RUNTIME_DEFAULT="$HOME/Library/Application Support/BilibiliDataScience/runtime"',
                                                    f'LAUNCH_RUNTIME_DEFAULT="{local}"'))
        runtime = self.root / '.runtime'
        runtime.mkdir()
        cookie = runtime / 'bilibili_credential.json'
        cookie.write_text('{"sessdata":"fixture-secret"}')
        self.env.pop('BILIBILI_RUNTIME_DIR')
        for command in ('start-main.command', 'start-web.command'):
            result = self.run_command(command, '--browser', 'none')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.calls()[-1]['runtime'], str(runtime))
            self.assertNotIn('fixture-secret', result.stdout + result.stderr)
        self.assertFalse(local.exists(), 'Startup must not duplicate the saved sign-in')
        self.assertEqual(cookie.read_text(), '{"sessdata":"fixture-secret"}')
        local.mkdir()
        (local / 'bilibili_credential.json').write_text('{"sessdata":"newer-local-fixture"}')
        self.run_command('start-main.command', '--browser', 'none')
        self.assertEqual(self.calls()[-1]['runtime'], str(local))

    def test_reuses_legacy_venv(self):
        legacy = self.environment("venv")
        self.ready_file.touch()
        result = self.run_command("start-node.command", "--no-browser")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(call["python"] == str(legacy) for call in self.calls()))
        self.assertFalse((self.root / ".venv").exists())

    def test_missing_dependencies_require_explicit_setup_without_installing(self):
        for mode in ("main", "node"):
            with self.subTest(mode=mode):
                self.calls_file.unlink(missing_ok=True)
                result = self.run_command(f"start-{mode}.command")
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"setup-{mode}.command", result.stderr)
                self.assertEqual(self.install_calls(), [])
                self.assertFalse((self.root / ".venv").exists())
                self.assertFalse(any("ascendant.web" in call["args"] or "ascendant.node" in call["args"]
                                     for call in self.calls()))

    def test_old_project_python_is_reported_without_replacing_the_environment(self):
        interpreter = self.environment(".venv")
        original = interpreter.read_bytes()
        self.env["FAKE_VERSION_STATUS"] = "1"
        result = self.run_command("setup-node.command")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Python 3.10", result.stderr)
        self.assertIn("setup-node.command", result.stderr)
        self.assertEqual(interpreter.read_bytes(), original)
        self.assertEqual(self.install_calls(), [])

    def test_first_setup_installs_only_the_selected_requirements_without_cache(self):
        for mode, requirements in (("main", "requirements.txt"), ("node", "requirements-node.txt")):
            with self.subTest(mode=mode):
                shutil.rmtree(self.root / ".venv", ignore_errors=True)
                self.ready_file.unlink(missing_ok=True)
                self.calls_file.unlink(missing_ok=True)
                result = self.run_command(f"setup-{mode}.command")
                self.assertEqual(result.returncode, 0, result.stderr)
                operations = self.install_calls()
                self.assertEqual(len(operations), 2)
                self.assertIn("venv", operations[0]["args"])
                install = operations[1]["args"]
                self.assertIn("--no-cache-dir", install)
                self.assertIn("--no-compile", install)
                self.assertEqual(install[install.index("-r") + 1], str(self.root / requirements))
                self.assertIn(f"start-{mode}.command", result.stdout)
                self.assertTrue((self.root / ".venv/bin/python").is_file())
                self.calls_file.unlink()
                repeated = self.run_command(f"setup-{mode}.command")
                self.assertEqual(repeated.returncode, 0, repeated.stderr)
                self.assertEqual(self.install_calls(), [])


if __name__ == "__main__":
    unittest.main()
