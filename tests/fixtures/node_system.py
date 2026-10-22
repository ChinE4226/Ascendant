"""Offline three-GUI fixture: main dashboard and two real node runtimes."""

import asyncio
import json
from pathlib import Path
import signal
import sys
from threading import Thread
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ascendant.distributed.coordinator import COORDINATOR
from ascendant.distributed.worker import Worker
from ascendant.node.server import NodeGUIHandler, NodeGUIServer
from ascendant.web.server import DashboardHTTPServer
from scripts.preview_dashboard import PreviewHandler, CREATOR
from ascendant.web.routes import BilibiliDataScienceHandler
from ascendant.web import distributed_dataset, dataset, actions
from tests.unit.test_nodes import item


async def fake_fetch(unit, cookie, rate, canceled, progress):
    payload = unit['payload']
    if unit['kind'] == 'selection':
        count = payload['selection']['end'] - payload['selection']['start'] + 1
        rows = [item(i + 1, uid=payload['uid']) for i in range(count)]
        await asyncio.sleep(.2)
        progress(95, f'{count} fixture rows collected')
        return {'items': rows, 'source_total': 1000, 'selection_label': 'Fixture published-time number range',
                'collection': {'examined': count + 2, 'skipped_invalid': 2, 'skipped_duplicates': 0}}
    ids = payload['bvids'] if unit['kind'] == 'videos' else ['BV' + str(i).zfill(10) for i in range(payload['count'])]
    rows = []
    for index, value in enumerate(ids):
        if canceled.is_set():
            raise ValueError('Fixture collection canceled.')
        await asyncio.sleep(.02)
        rows.append(item(int(value[2:]), uid=payload.get('uid', '1'), valid=int(value[2:]) != 3))
        progress(int(95 * (index + 1) / len(ids)), f'{index + 1}/{len(ids)} fixture videos checked')
    return {'items': rows}


servers, workers = [], []

distributed_dataset.selected_creator = lambda: CREATOR
dataset.selected_creator = lambda: CREATOR
actions.selected_creator = lambda: CREATOR
dataset.accounts.active_account_id = lambda: None

async def fake_local_fetch(payload):
    selection = payload['selection']
    count = int(selection['end']) - int(selection['start']) + 1
    rows = [item(i + 1, uid=CREATOR['uid']) for i in range(count)]
    return rows, 'Fixture local number range', 1000, {'examined': count, 'skipped_invalid': 0}

actions.fetch_selected_video_items = fake_local_fetch

# Exercise the normal Start button on a deterministic offline loopback link.
# Production discovers/binds Thunderbolt Bridge instead.
patch('ascendant.distributed.coordinator.thunderbolt_address', return_value='127.0.0.1').start()


class NodePreviewHandler(PreviewHandler):
    def do_POST(self):
        if self.path == '/api/video-action':
            BilibiliDataScienceHandler.do_POST(self)
        else:
            super().do_POST()

def serve(server):
    servers.append(server)
    Thread(target=server.serve_forever, daemon=True).start()
    return f'http://127.0.0.1:{server.server_port}'


def stop(signum, frame):
    raise KeyboardInterrupt


for signum in (signal.SIGINT, signal.SIGTERM):
    signal.signal(signum, stop)

try:
    COORDINATOR.start(0, '127.0.0.1')
    main = serve(DashboardHTTPServer(('127.0.0.1', 0), NodePreviewHandler))
    urls = []
    for _ in range(2):
        worker = Worker(fetcher=fake_fetch, interval=.08)
        server = NodeGUIServer(('127.0.0.1', 0), NodeGUIHandler)
        server.worker = worker
        urls.append(serve(server))
        worker.start()
        workers.append(worker)
    print(json.dumps({'main': main, 'nodes': urls, 'listener': f'http://127.0.0.1:{COORDINATOR.server.server_port}'}), flush=True)
    while True:
        signal.pause()
except KeyboardInterrupt:
    pass
finally:
    for worker in workers:
        worker.stop()
    for server in servers:
        server.shutdown()
        server.server_close()
    COORDINATOR.stop()
