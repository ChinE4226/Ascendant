"""Offline resource soak: Python tests/stress/check_stability.py --rounds 6.

Bilibili uses HTTPX MockTransport. Real dashboard requests use loopback; SQLite
and all account/runtime paths use a temporary directory. No saved data is used.
"""

import argparse
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import gc
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
from threading import Thread, active_count
import time
import tracemalloc
from unittest.mock import patch
from urllib.request import ProxyHandler, build_opener
import weakref

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx

from bilibili_ds import client, config, tracking
from bilibili_ds.api import Credential
from bilibili_ds.errors import BilibiliRequestError
from bilibili_ds.fetch_context import REQUEST_DELAY
from bilibili_ds.memory import process_memory
from bilibili_ds.web.routes import BilibiliDataScienceHandler
from bilibili_ds.web.server import DashboardHTTPServer

INFO = {'bvid': 'BV1xx411c7mD', 'title': 'Offline stability fixture', 'pubdate': 1791417600,
        'owner': {'mid': 7, 'name': 'Fixture'}, 'stat': {'view': 100, 'like': 10}}


class QuietHandler(BilibiliDataScienceHandler):
    def log_message(self, *args):
        pass


def descriptors():
    try:
        return len(os.listdir('/dev/fd'))
    except OSError:
        return None


def snapshot():
    gc.collect()
    return {'rss_bytes': process_memory()['rss_bytes'], 'traced_bytes': tracemalloc.get_traced_memory()[0],
            'threads': active_count(), 'file_descriptors': descriptors(), 'cached_loops': len(client._sessions)}


def transport_round(count, workers):
    references = []

    async def operation(index):
        delay = REQUEST_DELAY.set(0)
        credential = Credential(sessdata='offline-fixture') if index % 2 else None
        session = client.get_session(credential)
        references.append((weakref.ref(asyncio.get_running_loop()), weakref.ref(session.http)))
        try:
            await client.request_data('https://api.bilibili.com/x/web-interface/view',
                                      params={'case': index % 5}, credential=credential)
            return 'success'
        except asyncio.CancelledError:
            return 'cancelled'
        except BilibiliRequestError as exc:
            return 'http_412' if exc.status == 412 else exc.kind
        finally:
            await client.close_bilibili_client()
            REQUEST_DELAY.reset(delay)
            assert session.http.is_closed
            assert asyncio.get_running_loop() not in client._sessions

    def worker(offset):
        return Counter(asyncio.run(operation(i)) for i in range(offset, count, workers))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        result = sum(executor.map(worker, range(workers)), Counter())
    labels = ('success', 'http_412', 'timeout', 'invalid_response', 'cancelled')
    assert result == Counter(labels[i % len(labels)] for i in range(count))
    gc.collect()
    assert all(loop() is None and session() is None for loop, session in references)
    assert not client._sessions
    return result


def run(args):
    original_client = httpx.AsyncClient

    def respond(request):
        case = int(request.url.params['case'])
        if case == 1:
            return httpx.Response(412, text='offline rejection')
        if case == 2:
            raise httpx.ReadTimeout('offline timeout')
        if case == 3:
            return httpx.Response(200, text='not JSON')
        if case == 4:
            raise asyncio.CancelledError
        return httpx.Response(200, json={'code': 0, 'data': INFO})

    def offline_client(**kwargs):
        return original_client(**kwargs, transport=httpx.MockTransport(respond))

    with ExitStack() as stack:
        root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='ascendant-stability-')))
        for name in ('TRACKING_DB', 'CREATORS_FILE', 'COOKIE_FILE', 'LEGACY_CREDENTIAL_FILE',
                     'ACTIVE_ACCOUNT_FILE', 'ACCOUNTS_DIR', 'PLOTS_DIR', 'QRCODE_FILE'):
            stack.enter_context(patch.object(config, name, root / name.lower()))
        stack.enter_context(patch.object(client.httpx, 'AsyncClient', new=offline_client))
        tracking.initialize()
        server = DashboardHTTPServer(('127.0.0.1', 0), QuietHandler)
        thread = Thread(target=server.serve_forever, name='stability-http', daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        opener = build_opener(ProxyHandler({}))

        def request(index):
            path = ('/api/health', '/api/tracking/revision', '/api/memory')[index % 3]
            started = time.perf_counter()
            with opener.open(base + path, timeout=10) as response:
                assert response.status == 200
                json.load(response)
            return 1000 * (time.perf_counter() - started)

        def observation(index):
            tracking.save_observation(INFO)

        def batch(index):
            transport = transport_round(args.operations, args.workers)
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                # Exercise reads while real SQLite writes contend for the WAL.
                writes = [executor.submit(observation, i) for i in range(args.writes)]
                latency = list(executor.map(request, range(args.requests)))
                for future in writes:
                    future.result()
            time.sleep(.05)  # Let completed HTTP handler threads leave their finally blocks.
            return {'round': index, 'outcomes': dict(transport),
                    'http_p95_ms': round(sorted(latency)[int(.95 * (len(latency) - 1))], 2),
                    'http_mean_ms': round(statistics.mean(latency), 2), **snapshot()}

        started = time.perf_counter()
        initial_threads, initial_fds = active_count() - 1, descriptors()
        samples = []
        try:
            tracemalloc.start()
            for i in range(2):
                batch(f'warmup-{i + 1}')
            baseline = snapshot()
            for i in range(args.rounds):
                samples.append(batch(i + 1))
                print(json.dumps(samples[-1]), flush=True)
            with tracking.connection() as db:
                integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
                records = db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]
            assert integrity == 'ok'
            assert records == (args.rounds + 2) * args.writes
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            assert not thread.is_alive()
        final = snapshot()
        assert final['threads'] == initial_threads
        if initial_fds is not None and final['file_descriptors'] is not None:
            assert final['file_descriptors'] <= initial_fds - 1
        assert not client._sessions
        report = {'baseline': baseline, 'final': final, 'rounds': samples,
                  'measured_transport_operations': args.rounds * args.operations,
                  'measured_http_requests': args.rounds * args.requests,
                  'sqlite_observations_including_warmup': records, 'sqlite_integrity': integrity,
                  'elapsed_seconds': round(time.perf_counter() - started, 2),
                  'all_worker_threads_stopped': True, 'all_client_loop_weakrefs_released': True,
                  'initial_open_file_descriptors_with_server': initial_fds}
        print(json.dumps(report, indent=2))
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds', type=int, default=6)
    parser.add_argument('--operations', type=int, default=400)
    parser.add_argument('--requests', type=int, default=400)
    parser.add_argument('--writes', type=int, default=100)
    parser.add_argument('--workers', type=int, default=8)
    options = parser.parse_args()
    if min(vars(options).values()) < 1:
        parser.error('All counts must be positive.')
    run(options)
