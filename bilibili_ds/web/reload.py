"""Standard-library development supervisor for the local dashboard."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import uuid


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Ascendant web server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--port-retries", default=10, type=int)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--browser", default="chrome", choices=["chrome", "default", "none"])
    parser.add_argument("--no-reload", action="store_true", help="Disable automatic Python restarts; browser source updates remain enabled.")
    return parser.parse_args()


def source_snapshot(root: Path, *, backend_only=False) -> dict[str, tuple[int, int]]:
    paths = [root / "web_server.py", root / "web_reload.py"]
    for folder in (('bilibili_ds',) if backend_only else ("bilibili_ds", "static", "templates")):
        paths.extend((root / folder).rglob("*"))
    result = {}
    for path in paths:
        if "__pycache__" in path.parts or path.suffix not in {".py", ".html", ".css", ".js"}:
            continue
        try:
            stat = path.stat()
            if path.is_file():
                result[str(path.relative_to(root))] = (stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            pass
    return result


def available_port(host: str, start: int, retries: int) -> int:
    for port in range(start, start + max(retries, 0) + 1):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
                return probe.getsockname()[1]
            except OSError as exc:
                if exc.errno not in (48, 98, 10048):
                    raise
    raise OSError(f"No available port in {start}-{start + retries}.")


def stop_worker(worker: subprocess.Popen) -> None:
    if worker.poll() is None:
        worker.terminate()
        try:
            worker.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()


def run(args: argparse.Namespace, root: Path) -> None:
    port = available_port(args.host, args.port, args.port_retries)
    command = [sys.executable, "-B", "-u", str(root / "web_server.py"),
               "--no-reload", "--host", args.host, "--port", str(port), "--port-retries", "0"]
    snapshot = source_snapshot(root, backend_only=True)
    worker = None
    first_start = True
    print(f"Auto-reload enabled at http://{args.host}:{port}. Save a source file to update.", flush=True)
    previous_handlers = {}

    def request_stop(signum, frame):
        raise KeyboardInterrupt

    # Terminal close (SIGHUP) and normal termination must stop the worker,
    # just like Control+C, even if only the supervisor receives the signal.
    for signum in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
        if signum is not None:
            previous_handlers[signum] = signal.signal(signum, request_stop)
    try:
        while True:
            token = uuid.uuid4().hex
            # -B prevents new bytecode, while this fresh prefix also avoids reading
            # existing caches after same-second, same-size edits to any module.
            env = dict(os.environ, BILIBILI_RELOAD_TOKEN=token, BILIBILI_DEV_RELOAD='1',
                       PYTHONPYCACHEPREFIX=str(root / ".runtime" / "reload-bytecode" / token))
            launch = command.copy()
            if first_start and args.open_browser:
                launch.extend(["--open-browser", "--browser", args.browser])
            worker = subprocess.Popen(launch, cwd=root, env=env)
            first_start = False
            reported_exit = False
            while True:
                time.sleep(0.4)
                current = source_snapshot(root, backend_only=True)
                if current != snapshot:
                    # Wait for a quiet save interval, including atomic editor writes.
                    while True:
                        time.sleep(0.4)
                        settled = source_snapshot(root, backend_only=True)
                        if settled == current:
                            break
                        current = settled
                    snapshot = current
                    print("Python source changed. Restarting the dashboard...", flush=True)
                    break
                if worker.poll() is not None and not reported_exit:
                    print("Server stopped. Fix the source and save to retry.", flush=True)
                    reported_exit = True
            stop_worker(worker)
    except KeyboardInterrupt:
        print("\nStopping dashboard and file watcher.", flush=True)
    finally:
        try:
            if worker is not None:
                stop_worker(worker)
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
