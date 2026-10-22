"""Explicit RAM release preserves saved data, other sources and mission copies."""

from collections import OrderedDict
from contextlib import ExitStack
import unittest
from unittest.mock import AsyncMock, patch

from ascendant import tracking
from ascendant.web import actions, dataset, plots, snapshots
from tests.integration import test_tracking as fixtures


class MemoryReleaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.TrackingTests.setUp(self)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module, name, value in ((dataset, 'CURRENT', None), (dataset, 'COHORTS', OrderedDict()),
                (dataset, 'MISSION_COLLECTIONS', OrderedDict()), (plots, '_pending_plots', OrderedDict())):
            self.stack.enter_context(patch.object(module, name, value))
        self.creator = self.stack.enter_context(patch.object(dataset, 'selected_creator', return_value={'uid': '7', 'name': 'Creator'}))
        self.account = self.stack.enter_context(patch.object(dataset.accounts, 'active_account_id', return_value=None))
        self.fetch = self.stack.enter_context(patch.object(actions, 'fetch_workspace_items', new_callable=AsyncMock,
            return_value=([fixtures.info()], 'First video', 1, {})))

    def sample(self):
        return dataset.retain_collection([fixtures.info()], kind='random', label='Sample',
            started_at=fixtures.TIME, collected_at=fixtures.TIME, collection={}, scope={})

    def chart(self, identity):
        return plots.prepare_plot({'uid': '7'}, 'First video', 'Views', 'Views',
            [{'label': '2026-10-08', 'value': 100}], collection_id=identity)

    async def test_creator_release_preserves_snapshot_tracking_and_independent_copies(self):
        await actions.execute_video_action({'action': 'list', 'refresh': True})
        items, meta = dataset.snapshot_collection('creator')
        batch = (await snapshots.capture_collection({'mode': 'loaded', 'collection_id': 'creator'}))['batch']
        tracking.save_observation(fixtures.info(), collected_at=fixtures.TIME)
        sample = self.sample()
        dataset.retain_mission_collection('mission-copy', items, {**meta, 'mission_id': 'copy'})
        released_plot, kept_plot = self.chart('creator'), self.chart(sample['collection_id'])
        self.assertEqual(dataset.release_collection('creator', expected_collected_at=meta['collected_at']), {'released': 'creator'})
        self.assertIsNone(dataset.CURRENT)
        self.assertIsNone(dataset.workspace_data()['creator'])
        with self.assertRaisesRegex(ValueError, 'Fetch / Refresh'):
            await actions.execute_video_action({'action': 'analysis', 'reuse_only': True})
        self.assertEqual(dataset.snapshot_collection('mission-copy')[0], items)
        self.assertEqual(len(dataset.snapshot_collection(sample['collection_id'])[0]), 1)
        self.assertEqual(tracking.collection_history(batch['id'])['videos'][0]['views'], 100)
        self.assertEqual(tracking.history(fixtures.BVID)['total'], 1)
        self.assertNotIn(released_plot, plots._pending_plots)
        self.assertIn(kept_plot, plots._pending_plots)
        self.fetch.assert_awaited_once()

    async def test_sample_release_removes_rows_report_and_exports_only_for_that_sample(self):
        await actions.execute_video_action({'action': 'list', 'refresh': True})
        first, second = self.sample(), self.sample()
        identity = first['collection_id']
        batch = (await snapshots.capture_collection({'mode': 'loaded', 'collection_id': identity}))['batch']
        with dataset.COHORT_LOCK:
            dataset.COHORTS[identity]['report'] = {'sampling': {'keyword': 'released'}}
        self.assertIn('random', dataset.workspace_data()['reports'])
        released_plot, kept_plot = self.chart(identity), self.chart(second['collection_id'])
        dataset.release_collection(identity, expected_collected_at=first['collected_at'])
        self.assertEqual(dataset.workspace_data()['reports'], {})
        with self.assertRaisesRegex(ValueError, 'expired'):
            await dataset.acquire({'collection_id': identity}, self.fetch)
        self.assertIsNotNone(dataset.CURRENT)
        self.assertEqual(len(dataset.snapshot_collection(second['collection_id'])[0]), 1)
        self.assertEqual(tracking.collection_history(batch['id'])['batch']['video_count'], 1)
        self.assertNotIn(released_plot, plots._pending_plots)
        self.assertIn(kept_plot, plots._pending_plots)

    async def test_stale_context_and_busy_operation_cannot_release_another_dataset(self):
        await actions.execute_video_action({'action': 'list', 'refresh': True})
        meta = dataset.creator_metadata()
        with self.assertRaisesRegex(ValueError, 'changed'):
            dataset.release_collection('creator', expected_collected_at=fixtures.TIME)
        with dataset.LOCK, self.assertRaisesRegex(ValueError, 'Another video operation'):
            dataset.release_collection('creator', expected_collected_at=meta['collected_at'])
        for target, value in ((self.creator, {'uid': '8'}), (self.account, 'other')):
            old = target.return_value
            target.return_value = value
            with self.assertRaisesRegex(ValueError, 'No dataset'):
                dataset.release_collection('creator', expected_collected_at=meta['collected_at'])
            target.return_value = old
        self.assertIsNotNone(dataset.CURRENT)
        self.assertFalse(dataset.LOCK.locked())
        self.fetch.assert_awaited_once()

    async def test_mission_requires_removal_and_stale_sample_is_preserved(self):
        meta = self.sample()
        dataset.retain_mission_collection('mission-copy', [fixtures.info()], {**meta, 'mission_id': 'copy'})
        with self.assertRaisesRegex(ValueError, 'Tasks'):
            dataset.release_collection('mission-copy', expected_collected_at=meta['collected_at'])
        with self.assertRaisesRegex(ValueError, 'changed'):
            dataset.release_collection(meta['collection_id'], expected_collected_at='old')
        for identity, timestamp in ((None, fixtures.TIME), ('creator', None), ('unknown', fixtures.TIME)):
            with self.assertRaises(ValueError):
                dataset.release_collection(identity, expected_collected_at=timestamp)
        mission_plot = self.chart('mission-copy')
        dataset.remove_mission_collection('mission-copy')
        self.assertNotIn(mission_plot, plots._pending_plots)
        self.assertEqual(len(dataset.snapshot_collection(meta['collection_id'])[0]), 1)
