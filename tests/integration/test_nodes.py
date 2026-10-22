"""Real local HTTP connections with a simulated fetcher; no Bilibili requests."""

import asyncio
from contextlib import ExitStack
import json
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ascendant.distributed.coordinator import Coordinator
from ascendant.distributed.worker import Worker
from ascendant.node.server import NodeGUIHandler, NodeGUIServer
from ascendant.web import nodes
from ascendant.web import distributed_dataset, dataset, actions
from ascendant.web.routes import BilibiliDataScienceHandler
from ascendant.web.server import DashboardHTTPServer
from tests.unit.test_nodes import bvid, item


class QuietHandler(BilibiliDataScienceHandler):
    def log_message(self, *args):
        pass


class NodeHTTPTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.core = Coordinator()
        self.core.start(0, '127.0.0.1')
        self.stack.callback(self.core.stop)
        self.stack.enter_context(patch.object(nodes, 'COORDINATOR', self.core))
        self.stack.enter_context(patch.object(distributed_dataset, 'COORDINATOR', self.core))
        async def fetcher(unit, cookie, rate, canceled, progress):
            await asyncio.sleep(.03)
            progress(95, 'Simulated collection completed.')
            if unit['kind'] == 'selection':
                selection = unit['payload']['selection']
                rows = [item(index, uid=unit['payload']['uid']) for index in range(selection['start'], selection['end'] + 1)]
                return {'items': rows, 'source_total': 1000, 'selection_label': 'Fixture number range',
                        'collection': {'examined': len(rows) + 2, 'skipped_invalid': 2, 'skipped_duplicates': 0}}
            return {'items': [item(int(value[2:]), valid=int(value[2:]) != 3) for value in unit['payload']['bvids']]}
        self.workers = [Worker(fetcher=fetcher, interval=.02), Worker(fetcher=fetcher, interval=.02)]
        self.gui_urls = []
        for worker in self.workers:
            server = NodeGUIServer(('127.0.0.1', 0), NodeGUIHandler)
            server.worker = worker
            self.gui_urls.append(self.serve(server))
            worker.start()
            self.stack.callback(worker.stop)
        self.main = self.serve(DashboardHTTPServer(('127.0.0.1', 0), QuietHandler))
        self.listener = f'http://127.0.0.1:{self.core.server.server_port}'

    def serve(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.stack.callback(stop)
        return f'http://127.0.0.1:{server.server_port}'

    def request(self, base, path, data=None, headers=None):
        body = json.dumps(data).encode() if data is not None else None
        request = Request(base + path, data=body, headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            return response.status, json.loads(content) if response.headers.get_content_type() == 'application/json' else content

    def test_two_node_guis_pair_collect_and_return_one_merged_report(self):
        for index, base in enumerate(self.gui_urls):
            code = self.core.new_pairing()['code']
            status, state = self.request(base, '/api/connect', {'name': f'Mac {index}', 'url': self.listener, 'code': code, 'rate': 1, 'cookie': 'private=local'})
            self.assertEqual(status, 200)
            self.assertEqual(state['connection'], 'connected')
            self.assertNotIn('private=local', str(state))
            self.assertNotIn('token', state)
        status, task = self.request(self.main, '/api/nodes/tasks', {'videos': '\n'.join(bvid(i) for i in range(42))})
        self.assertEqual(status, 200)
        deadline = time.monotonic() + 5
        while self.core.result(task['id'])['task']['state'] != 'completed':
            self.assertLess(time.monotonic(), deadline, self.core.snapshot())
            time.sleep(.03)
        status, report = self.request(self.main, '/api/nodes/result?id=' + task['id'])
        self.assertEqual(status, 200)
        self.assertEqual((report['counts']['included'], report['counts']['invalid']), (41, 1))
        self.assertEqual(len(report['nodes']), 2)
        self.assertEqual(report['task']['shortfall'], 1)
        self.assertEqual(report['summaries'][0]['total'], sum(i * 100 for i in range(42) if i != 3))

    def test_node_process_memory_reaches_gui_coordinator_and_main_dashboard(self):
        from ascendant.web import memory
        base = self.gui_urls[0]
        code = self.core.new_pairing()['code']
        self.assertEqual(self.request(base, '/api/connect', {'name': 'Memory node', 'url': self.listener, 'code': code, 'rate': 1})[0], 200)
        self.workers[0].tick()
        status, gui = self.request(base, '/api/status')
        self.assertEqual(status, 200)
        self.assertGreater(gui['memory']['rss_bytes'], 0)
        self.assertGreater(self.core.snapshot()['nodes'][0]['rss_bytes'], 0)
        with patch.object(memory, 'COORDINATOR', self.core):
            status, report = self.request(self.main, '/api/memory')
        self.assertEqual(status, 200)
        self.assertEqual(report['nodes'][0]['name'], 'Memory node')
        self.assertFalse(report['nodes'][0]['stale'])
        self.assertGreater(report['nodes'][0]['rss_bytes'], 0)
        self.assertNotIn('token', str(report))

    def test_remote_listener_exposes_no_admin_or_account_routes(self):
        for path in ('/', '/api/nodes', '/api/accounts', '/api/health', '/api/status'):
            self.assertEqual(self.request(self.listener, path)[0], 404)
        self.assertEqual(self.request(self.listener, '/node/claim', {})[0], 401)
        self.assertEqual(self.request(self.listener, '/node/pair', {'protocol': 1, 'code': 'invalid', 'name': 'Mac', 'capabilities': ['videos']})[0], 401)
        self.assertEqual(self.request(self.listener, '/node/claim', {}, {'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request(self.listener, '/node/health')[1]['protocol'], 1)

    def test_control_pages_reject_foreign_origin_host_and_bad_input(self):
        for base, path in ((self.main, '/api/nodes'), (self.gui_urls[0], '/api/status')):
            self.assertEqual(self.request(base, path, headers={'Host': 'evil.example'})[0], 403)
            self.assertEqual(self.request(base, path, headers={'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.request(self.main, '/api/nodes/tasks', {'videos': 'invalid'})[0], 400)
        self.assertEqual(self.request(self.gui_urls[0], '/api/connect', {'url': 'invalid'})[0], 400)
        self.assertEqual(self.request(self.gui_urls[0], '/node.js')[0], 200)
        self.assertEqual(self.request(self.gui_urls[0], '/../../README.md')[0], 404)
        self.assertEqual(self.request(self.main, '/api/nodes/tasks', {}, {'Content-Type': 'text/plain'})[0], 415)

    def test_connection_service_can_stop_and_restart_without_disk_state(self):
        self.core.stop()
        self.assertFalse(self.request(self.main, '/api/nodes')[1]['running'])
        self.core.start(0, '127.0.0.1')
        self.assertTrue(self.request(self.main, '/api/nodes')[1]['running'])

    def test_preflight_checks_actual_port_without_consuming_pairing_code(self):
        code = self.core.new_pairing()['code']
        status, result = self.request(self.gui_urls[0], '/api/check-connection', {'url': self.listener})
        self.assertEqual(status, 200)
        self.assertTrue(result['ok'])
        self.assertEqual(self.core.pair_code, code)
        status, result = self.request(self.gui_urls[0], '/api/check-connection', {'url': self.main})
        self.assertEqual(status, 400)
        self.assertIn('connection service', result['error'])
        self.assertEqual(self.core.pair_code, code)

    def test_normal_fetch_imports_remote_rows_and_reuses_them_for_ratios_and_statistics(self):
        creator = {'uid': '42', 'name': 'Fixture Creator'}
        self.stack.enter_context(patch.object(distributed_dataset, 'selected_creator', return_value=creator))
        self.stack.enter_context(patch.object(dataset, 'selected_creator', return_value=creator))
        self.stack.enter_context(patch.object(dataset, 'CURRENT', None))
        local = self.stack.enter_context(patch.object(actions, 'fetch_selected_video_items', side_effect=AssertionError('Local fetching must not run')))
        code = self.core.new_pairing()['code']
        status, _ = self.request(self.gui_urls[0], '/api/connect', {'name': 'Other Mac', 'url': self.listener, 'code': code, 'rate': 1})
        self.assertEqual(status, 200)
        payload = {'selection': {'kind': 'position', 'start': '1', 'end': '2'}, 'fetch_source': 'auto', 'reuse_only': True}
        status, result = self.request(self.main, '/api/video-action', {**payload, 'action': 'list', 'refresh': True})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['count'], 2)
        self.assertEqual(result['dataset']['collection']['node_name'], 'Other Mac')
        self.assertEqual(result['dataset']['collection']['skipped_invalid'], 2)
        for action in ('analysis', 'division'):
            status, report = self.request(self.main, '/api/video-action', {**payload, 'action': action, 'mode': 'aggregate'})
            self.assertEqual(status, 200, report)
            self.assertEqual(report['count'], 2)
            self.assertTrue(report['dataset']['reused'])
        local.assert_not_called()
        self.assertEqual(len(self.core.jobs), 1)

    def test_normal_workflow_switches_local_remote_local_remote_automatically(self):
        creator = {'uid': '42', 'name': 'Fixture Creator'}
        self.stack.enter_context(patch.object(distributed_dataset, 'selected_creator', return_value=creator))
        self.stack.enter_context(patch.object(dataset, 'selected_creator', return_value=creator))
        self.stack.enter_context(patch.object(dataset, 'CURRENT', None))
        local = self.stack.enter_context(patch.object(actions, 'fetch_selected_video_items', new_callable=AsyncMock,
            return_value=([item(1, uid='42'), item(2, uid='42')], 'Local fixture range', 1000,
                          {'examined': 2, 'skipped_invalid': 0})))
        payload = {'selection': {'kind': 'position', 'start': 1, 'end': 2}, 'reuse_only': True}
        def fetch(expected_node=None):
            status, result = self.request(self.main, '/api/video-action', {**payload, 'action': 'list', 'refresh': True})
            self.assertEqual(status, 200, result)
            self.assertEqual(result['count'], 2)
            self.assertEqual(result['dataset']['collection'].get('node_name'), expected_node)
            return result
        fetch()
        code = self.core.new_pairing()['code']
        status, state = self.request(self.gui_urls[0], '/api/connect', {'name': 'Other Mac', 'url': self.listener, 'code': code})
        self.assertEqual(status, 200)
        fetch('Other Mac')
        self.core.node_action(state['node_id'], 'pause')
        fetch()
        self.core.node_action(state['node_id'], 'resume')
        # Availability changes do not replace a loaded dataset during analysis.
        status, analysis = self.request(self.main, '/api/video-action', {**payload, 'action': 'analysis'})
        self.assertEqual(status, 200, analysis)
        self.assertTrue(analysis['dataset']['reused'])
        self.assertNotIn('node_name', analysis['dataset']['collection'])
        fetch('Other Mac')
        self.assertEqual(local.await_count, 2)
        self.assertEqual(len(self.core.jobs), 2)

    def test_remote_page_error_reaches_main_and_preserves_previous_dataset(self):
        from unittest.mock import Mock
        from ascendant.errors import ResponseCodeException
        from ascendant.videos import fetch_creator_video_page
        creator = {'uid': '42', 'name': 'Fixture Creator'}
        self.stack.enter_context(patch.object(distributed_dataset, 'selected_creator', return_value=creator))
        self.stack.enter_context(patch.object(dataset, 'selected_creator', return_value=creator))
        self.stack.enter_context(patch.object(dataset, 'CURRENT', None))
        local = self.stack.enter_context(patch.object(actions, 'fetch_selected_video_items', side_effect=AssertionError('Do not retry locally')))
        status, _ = self.request(self.gui_urls[0], '/api/connect', {'name': 'Other Mac', 'url': self.listener,
                                                                'code': self.core.new_pairing()['code']})
        self.assertEqual(status, 200)
        selection = {'kind': 'position', 'start': 1, 'end': 2}
        status, first = self.request(self.main, '/api/video-action', {'action': 'list', 'refresh': True,
                                    'selection': selection, 'fetch_source': 'auto'})
        self.assertEqual(status, 200, first)
        async def failed_fetch(*args):
            uploader = Mock(get_videos=AsyncMock(side_effect=ResponseCodeException(-352, 'Rejected', {'cookie': 'private'})))
            await fetch_creator_video_page(uploader, pn=2, ps=30, order='publication', collected=30, requested=100)
        self.workers[0].fetcher = failed_fetch
        status, failure = self.request(self.main, '/api/video-action', {'action': 'list', 'refresh': True,
            'selection': {**selection, 'end': 100}, 'fetch_source': 'auto'})
        self.assertEqual(status, 400, failure)
        self.assertIn('Other Mac', failure['error'])
        self.assertIn('API code -352', failure['error'])
        self.assertIn('30/100 valid videos', failure['error'])
        self.assertNotIn('private', failure['error'])
        self.assertEqual(self.core.snapshot()['tasks'][0]['state'], 'failed')
        self.assertEqual(self.core.snapshot()['nodes'][0]['status'], 'paused')
        status, reused = self.request(self.main, '/api/video-action', {'action': 'analysis', 'reuse_only': True,
                                     'selection': selection})
        self.assertEqual(status, 200, reused)
        self.assertEqual(reused['count'], 2)
        self.assertTrue(reused['dataset']['reused'])
        local.assert_not_called()
