"""Offline parallel collection with real node connections and shared dataset semantics."""

import asyncio
from contextlib import ExitStack
from copy import deepcopy
from threading import Event
import unittest
from unittest.mock import AsyncMock, Mock, patch

from ascendant.distributed.coordinator import Coordinator
from ascendant.distributed.worker import Worker
from ascendant.fetch_context import REQUEST_DELAY
from ascendant.web import parallel, videos, actions, dataset
from tests.unit.test_nodes import bvid, item


class ParallelFetchingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.core = Coordinator()
        self.core.start(0, '127.0.0.1')
        self.stack.callback(self.core.stop)
        self.stack.enter_context(patch.object(parallel, 'COORDINATOR', self.core))
        self.stack.enter_context(patch.object(dataset, 'CURRENT', None))
        for module in (parallel, videos, dataset, actions):
            self.stack.enter_context(patch.object(module, 'selected_creator', return_value={'uid': '42', 'name': 'Creator'}))
        self.stack.enter_context(patch.object(videos.account_service, 'credential_from_env', return_value=None))
        self.stack.enter_context(patch.object(videos.client, 'configure_bilibili_client'))
        self.stack.enter_context(patch.object(videos.client, 'close_bilibili_client', new_callable=AsyncMock))
        self.stack.enter_context(patch.object(videos.client, 'request_delay_seconds', return_value=0))
        self.stack.enter_context(patch.object(parallel.settings, 'REQUEST_FREQUENCY', 4))
        self.main_started, self.remote_started = Event(), Event()
        self.local_ids, self.remote_ids, self.remote_macs, self.pacing = [], [], set(), []
        self.fail_remote = False
        self.payload = {'action': 'list', 'refresh': True, 'fetch_source': 'parallel',
                        'selection': {'kind': 'position', 'start': 1, 'end': 100}}

        async def detail(summary, credential):
            self.main_started.set()
            self.assertTrue(await asyncio.to_thread(self.remote_started.wait, 2), 'Main and node requests must overlap')
            index = int(summary['bvid'][2:])
            self.local_ids.append(index)
            self.pacing.append(REQUEST_DELAY.get())
            return item(index, uid='42', valid=index not in {1, 31})
        self.detail = self.stack.enter_context(patch.object(videos.video_service, 'fetch_video_detail', side_effect=detail))
        self.workers = []
        for name in ('Mac A', 'Mac B'):
            async def fetch(unit, cookie, rate, canceled, progress, name=name):
                self.remote_macs.add(name)
                self.remote_started.set()
                self.assertTrue(await asyncio.to_thread(self.main_started.wait, 2))
                self.assertEqual((unit['payload']['request_rate'], unit['payload']['participants']), (4, 3))
                if self.fail_remote:
                    raise ValueError('Bilibili returned HTTP 412.')
                await asyncio.sleep(.03)
                ids = [int(value[2:]) for value in unit['payload']['bvids']]
                self.remote_ids.extend(ids)
                return {'items': [item(index, uid='42', valid=index not in {1, 31}) for index in ids]}
            worker = Worker(fetcher=fetch, interval=.005)
            worker.connect({'name': name, 'url': f'http://127.0.0.1:{self.core.server.server_port}',
                            'code': self.core.new_pairing()['code'], 'rate': 1})
            worker.start()
            self.workers.append(worker)
            self.stack.callback(worker.stop)
        self.uploader = Mock(get_videos=AsyncMock(side_effect=[{'page': {'count': 150}}, *[
            {'list': {'vlist': [{'bvid': bvid(index)} for index in range(start, start + 30)]}}
            for start in (0, 30, 60, 90, 120)]]))
        self.stack.enter_context(patch.object(videos.user, 'User', return_value=self.uploader))

    async def test_both_nodes_and_main_overlap_replace_invalid_rows_and_merge_one_dataset(self):
        response = await actions.execute_video_action(self.payload)
        self.assertEqual(response['count'], 100)
        self.assertEqual(set(self.local_ids + self.remote_ids), set(range(102)))
        self.assertFalse(set(self.local_ids).intersection(self.remote_ids), 'Do not duplicate detail requests across Macs')
        self.assertEqual(self.remote_macs, {'Mac A', 'Mac B'})
        self.assertTrue(all(delay == .75 for delay in self.pacing))
        collection = response['dataset']['collection']
        self.assertEqual((collection['examined'], collection['skipped_invalid'], collection['shortfall']), (102, 2, 0))
        self.assertIn('This Mac + Mac A, Mac B', collection['node_name'])
        self.assertEqual(len(self.core.jobs), 1, 'Every detail wave belongs to the same bounded RAM job')
        self.assertEqual(self.core.snapshot()['tasks'][0]['state'], 'completed')
        self.assertEqual(len(dataset.CURRENT['data'][0]), 100)
        self.assertEqual(self.uploader.get_videos.await_count, 5)

    async def test_remote_guard_stops_followup_pages_and_preserves_previous_dataset(self):
        self.fail_remote = True
        previous = {'key': ('old',), 'data': ([item(999)], 'previous selection', 1), 'meta': {'count': 1}}
        dataset.CURRENT = deepcopy(previous)
        with self.assertRaisesRegex(ValueError, 'HTTP 412'):
            await actions.execute_video_action(self.payload)
        self.assertEqual(dataset.CURRENT, previous)
        self.assertEqual(self.uploader.get_videos.await_count, 2, 'No next summary page after node protection failure')
        self.assertEqual(self.core.snapshot()['tasks'][0]['state'], 'failed')
        self.assertTrue(any(not worker.enabled for worker in self.workers))
        self.assertTrue(set(self.local_ids).issubset(range(30)))
