"""Explicit collection choices persist only selected observations, never all RAM."""

import asyncio
from collections import OrderedDict
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

from ascendant import tracking
from ascendant.web import dataset, distributed_dataset, sampling, weekly
from ascendant.web import snapshots as service
from tests.integration import test_tracking as fixtures

info, BVID, OTHER, TIME = fixtures.info, fixtures.BVID, fixtures.OTHER, fixtures.TIME


class CollectionSnapshotTests(unittest.TestCase):
    def setUp(self):
        fixtures.TrackingTests.setUp(self)
        self.rows = [info(), {**info(200), 'bvid': OTHER}]
        self.meta = {'source_kind': 'creator', 'source_label': 'Creator 7', 'uid': '7',
                     'collected_at': TIME, 'count': 2, 'selection': 'First 2 videos',
                     'collection': {'requested': 2, 'examined': 2}}
        self.current = {'key': ('7', None, '{"end": 2}'),
                        'data': (deepcopy(self.rows), 'First 2 videos', 20), 'meta': deepcopy(self.meta)}
        for target, name, value in ((dataset, 'CURRENT', self.current), (dataset, 'COHORTS', OrderedDict()),
                (dataset, 'context_key', lambda _: ('7', None, '{}')),
                (dataset, 'selected_creator', lambda: {'uid': '7', 'name': 'Creator 7'})):
            replacement = patch.object(target, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def observation_count(self):
        with tracking.connection() as db:
            return db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]

    def sample(self, kind='random'):
        scope = {'keyword': 'camera', 'seed': 'test', 'sample_size': 2, 'pool_size': 2} if kind == 'random' else {'number': 393}
        return dataset.retain_collection([info(300)], kind=kind, label='Selected sample',
            started_at=TIME, collected_at='2026-10-08T03:00:00Z', collection={}, scope=scope)

    def capture(self, mode='loaded', identity='creator', **extra):
        return asyncio.run(service.capture_collection({'mode': mode, 'collection_id': identity, **extra}))['batch']

    def test_loaded_creator_saves_whole_collection_without_fetch_or_other_ram(self):
        self.sample()
        original = deepcopy(self.current)
        with patch.object(distributed_dataset, 'fetch_workspace_items', new_callable=AsyncMock) as fetch:
            batch = self.capture()
        fetch.assert_not_awaited()
        self.assertEqual(dataset.CURRENT, original)
        self.assertEqual(batch['video_count'], 2)
        self.assertEqual(batch['collected_at'], tracking.utc_time(TIME))
        self.assertNotEqual(batch['saved_at'], batch['collected_at'])
        self.assertEqual(json.loads(batch['scope_json']), {'uid': '7', 'selection': {'end': 2}})
        self.assertEqual([row['views'] for row in tracking.collection_history(batch['id'])['videos']], [100, 200])
        self.assertEqual(self.observation_count(), 2)

    def test_loaded_sample_saves_only_selected_sample(self):
        selected = self.sample()
        self.sample('weekly')
        batch = self.capture(identity=selected['collection_id'])
        self.assertEqual(batch['collection_id'], selected['collection_id'])
        self.assertEqual(batch['source_kind'], 'random')
        self.assertEqual(tracking.collection_history(batch['id'])['videos'][0]['views'], 300)
        self.assertEqual(tracking.history(BVID)['total'], 0)
        self.assertEqual(tracking.history(OTHER)['total'], 0)
        self.assertEqual(len(dataset.COHORTS), 2)

    def test_inline_save_rejects_a_replaced_collection_without_saving(self):
        for identity in ('creator', self.sample()['collection_id']):
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, 'collection changed'):
                self.capture(identity=identity, expected_collected_at='outdated collection time')
        self.assertEqual(self.observation_count(), 0)
        self.assertFalse(dataset.LOCK.locked())

    def test_inline_save_accepts_the_displayed_collection(self):
        batch = self.capture(expected_collected_at=TIME)
        self.assertEqual(batch['video_count'], 2)

    def test_resaving_loaded_collection_reuses_observations(self):
        first, second = self.capture(), self.capture()
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(len(tracking.snapshot_overview()['batches']), 2)
        self.assertEqual(self.observation_count(), 2)
        self.assertEqual(tracking.history(BVID)['total'], 0)

    def test_fresh_creator_saves_new_result_and_preserves_loaded_rows(self):
        self.sample()
        original = deepcopy((dataset.CURRENT, dataset.COHORTS))
        with patch.object(distributed_dataset, 'fetch_workspace_items', new_callable=AsyncMock,
                          return_value=([info(999)], 'New selection', 20, {'requested': 1})) as fetch:
            batch = self.capture(mode='fresh', selection={'end': 1}, fetch_source='local')
        fetch.assert_awaited_once_with({'mode': 'fresh', 'collection_id': 'creator', 'selection': {'end': 1}, 'fetch_source': 'local'})
        self.assertEqual((dataset.CURRENT, dataset.COHORTS), original)
        self.assertEqual(batch['capture_mode'], 'fresh')
        self.assertEqual(batch['video_count'], 1)
        self.assertNotEqual(batch['collected_at'], tracking.utc_time(TIME))
        self.assertEqual(tracking.collection_history(batch['id'])['videos'][0]['views'], 999)
        self.assertEqual(tracking.history(BVID)['total'], 0)
        self.assertEqual(tracking.history(OTHER)['total'], 0)
        self.assertFalse(dataset.LOCK.locked())

    def test_fresh_samples_use_original_recipe_and_save_only_new_rows(self):
        for kind, module, method in (('random', sampling, 'fetch_random_sample'), ('weekly', weekly, 'fetch_weekly_analysis')):
            with self.subTest(kind=kind):
                selected = self.sample(kind)
                original = deepcopy((dataset.CURRENT, dataset.COHORTS))
                fresh_meta = {**selected, 'collected_at': '2026-10-08T04:00:00Z' if kind == 'random' else '2026-10-08T05:00:00Z'}
                with patch.object(module, method, new_callable=AsyncMock, return_value=([info(888)], fresh_meta)) as fetch:
                    batch = self.capture(mode='fresh', identity=selected['collection_id'])
                payload = selected['scope'] if kind == 'random' else {'source': 393}
                fetch.assert_awaited_once_with(payload, snapshot_only=True)
                self.assertEqual((dataset.CURRENT, dataset.COHORTS), original)
                self.assertEqual(tracking.collection_history(batch['id'])['videos'][0]['views'], 888)
        self.assertEqual(self.observation_count(), 2)

    def test_expired_empty_invalid_and_busy_choices_never_save(self):
        for mode in ('loaded', 'fresh'):
            with self.assertRaisesRegex(ValueError, 'expired'):
                self.capture(mode=mode, identity='expired')
        with self.assertRaisesRegex(ValueError, 'Choose'):
            self.capture(mode='invalid')
        with dataset.LOCK, self.assertRaises(service.SnapshotBusy):
            self.capture()
        dataset.CURRENT['data'] = ([], 'Empty', 0)
        with self.assertRaisesRegex(ValueError, 'no videos'):
            self.capture()
        self.assertEqual(tracking.overview()['counts']['snapshot_batches'], 0)

    def test_failed_fresh_fetch_keeps_ram_and_database_untouched(self):
        with patch.object(distributed_dataset, 'fetch_workspace_items', new_callable=AsyncMock, side_effect=ValueError('Unavailable')):
            with self.assertRaisesRegex(ValueError, 'Unavailable'):
                self.capture(mode='fresh')
        self.assertIs(dataset.CURRENT, self.current)
        self.assertFalse(dataset.LOCK.locked())
        self.assertFalse(self.path.exists())

    def test_batch_validation_rolls_back_all_rows(self):
        for bad in ({**info(), 'bvid': 'bad'}, {'bvid': OTHER, 'stat': {}}, info()):
            with self.subTest(bad=bad), self.assertRaises((ValueError, sqlite3.IntegrityError)):
                tracking.save_collection([info(), bad], self.meta, mode='loaded')
            counts = tracking.overview()['counts']
            self.assertEqual((counts['videos'], counts['snapshots'], counts['snapshot_batches']), (0, 0, 0))

    def test_older_loaded_data_does_not_replace_latest_metadata(self):
        tracking.save_snapshot(info(500, 'New title'), collected_at='2026-10-08T06:00:00Z')
        self.capture()
        self.assertEqual(tracking.overview()['videos'][0]['title'], 'New title')
        self.assertIsNone(tracking.history(BVID)['snapshots'][0]['views_change'])
        self.assertEqual(tracking.analyse_history(BVID)['summary']['observations'], 1)

    def test_version_one_migration_preserves_observations_and_trackers(self):
        self.path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            db.executescript(tracking.SCHEMA_FILE.read_text())
            db.execute('PRAGMA user_version = 1')
            tracking._insert_snapshot(db, info(), collected_at=TIME)
            db.execute("INSERT INTO trackers(bvid, status, interval_seconds, created_at) VALUES (?, 'paused', 600, ?)", (BVID, TIME))
        self.assertEqual(tracking.initialize()['schema_version'], tracking.SCHEMA_VERSION)
        self.assertEqual(tracking.history(BVID)['snapshots'][0]['views'], 100)
        self.assertEqual(tracking.get_tracker(1)['status'], 'paused')
        self.capture()
        self.assertEqual(tracking.history(BVID)['total'], 1)
