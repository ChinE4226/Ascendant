"""python -m ascendant.node starts the fetching Mac's web GUI."""

import argparse
import errno
import json
import signal
from urllib.request import ProxyHandler, build_opener

from ascendant.browser import open_browser
from ascendant.distributed.worker import NoRedirect, Worker
from ascendant.node.server import NodeGUIHandler, NodeGUIServer


def existing_node(url):
    """Recognize an existing node without pairing or changing its state."""
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(url + '/api/status', timeout=1) as response:
            status = json.loads(response.read(8192))
        if not isinstance(status, dict) or status.get('protocol') != 1:
            return False
        if status.get('service') == 'Bilibili fetching node':
            return True
        # Older running nodes have no service marker. Check both their status
        # shape and HTML title so an unrelated JSON endpoint is not reused.
        if not {'connection', 'coordinator_enabled', 'completed', 'node_id'} <= status.keys():
            return False
        with opener.open(url + '/', timeout=1) as response:
            html = response.read(8192)
            return any(title in html for title in (
                b'<title>Bilibili fetching node</title>',
                b'<title>Bilibili Data Science fetching node</title>',
                b'<title>Ascendant fetching node</title>',
            ))
    except (OSError, ValueError):
        return False


def node_server(port, retries):
    for candidate in range(port, min(port + retries, 65535) + 1):
        url = f'http://127.0.0.1:{candidate}'
        try:
            server = NodeGUIServer(('127.0.0.1', candidate), NodeGUIHandler)
            return server, f'http://127.0.0.1:{server.server_port}'
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise SystemExit(f'Could not start the fetching node: {error}') from None
            if existing_node(url):
                return None, url
    raise SystemExit('No free node GUI port. Close the conflicting app or start with --port followed by an unused port.')


def main():
    parser = argparse.ArgumentParser(description='Run the Ascendant fetching node with a local web interface.')
    parser.add_argument('--port', type=int, default=8011)
    parser.add_argument('--port-retries', type=int, default=10)
    parser.add_argument('--open-browser', action='store_true')
    parser.add_argument('--browser', default='chrome', choices=['chrome', 'default', 'none'])
    parser.add_argument('--no-browser', action='store_true', help='Keep the GUI available without opening a browser.')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535 or not 0 <= args.port_retries <= 100:
        parser.error('Use a port from 0 to 65535 and port-retries from 0 to 100.')
    server, url = node_server(args.port, args.port_retries)
    if server is None:
        print(f'Ascendant fetching node is already running: {url}', flush=True)
        print('Reusing its GUI; its connection and current task are unchanged.', flush=True)
        if args.open_browser and not args.no_browser:
            open_browser(url, args.browser)
        return
    worker = Worker()
    server.worker = worker
    worker.start()

    def stop(signum, frame):
        raise KeyboardInterrupt

    for signum in (signal.SIGTERM, getattr(signal, 'SIGHUP', None)):
        if signum is not None:
            signal.signal(signum, stop)
    print(f'Ascendant fetching node web GUI: {url}', flush=True)
    print('Keep this Terminal open. Pair using the main Mac connection URL and a fresh code.', flush=True)
    if args.open_browser and not args.no_browser:
        open_browser(url, args.browser)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('Stopping fetching node.', flush=True)
    finally:
        server.server_close()
        worker.stop()


if __name__ == '__main__':
    main()
