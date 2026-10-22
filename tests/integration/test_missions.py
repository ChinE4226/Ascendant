"""Independent creator targets, retained RAM datasets, explicit saves and resumable missions."""

import asyncio
from collections import OrderedDict
from contextlib import ExitStack
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch

from ascendant import tracking
from ascendant.web import actions, creators, dataset, missions, plots, snapshots, state
from tests.integration import test_tracking as fixtures


class MissionTests(unittest.TestCase):
    def setUp(self):
        fixtures.TrackingTests.setUp(self)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.original = {'key': ('42', None, '{}'), 'data': ([fixtures.info(42)], 'Original', 1),
                         'meta': {'uid': '42', 'count': 1, 'source_kind': 'creator', 'selection': 'Original'}}
        for target, name, value in ((missions, 'MISSIONS', OrderedDict()), (missions, 'RUNNING', False),
                (missions, 'STOP_REQUESTED', False), (missions, 'WORKER', None),
                (dataset, 'MISSION_COLLECTIONS', OrderedDict()), (dataset, 'COHORTS', OrderedDict()),
                (dataset, 'CURRENT', deepcopy(self.original)), (plots, '_pending_plots', OrderedDict()),
                (state, 'SELECTED_UID', '42')):
            self.stack.enter_context(patch.object(target, name, value))
        self.stack.enter_context(patch.object(creators, 'load_web_creators', return_value=[{'uid': str(uid), 'name': f'Creator {uid}'} for uid in (7, 8, 9, 42)]))
        self.account = self.stack.enter_context(patch.object(missions.accounts, 'active_account_id', return_value=None))
        self.targets = []
        async def fetch(payload):
            uid = creators.selected_creator()['uid']
            self.targets.append(uid)
            video = fixtures.info(int(uid)*100, f'Video from {uid}')
            video['owner'] = {'mid': int(uid), 'name': f'Creator {uid}'}
            video['bvid'] = { '7':'BV1xx411c7mD', '8':'BV1xx411c7mE', '9':'BV1xx411c7mF' }.get(uid,fixtures.BVID)
            video['stat']['like'] = int(uid)*10
            return [video], 'First video', 10, {'requested': 1, 'examined': 1, 'skipped_invalid': 0, 'skipped_duplicates': 0}
        self.fetch = self.stack.enter_context(patch.object(missions.distributed_dataset, 'fetch_workspace_items', new_callable=AsyncMock, side_effect=fetch))
        self.addCleanup(self.join_worker)

    def join_worker(self):
        if missions.WORKER is not None:
            missions.WORKER.join(timeout=5)
            self.assertFalse(missions.WORKER.is_alive())

    def add(self, uid='7', steps=None, **extra):
        return missions.add({'source': 'fetch', 'creator_uid': uid, 'selection': {'kind': 'position', 'start': 1, 'end': 1},
                             'steps': ['analysis'] if steps is None else steps, **extra})

    def run_queue(self):
        missions.start()
        self.join_worker()
        self.assertFalse(missions.overview()['running'])

    def test_a_save_analyze_b_plot_c_ratio_preserves_every_target(self):
        a = self.add('7', ['snapshot', 'analysis'], name='A')
        b = self.add('8', ['plot'], name='B')
        c = self.add('9', ['division'], name='C', numerator='likes', denominator='views')
        self.run_queue()
        self.assertEqual(self.targets, ['7', '8', '9'])
        self.assertEqual(creators.selected_creator()['uid'], '42')
        self.assertEqual(dataset.CURRENT, self.original)
        self.assertEqual([row['state'] for row in missions.overview()['missions']], ['completed']*3)
        self.assertEqual(missions.detail(a['id'])['videos'][0]['views'], 700)
        self.assertEqual(missions.detail(b['id'])['results']['plot']['points'][0]['value'], 800)
        self.assertEqual(missions.detail(b['id'])['results']['plot']['selected_creator']['uid'], '8')
        self.assertAlmostEqual(missions.detail(c['id'])['results']['division']['ratio'], .1)
        batches = tracking.snapshot_overview()['batches']
        self.assertEqual(len(batches), 1)
        self.assertEqual(tracking.collection_history(batches[0]['id'])['videos'][0]['views'], 700)
        self.assertEqual(len(dataset.MISSION_COLLECTIONS), 3)

    def test_no_save_step_never_creates_a_snapshot_and_fetch_only_is_valid(self):
        row = self.add(steps=[])
        self.run_queue()
        self.assertEqual(missions.detail(row['id'])['completed_steps'], ['fetch'])
        self.assertEqual(tracking.snapshot_overview()['batches'], [])

    def test_stop_then_resume_does_not_refetch_or_duplicate_saved_steps(self):
        row = self.add(steps=['snapshot', 'analysis'])
        original = self.fetch.side_effect
        async def stop_during_fetch(payload):
            result = await original(payload)
            missions.stop()
            return result
        self.fetch.side_effect = stop_during_fetch
        self.run_queue()
        self.assertEqual(missions.detail(row['id'])['state'], 'paused')
        self.assertEqual(missions.detail(row['id'])['completed_steps'], ['fetch'])
        self.assertEqual(tracking.snapshot_overview()['batches'], [])
        self.run_queue()
        self.fetch.assert_awaited_once()
        self.assertEqual(missions.detail(row['id'])['state'], 'completed')
        self.assertEqual(len(tracking.snapshot_overview()['batches']), 1)

    def test_failure_stops_later_missions_and_retry_preserves_completed_saves(self):
        a = self.add(steps=['snapshot', 'analysis'])
        b = self.add('8', ['plot'])
        with patch.object(actions, '_execute_video_action', new_callable=AsyncMock, side_effect=ValueError('Analysis failed')):
            self.run_queue()
        self.assertEqual(missions.detail(a['id'])['completed_steps'], ['fetch', 'snapshot'])
        self.assertEqual(missions.detail(a['id'])['state'], 'failed')
        self.assertEqual(missions.detail(b['id'])['state'], 'queued')
        self.run_queue()
        self.assertEqual(self.targets, ['7', '8'])
        self.assertEqual(len(tracking.snapshot_overview()['batches']), 1)

    def test_loaded_source_is_copied_at_enqueue_and_survives_source_eviction(self):
        meta = dataset.retain_collection([fixtures.info(333)], kind='random', label='Sample',
            started_at=fixtures.TIME, collected_at=fixtures.TIME, collection={}, scope={'seed': 'test'})
        row = missions.add({'source':'loaded', 'source_collection_id':meta['collection_id'], 'steps':['analysis']})
        dataset.COHORTS.clear()
        self.run_queue()
        self.fetch.assert_not_awaited()
        self.assertEqual(missions.detail(row['id'])['videos'][0]['views'], 333)

    def test_sample_eviction_does_not_evict_missions_and_results_are_copies(self):
        rows = [self.add(steps=[]) for _ in range(6)]
        self.run_queue()
        for i in range(6):
            dataset.retain_collection([], kind='weekly', label=str(i), started_at=fixtures.TIME,
                collected_at=fixtures.TIME, collection={}, scope={})
        self.assertEqual(len(dataset.MISSION_COLLECTIONS), 6)
        self.assertEqual(len(dataset.COHORTS), 4)
        detail = missions.detail(rows[0]['id'])
        detail['videos'][0]['views'] = 99999
        self.assertEqual(missions.detail(rows[0]['id'])['videos'][0]['views'], 700)

    def test_reorder_changes_execution_order_and_removal_releases_only_owned_data(self):
        a, b = self.add(), self.add('8')
        missions.change(b['id'], 'up')
        self.run_queue()
        self.assertEqual(self.targets, ['8', '7'])
        missions.change(a['id'], 'remove')
        self.assertEqual(missions.detail(b['id'])['videos'][0]['views'], 800)
        self.assertNotIn(a['collection_id'], dataset.MISSION_COLLECTIONS)

    def test_queue_capacity_and_invalid_config_do_not_drop_existing_data(self):
        with patch.object(missions, 'MAX_MISSIONS', 2):
            self.add(); self.add('8')
            with self.assertRaisesRegex(ValueError, 'queue can retain'):
                self.add('9')
        for extra in ({'creator_uid':'bad'}, {'steps':['save-everything']}, {'steps':[{}]}, {'local_filter':'bad'}, {'local_filter':{'minimum_views':20,'maximum_views':10}}):
            with self.assertRaises(ValueError): self.add(**extra)
        self.assertEqual(len(missions.overview()['missions']), 2)

    def test_loaded_mission_survives_removal_of_original_mission(self):
        original = self.add(steps=[])
        self.run_queue()
        copy = missions.add({'source':'loaded', 'source_collection_id':original['collection_id'], 'steps':['analysis']})
        missions.change(original['id'], 'remove')
        self.run_queue()
        self.assertEqual(missions.detail(copy['id'])['videos'][0]['views'], 700)
        self.fetch.assert_awaited_once()

    def test_creator_followers_use_retained_target_and_never_the_selected_creator(self):
        row = self.add('8', ['division'], denominator='followers')
        with patch.object(actions, 'fetch_followers', new_callable=AsyncMock, return_value=1000) as followers:
            self.run_queue()
        followers.assert_awaited_once_with(creator_uid='8')
        self.assertAlmostEqual(missions.detail(row['id'])['results']['division']['ratio'], .08)

    def test_account_change_or_busy_dataset_stops_before_fetching(self):
        row = self.add()
        self.account.return_value = 'another-account'
        self.run_queue()
        self.fetch.assert_not_awaited()
        self.account.return_value = None
        with dataset.LOCK:
            self.run_queue()
        self.fetch.assert_not_awaited()
        self.assertEqual(missions.detail(row['id'])['state'], 'failed')
        self.run_queue()
        self.assertEqual(missions.detail(row['id'])['state'], 'completed')

    def test_fresh_snapshot_of_retained_creator_uses_its_own_recipe(self):
        row = self.add('8', [])
        self.run_queue()
        result = asyncio.run(snapshots.capture_collection({'mode':'fresh','collection_id':row['collection_id']}))
        self.assertEqual(self.targets, ['8','8'])
        self.assertEqual(tracking.collection_history(result['batch']['id'])['videos'][0]['views'], 800)
        self.assertEqual(creators.selected_creator()['uid'], '42')
        self.assertEqual(dataset.CURRENT, self.original)
