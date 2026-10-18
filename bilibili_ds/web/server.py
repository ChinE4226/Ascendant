"""HTTP server lifecycle and optional browser launch."""

from __future__ import annotations

from argparse import Namespace
from http.server import ThreadingHTTPServer
import signal

from bilibili_ds.browser import open_browser
from bilibili_ds.web.routes import BilibiliDataScienceHandler


class DashboardHTTPServer(ThreadingHTTPServer):
    # Browsers request the module graph in bursts; the default backlog of five
    # can reset local asset connections while the worker is accepting them.
    request_queue_size = 64


def create_server(host: str, start_port: int, retries: int) -> tuple[ThreadingHTTPServer, int]:
    last_error: OSError | None = None
    for port in range(start_port, start_port + max(retries, 0) + 1):
        try:
            server = DashboardHTTPServer((host, port), BilibiliDataScienceHandler)
            return server, server.server_port
        except OSError as exc:
            last_error = exc
            if exc.errno not in (48, 98, 10048):
                raise
            print(f"Port {port} is already in use.")
            continue
    raise OSError(f"Could not bind {host}:{start_port}-{start_port + retries}") from last_error


def main(args: Namespace) -> None:
    server, port = create_server(args.host, args.port, args.port_retries)
    url = f"http://{args.host}:{port}"
    previous_handlers = {}
    from bilibili_ds.web.tracking import TrackingWorker
    tracking_worker = TrackingWorker()

    def request_stop(signum, frame):
        raise KeyboardInterrupt

    # A stable launcher has no supervisor: Terminal close must run cleanup here.
    for signum in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
        if signum is not None:
            previous_handlers[signum] = signal.signal(signum, request_stop)
    try:
        tracking_worker.start()
        print(f"Serving Ascendant web UI at {url}")
        if args.open_browser:
            open_browser(url, args.browser)
        print("Press Ctrl+C to stop the server.")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web server.")
    finally:
        tracking_worker.stop()
        server.server_close()
        from bilibili_ds.distributed.coordinator import COORDINATOR
        COORDINATOR.stop()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
