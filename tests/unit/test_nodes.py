"""Pairing, task leases, invalid metrics, cancellation and delivery retry."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from ascendant.distributed.coordinator import Coordinator
from ascendant.distributed.protocol import ProtocolError, coordinator_url, frequency, video_ids
from ascendant.distributed.worker import Worker
from ascendant.distributed import worker as worker_module
from ascendant.web import distributed_dataset


class CableTransportTests(unittest.TestCase):
    def test_remote_control_requests_bind_bridge_source_and_keep_secrets_in_memory(self):
        response = Mock()
        response.read.return_value = b'{"ok": true}'
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch.object(worker_module, 'thunderbolt_address', return_value='169.254.138.156'), \
             patch.object(worker_module, 'build_opener', return_value=opener) as build:
            self.assertEqual(worker_module.request_json('http://169.254.57.204:8010', '/node/heartbeat', {}, 'fixture-token'), {'ok': True})
        handlers = build.call_args.args
        cable = [handler for handler in handlers if isinstance(handler, (worker_module.CableHTTPHandler, worker_module.CableHTTPSHandler))]
        self.assertEqual([handler.source for handler in cable], [('169.254.138.156', 0)] * 2)
        self.assertEqual(opener.open.call_args.args[0].get_header('Authorization'), 'Bearer fixture-token')

    def test_missing_cable_never_tries_wifi_transport(self):
        with patch.object(worker_module, 'thunderbolt_address', side_effect=ValueError('No active Thunderbolt Bridge')), \
             patch.object(worker_module, 'build_opener') as build:
            with self.assertRaisesRegex(ProtocolError, 'No active Thunderbolt Bridge'):
                worker_module.request_json('http://169.254.57.204:8010', '/node/health')
        build.assert_not_called()


def bvid(index):
    return 'BV' + str(index).zfill(10)


def item(index, uid='1', valid=True):
    return {'bvid': bvid(index), 'title': f'Video {index}', 'pubdate': 1790812800,
            'owner': {'mid': uid, 'name': 'Creator'},
            'stat': {'view': index * 100, 'like': 10, 'reply': 0, 'favorite': 0, 'coin': 0, 'share': 0} if valid else {}}


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.now = 100000
        self.core = Coordinator(clock=lambda: self.now)
        self.core.server = SimpleNamespace(server_port=8010)

    def pair(self, name='Mac'):
        code = self.core.new_pairing()['code']
        return self.core.pair({'protocol': 1, 'name': name, 'code': code, 'capabilities': ['videos', 'creator']})

    def claim(self, node):
        return self.core.dispatch('/node/claim', node['token'], {})['unit']

    def complete(self, node, unit, rows):
        return self.core.dispatch('/node/complete', node['token'], {'unit_id': unit['id'], 'lease': unit['lease'], 'items': rows})

    def task(self, count=1, **extra):
        return self.core.create_job({'videos': '\n'.join(bvid(i) for i in range(count)), **extra})

    def test_pairing_code_one_use_expiry_protocol_and_private_tokens(self):
        code = self.core.new_pairing()['code']
        node = self.core.pair({'protocol': 1, 'name': 'Mac', 'code': code, 'capabilities': ['videos']})
        with self.assertRaises(ProtocolError):
            self.core.pair({'protocol': 1, 'name': 'Mac', 'code': code, 'capabilities': ['videos']})
        self.assertNotIn(node['token'], str(self.core.snapshot()))
        code = self.core.new_pairing()['code']
        self.now += 301
        with self.assertRaises(ProtocolError):
            self.core.pair({'protocol': 1, 'name': 'Mac', 'code': code, 'capabilities': ['videos']})
        with self.assertRaises(ProtocolError):
            self.core.pair({'protocol': 2})
        with self.assertRaises(ProtocolError):
            self.core.dispatch('/node/claim', 'wrong token', {})

    def test_heartbeat_memory_is_optional_and_validated_without_exposing_secrets(self):
        node = self.pair()
        self.core.dispatch('/node/heartbeat', node['token'], {'ready': True, 'memory': {'rss_bytes': 123456}})
        snapshot = self.core.snapshot()['nodes'][0]
        self.assertEqual(snapshot['rss_bytes'], 123456)
        self.assertIsNotNone(snapshot['memory_received_at'])
        self.assertNotIn(node['token'], str(snapshot))
        for value in (True, -1, '500', float('nan'), 2**63):
            self.core.dispatch('/node/heartbeat', node['token'], {'ready': True, 'memory': {'rss_bytes': value}})
            self.assertIsNone(self.core.snapshot()['nodes'][0]['rss_bytes'])
        self.core.dispatch('/node/heartbeat', node['token'], {'ready': True})
        self.assertIsNone(self.core.snapshot()['nodes'][0]['memory_received_at'])

    def test_hot_plug_updates_connection_addresses_without_losing_pairing(self):
        node = self.pair()
        task = self.task()
        address = {'url': 'http://169.254.57.204:8010', 'interface': 'bridge0',
                   'kind': 'thunderbolt', 'label': 'Thunderbolt Bridge'}
        with patch('ascendant.distributed.coordinator.connection_addresses', return_value=[address]):
            snapshot = self.core.snapshot()
        self.assertEqual(snapshot['addresses'], [address])
        self.assertEqual(snapshot['urls'], [address['url']])
        self.assertEqual(snapshot['nodes'][0]['name'], 'Mac')
        self.assertEqual(snapshot['tasks'][0]['id'], task['id'])
        self.assertIsNotNone(self.claim(node), 'Transport address changes do not revoke RAM pairing')

    def test_split_work_exact_ids_metrics_and_duplicate_completion(self):
        a, b = self.pair('A'), self.pair('B')
        task = self.task(22)
        first, second = self.claim(a), self.claim(b)
        self.assertEqual((len(first['payload']['bvids']), len(second['payload']['bvids'])), (20, 2))
        self.assertIsNone(self.claim(a))
        rows = [item(i, valid=i != 3) for i in range(20)]
        self.complete(a, first, rows)
        self.assertTrue(self.complete(a, first, rows)['duplicate'])
        self.complete(b, second, [item(20), item(21)])
        result = self.core.result(task['id'])
        self.assertEqual(result['task']['state'], 'completed')
        self.assertEqual((result['counts']['included'], result['counts']['invalid'], result['task']['shortfall']), (21, 1, 1))
        self.assertEqual(result['nodes'], ['A', 'B'])
        self.assertEqual(result['summaries'][0]['total'], sum(i * 100 for i in range(22) if i != 3))

    def test_parallel_detail_waves_retain_old_delivery_leases_and_one_job(self):
        node = self.pair()
        task = self.task(2, request_rate=.5, participants=2, batch_size=1)
        for index in (0, 1):
            unit = self.claim(node)
            self.assertEqual(unit['payload']['request_rate'], .5)
            self.complete(node, unit, [item(index)])
        self.core.append_video_units(task['id'], {'videos': bvid(2), 'batch_size': 1,
                                                'request_rate': .5, 'participants': 2, 'targets': [node['node_id']]})
        new = self.claim(node)
        self.assertTrue(self.complete(node, unit, [item(1)])['duplicate'])
        self.assertEqual(self.core.nodes[node['node_id']]['unit_id'], new['id'])
        self.complete(node, new, [item(2)])
        self.assertEqual(len(self.core.raw_video_result(task['id'])), 3)
        self.assertEqual(len(self.core.jobs), 1)
        with self.assertRaisesRegex(ValueError, 'unique'):
            self.core.append_video_units(task['id'], {'videos': bvid(2), 'batch_size': 1,
                                                    'request_rate': .5, 'participants': 2, 'targets': [node['node_id']]})

    def test_expired_attempt_reassigned_and_stale_result_rejected(self):
        a, b = self.pair('A'), self.pair('B')
        self.task()
        old = self.claim(a)
        self.now += 61
        new = self.claim(b)
        self.assertNotEqual(old['lease'], new['lease'])
        with self.assertRaises(ProtocolError) as error:
            self.complete(a, old, [item(0)])
        self.assertEqual(error.exception.status, 409)
        self.complete(b, new, [item(0)])

    def test_heartbeat_renews_lease_offline_status_and_three_attempt_limit(self):
        node = self.pair()
        task = self.task()
        unit = self.claim(node)
        self.now += 50
        self.core.dispatch('/node/heartbeat', node['token'], {'ready': True, 'unit_id': unit['id'], 'lease': unit['lease'], 'progress': 50})
        self.now += 30
        self.assertEqual(self.core.snapshot()['nodes'][0]['status'], 'offline')
        self.assertEqual(self.core.snapshot()['tasks'][0]['state'], 'running')
        self.now += 31
        self.claim(node)
        self.now += 61
        self.claim(node)
        self.now += 61
        self.assertEqual(self.core.result(task['id'])['task']['state'], 'failed')

    def test_pause_resume_revoke_and_targets_do_not_redirect(self):
        a, b = self.pair('A'), self.pair('B')
        task = self.task(targets=[a['node_id']])
        self.assertIsNone(self.claim(b))
        self.core.node_action(a['node_id'], 'pause')
        self.assertIsNone(self.claim(a))
        self.core.node_action(a['node_id'], 'resume')
        old = self.claim(a)
        self.core.node_action(a['node_id'], 'remove')
        with self.assertRaises(ProtocolError):
            self.complete(a, old, [item(0)])
        self.assertIsNone(self.claim(b))
        self.assertEqual(self.core.result(task['id'])['task']['state'], 'queued')

    def test_cancel_and_failure_keep_accepted_results_and_stop_remaining_work(self):
        a, b = self.pair('A'), self.pair('B')
        task = self.task(60)
        first = self.claim(a)
        self.complete(a, first, [item(i) for i in range(20)])
        failed, other = self.claim(a), self.claim(b)
        self.core.dispatch('/node/fail', a['token'], {'unit_id': failed['id'], 'lease': failed['lease'], 'error': 'Bilibili rejected collection'})
        with self.assertRaises(ProtocolError):
            self.complete(b, other, [item(i) for i in range(40, 60)])
        result = self.core.result(task['id'])
        self.assertEqual((result['task']['state'], result['counts']['included']), ('failed', 20))
        self.core.task_action(task['id'], 'cancel')
        self.assertEqual(self.core.result(task['id'])['counts']['included'], 20)
        self.core.task_action(None, 'clear')
        self.assertEqual(self.core.snapshot()['tasks'], [])

    def test_result_id_scope_creator_count_and_validation(self):
        node = self.pair()
        self.task()
        unit = self.claim(node)
        with self.assertRaisesRegex(ValueError, 'assigned video IDs'):
            self.complete(node, unit, [item(1)])
        with self.assertRaisesRegex(ValueError, 'repeated'):
            self.complete(node, unit, [item(0), item(0)])
        self.complete(node, unit, [item(0)])
        task = self.core.create_job({'kind': 'creator', 'uid': '42', 'count': 2, 'start': 1})
        unit = self.claim(node)
        with self.assertRaisesRegex(ValueError, 'different creator'):
            self.complete(node, unit, [item(1)])
        self.complete(node, unit, [item(1, uid='42'), item(2, uid='42', valid=False), item(3, uid='42')])
        self.assertEqual(self.core.result(task['id'])['task']['shortfall'], 0)
        for data in ({'videos': ''}, {'kind': 'other'}, {'kind': 'creator', 'uid': 'abc'}, {'videos': bvid(1), 'targets': ['missing']}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.core.create_job(data)

    def test_auto_fallback_cancels_only_selections_that_were_never_claimed(self):
        code = self.core.new_pairing()['code']
        node = self.core.pair({'protocol': 1, 'name': 'Mac', 'code': code, 'capabilities': ['selection']})
        def selection():
            return self.core.create_job({'kind': 'selection', 'uid': '1',
                                        'selection': {'kind': 'position', 'start': 1, 'end': 2}})
        unstarted = selection()
        self.assertTrue(self.core.cancel_unstarted_selection(unstarted['id']))
        self.assertEqual(self.core.result(unstarted['id'])['task']['state'], 'canceled')
        self.assertIsNone(self.claim(node))
        started = selection()
        unit = self.claim(node)
        self.assertFalse(self.core.cancel_unstarted_selection(started['id']))
        self.now += 61
        self.core.snapshot()  # Its lease expires, but it has already made an attempt.
        self.assertFalse(self.core.cancel_unstarted_selection(started['id']))
        self.assertNotEqual(unit['lease'], self.claim(node)['lease'])


class AutomaticFetchingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.core = Mock()
        self.status = {'running': True, 'nodes': [], 'tasks': []}
        self.core.snapshot.return_value = self.status
        self.local_result = ([], 'local collection', 0, {})
        self.local = AsyncMock(return_value=self.local_result)
        self.payload = {'selection': {'kind': 'position', 'start': 1, 'end': 2}}
        for name, value in (('COORDINATOR', self.core), ('set_progress', Mock()),
                            ('selected_creator', Mock(return_value={'uid': '42'}))):
            patcher = patch.object(distributed_dataset, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def node(self, status='idle', capabilities=('selection',)):
        return {'id': 'node', 'name': 'Other Mac', 'status': status,
                'capabilities': capabilities, 'message': 'Ready'}

    async def fetch(self, **extra):
        return await distributed_dataset.fetch_workspace_items({**self.payload, **extra}, local_fetcher=self.local)

    async def test_automatic_falls_back_for_all_unavailable_node_states(self):
        configurations = [([], True), ([self.node()], False),
                          ([self.node(capabilities=('videos',))], True)]
        configurations += [([self.node(status)], True) for status in ('busy', 'paused', 'offline')]
        for nodes, running in configurations:
            with self.subTest(nodes=nodes, running=running):
                self.status.update(nodes=nodes, running=running)
                self.local.reset_mock()
                self.assertEqual(await self.fetch(), self.local_result)
                self.local.assert_awaited_once()
                self.core.create_job.assert_not_called()

    async def test_default_automatic_chooses_ready_node(self):
        self.status['nodes'] = [self.node('paused'), self.node()]
        self.core.snapshot.side_effect = [self.status, {**self.status, 'tasks': [
            {'id': 'job', 'state': 'completed'}]}]
        self.core.create_job.return_value = {'id': 'job'}
        result = ([], 'remote collection', 0, {'node_name': 'Other Mac'})
        self.core.working_result.return_value = result
        self.assertEqual(await self.fetch(), result)
        self.local.assert_not_awaited()
        self.core.create_job.assert_called_once_with({'kind': 'selection', 'uid': '42',
            'selection': self.payload['selection'], 'targets': ['node'], 'request_rate': 4.0})

    async def test_local_only_and_remote_only_keep_their_explicit_behaviour(self):
        self.status['nodes'] = [self.node()]
        self.assertEqual(await self.fetch(fetch_source='local'), self.local_result)
        self.core.snapshot.assert_not_called()
        self.local.reset_mock()
        self.status['nodes'] = [self.node('offline')]
        with self.assertRaisesRegex(ValueError, 'busy, paused or offline'):
            await self.fetch(fetch_source='remote')
        self.local.assert_not_awaited()

    async def test_ranges_outside_node_protocol_and_full_queue_use_this_mac(self):
        self.status['nodes'] = [self.node()]
        for selection in ({}, {'kind': 'position', 'start': 1, 'end': 501}):
            with self.subTest(selection=selection):
                self.local.reset_mock()
                self.assertEqual(await self.fetch(selection=selection), self.local_result)
                self.local.assert_awaited_once()
        self.status['tasks'] = [{}] * 30
        self.local.reset_mock()
        self.assertEqual(await self.fetch(), self.local_result)
        self.local.assert_awaited_once()
        self.core.create_job.assert_not_called()

    async def test_failed_remote_collection_does_not_retry_bilibili_locally(self):
        self.status['nodes'] = [self.node()]
        self.core.snapshot.side_effect = [self.status, {**self.status, 'tasks': [
            {'id': 'job', 'state': 'failed', 'error': 'Bilibili rejected requests.'}]}]
        self.core.create_job.return_value = {'id': 'job'}
        with self.assertRaisesRegex(ValueError, 'Bilibili rejected'):
            await self.fetch()
        self.local.assert_not_awaited()
        self.core.task_action.assert_not_called()

    async def test_node_disappearing_before_first_claim_falls_back_without_duplicate_work(self):
        self.status['nodes'] = [self.node()]
        self.core.snapshot.side_effect = [self.status, {'running': True, 'nodes': [], 'tasks': [
            {'id': 'job', 'state': 'queued', 'started_at': None}]}]
        self.core.create_job.return_value = {'id': 'job'}
        self.core.cancel_unstarted_selection.return_value = True
        self.assertEqual(await self.fetch(), self.local_result)
        self.core.cancel_unstarted_selection.assert_called_once_with('job')
        self.local.assert_awaited_once()

    async def test_stopped_service_after_work_started_does_not_start_another_collection(self):
        self.status['nodes'] = [self.node()]
        self.core.snapshot.side_effect = [self.status, {'running': False, 'nodes': [], 'tasks': [
            {'id': 'job', 'state': 'queued', 'started_at': 'earlier'}]}]
        self.core.create_job.return_value = {'id': 'job'}
        with self.assertRaisesRegex(ValueError, 'connections stopped'):
            await self.fetch()
        self.core.cancel_unstarted_selection.assert_not_called()
        self.local.assert_not_awaited()


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.core = Coordinator()
        self.core.server = SimpleNamespace(server_port=8010)
        self.delivery_failure = False
        self.ack_failure = False
        async def fetcher(unit, cookie, rate, canceled, progress):
            progress(95, 'Fetched fixture data.')
            return {'items': [item(int(value[2:])) for value in unit['payload']['bvids']]}
        def transport(url, path, data, token=''):
            if path == '/node/pair':
                return self.core.pair(data)
            if path == '/node/complete' and self.delivery_failure:
                self.delivery_failure = False
                raise OSError('Connection interrupted')
            result = self.core.dispatch(path, token, data)
            if path == '/node/complete' and self.ack_failure:
                self.ack_failure = False
                raise OSError('Acknowledgement lost after accepting the result')
            return result
        self.worker = Worker(fetcher=fetcher, transport=transport, health_checker=lambda url: {'ok': True})
        self.addCleanup(self.worker.stop)
        self.worker.connect({'url': 'http://127.0.0.1:8010', 'code': self.core.new_pairing()['code'], 'name': 'Worker', 'cookie': 'secret=local'})

    def test_delivery_retries_keeps_results_in_ram_and_hides_credentials(self):
        task = self.core.create_job({'videos': bvid(1)})
        self.worker.tick()
        self.worker.future.result(timeout=3)
        self.delivery_failure = True
        self.worker.tick()
        self.assertEqual(self.worker.snapshot()['connection'], 'reconnecting')
        self.assertIsNotNone(self.worker.pending)
        self.worker.tick()
        self.assertEqual(self.worker.snapshot()['completed'], 1)
        self.assertEqual(self.core.result(task['id'])['counts']['included'], 1)
        self.assertNotIn('secret=local', str(self.worker.snapshot()))
        self.assertNotIn(self.worker.token, str(self.worker.snapshot()))

    def test_local_pause_and_main_pause_prevent_claim_then_resume(self):
        self.core.create_job({'videos': bvid(1)})
        self.worker.control('pause')
        self.worker.tick()
        self.assertIsNone(self.worker.active)
        self.worker.control('resume')
        self.core.node_action(self.worker.node_id, 'pause')
        self.worker.tick()
        self.assertIsNone(self.worker.active)
        self.assertFalse(self.worker.snapshot()['coordinator_enabled'])
        self.core.node_action(self.worker.node_id, 'resume')
        self.worker.tick()
        self.assertIsNotNone(self.worker.active)
        with self.assertRaises(ValueError):
            self.worker.control('disconnect')

    def test_lost_acknowledgement_retries_without_duplicate_data_or_losing_completion(self):
        task = self.core.create_job({'videos': bvid(1)})
        self.worker.tick()
        self.worker.future.result(timeout=3)
        self.ack_failure = True
        self.worker.tick()
        self.assertEqual(self.core.result(task['id'])['task']['state'], 'completed')
        self.assertIsNotNone(self.worker.pending)
        self.worker.tick()
        self.assertIsNone(self.worker.active)
        self.assertEqual(self.worker.completed, 1)
        self.assertEqual(self.core.result(task['id'])['counts']['included'], 1)

    def test_main_resume_releases_a_locally_paused_node(self):
        self.worker.control('pause')
        self.worker.tick()
        self.core.node_action(self.worker.node_id, 'resume')
        self.worker.tick()
        self.assertTrue(self.worker.enabled)

    def test_revoke_cancels_fetch_and_allows_pairing_again(self):
        self.core.node_action(self.worker.node_id, 'remove')
        self.worker.tick()
        self.assertEqual(self.worker.snapshot()['connection'], 'pairing required')
        self.assertEqual(self.worker.token, '')
        self.worker.connect({'url': 'http://127.0.0.1:8010', 'code': self.core.new_pairing()['code'], 'name': 'Repaired'})
        self.assertEqual(self.worker.snapshot()['connection'], 'connected')

    def test_connection_inputs_and_deduplicated_ids(self):
        self.assertEqual(video_ids(bvid(1) + ', https://www.bilibili.com/video/' + bvid(1)), [bvid(1)])
        for value in ('', 'https://example.com/path', 'ftp://127.0.0.1', 'http://user:password@localhost', 'http://localhost:0', 'http://localhost?q=x'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                coordinator_url(value)
        for value in (0, 5, 'nan', 'inf', 'bad'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                frequency(value)
