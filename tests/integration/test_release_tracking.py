"""Release baselines, durable discovery and independent video monitoring."""

import asyncio
from contextlib import closing
from copy import deepcopy
from datetime import datetime
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

from ascendant import tracking
from ascendant.web import dataset
from ascendant.web import tracking as service
from tests.integration import test_tracking as fixtures

BVID, OTHER, TIME = fixtures.BVID, fixtures.OTHER, fixtures.TIME


def release(bvid=BVID, published='2026-10-08T01:00:00+00:00'):
    return {'bvid': bvid, 'title': 'Release', 'created': int(datetime.fromisoformat(published).timestamp())}


class ReleaseTrackingTests(unittest.TestCase):
    def setUp(self):
        fixtures.TrackingTests.setUp(self)

    def watch(self):
        return tracking.create_creator_watch('7', 600, 120, now=TIME)

    def test_baseline_is_not_a_new_release_then_new_video_is_enrolled_once(self):
        watch = self.watch()
        first = tracking.record_creator_scan(watch['id'], [release()], checked_at=TIME)
        self.assertTrue(first['baseline'])
        self.assertEqual(first['releases'], [])
        self.assertEqual(tracking.overview()['counts']['trackers'], 0)
        rows = [release(OTHER, '2026-10-08T02:05:00+00:00'), release()]
        second = tracking.record_creator_scan(watch['id'], rows, checked_at='2026-10-08T02:10:00Z')
        self.assertEqual(len(second['releases']), 1)
        video_tracker = tracking.get_tracker(second['releases'][0]['tracker_id'])
        self.assertEqual(video_tracker['interval_seconds'], 120)
        self.assertEqual(video_tracker['bvid'], OTHER)
        self.assertEqual(tracking.record_creator_scan(watch['id'], rows)['releases'], [])
        tracking.initialize()
        self.assertEqual(len(tracking.overview()['releases']), 1)
        self.assertEqual(tracking.history(OTHER)['total'], 0)  # Discovery counts are never metric observations.

    def test_old_upload_surfacing_later_is_not_reported_as_a_release(self):
        watch = self.watch()
        tracking.record_creator_scan(watch['id'], [], checked_at=TIME)
        result = tracking.record_creator_scan(watch['id'], [release()], checked_at='2026-10-08T03:00:00Z')
        self.assertEqual(result['releases'], [])
        self.assertEqual(tracking.overview()['counts']['trackers'], 0)

    def test_existing_paused_video_tracker_and_creator_pause_are_preserved(self):
        video_tracker = tracking.create_tracker(OTHER, 3600, now=TIME)
        tracking.update_tracker(video_tracker['id'], status='paused')
        watch = self.watch()
        tracking.record_creator_scan(watch['id'], [], checked_at=TIME)
        tracking.update_creator_watch(watch['id'], status='paused')
        result = tracking.record_creator_scan(watch['id'], [release(OTHER, '2026-10-08T02:05:00+00:00')])
        self.assertEqual(result['releases'][0]['tracker_id'], video_tracker['id'])
        self.assertEqual(tracking.get_tracker(video_tracker['id'])['status'], 'paused')
        self.assertEqual(tracking.get_tracker(video_tracker['id'])['interval_seconds'], 3600)
        self.assertIsNone(tracking.get_creator_watch(watch['id'])['next_check_at'])
        tracking.update_creator_watch(watch['id'], status='active', now=TIME)
        self.assertEqual(tracking.due_creator_watch(now=TIME)['id'], watch['id'])

    def test_invalid_scan_rolls_back_new_trackers_and_seen_ids(self):
        watch = self.watch()
        tracking.record_creator_scan(watch['id'], [], checked_at=TIME)
        with self.assertRaises(ValueError):
            tracking.record_creator_scan(watch['id'], [release(OTHER, '2026-10-08T02:05:00+00:00'), {'bvid': 'bad'}])
        self.assertEqual(tracking.creator_seen_ids(watch['id']), set())
        self.assertEqual(tracking.overview()['counts']['trackers'], 0)

    def test_worker_discovers_then_collects_new_video_without_mutating_ram(self):
        original = {'unchanged': 'RAM dataset'}
        watch = self.watch()
        with patch.object(dataset, 'CURRENT', original), patch.object(service, 'fetch_releases', new_callable=AsyncMock, return_value=[]):
            service.TrackingWorker().collect_due()
        tracking.update_creator_watch(watch['id'], status='active', now=TIME)
        fresh = release(OTHER, '2099-01-01T00:00:00+00:00')
        with patch.object(dataset, 'CURRENT', original), patch.object(service, 'fetch_releases', new_callable=AsyncMock, return_value=[fresh]), patch.object(service, 'fetch_info', new_callable=AsyncMock, return_value={**fixtures.info(999), 'bvid': OTHER}) as fetch:
            worker = service.TrackingWorker()
            worker.collect_due()
            fetch.assert_not_awaited()
            worker.collect_due()
            fetch.assert_awaited_once_with(OTHER)
            self.assertIs(dataset.CURRENT, original)
        self.assertEqual(tracking.analyse_history(OTHER)['summary']['latest_value'], 999)

    def test_failed_busy_and_paused_checks_do_not_invent_releases(self):
        watch = self.watch()
        with patch.object(service, 'fetch_releases', new_callable=AsyncMock, side_effect=RuntimeError('private-cookie')) as fetch:
            with dataset.LOCK, self.assertRaises(service.CollectionBusy):
                asyncio.run(service.check_creator(watch['id']))
            fetch.assert_not_awaited()
            with self.assertRaises(ValueError):
                asyncio.run(service.check_creator(watch['id']))
        row = tracking.get_creator_watch(watch['id'])
        self.assertNotIn('private-cookie', row['last_error'])
        self.assertIsNone(row['baseline_at'])
        self.assertEqual(tracking.overview()['releases'], [])
        self.assertFalse(dataset.LOCK.locked())
        tracking.update_creator_watch(watch['id'], status='paused')
        with patch.object(service, 'fetch_releases', new_callable=AsyncMock) as fetch:
            self.assertIsNone(asyncio.run(service.check_creator(watch['id'], scheduled=True)))
            fetch.assert_not_awaited()

    def test_release_fetch_paginates_and_handles_a_pinned_old_video(self):
        watch = self.watch()
        tracking.record_creator_scan(watch['id'], [release()], checked_at=TIME)
        watch = tracking.get_creator_watch(watch['id'])
        newest = [release(f'BV{i:010d}', '2026-10-08T03:00:00+00:00') for i in range(49)]
        pages = [{'list': {'vlist': [release(), *newest]}}, {'list': {'vlist': [release(OTHER, '2026-10-08T02:05:00+00:00'), release()]}}]
        with patch.object(service.client, 'configure_bilibili_client'), patch.object(service.client, 'close_bilibili_client', new_callable=AsyncMock), patch.object(service.accounts, 'credential_from_env', return_value=None), patch.object(service.asyncio, 'sleep', new_callable=AsyncMock), patch.object(service.videos, 'fetch_creator_video_page', new_callable=AsyncMock, side_effect=pages) as fetch:
            rows = asyncio.run(service.fetch_releases(watch))
        self.assertEqual(fetch.await_count, 2)
        self.assertEqual(len(rows), 51)
        self.assertIn(OTHER, {r['bvid'] for r in rows})

    def test_known_boundary_uses_lookup_without_loading_all_seen_ids(self):
        watch = self.watch()
        tracking.record_creator_scan(watch['id'], [release()], checked_at=TIME)
        watch = tracking.get_creator_watch(watch['id'])
        self.assertTrue(tracking.creator_has_seen(watch['id'], BVID))
        self.assertFalse(tracking.creator_has_seen(watch['id'], OTHER))
        other_watch = tracking.create_creator_watch('8', now=TIME)
        self.assertFalse(tracking.creator_has_seen(other_watch['id'], BVID))
        page = {'list': {'vlist': [release(f'BV{i:010d}', '2026-10-08T03:00:00+00:00') for i in range(49)] + [release()]}}
        with patch.object(tracking, 'creator_seen_ids', side_effect=AssertionError('Full history loaded')), patch.object(service.client, 'configure_bilibili_client'), patch.object(service.client, 'close_bilibili_client', new_callable=AsyncMock), patch.object(service.accounts, 'credential_from_env', return_value=None), patch.object(service.asyncio, 'sleep', new_callable=AsyncMock), patch.object(service.videos, 'fetch_creator_video_page', new_callable=AsyncMock, return_value=page) as fetch:
            rows = asyncio.run(service.fetch_releases(watch))
        self.assertEqual(len(rows), 50)
        self.assertEqual(fetch.await_count, 1)

    def test_version_two_migration_separates_batches_from_legacy_tracking(self):
        self.path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            db.executescript(tracking.SCHEMA_FILE.read_text() + tracking.COLLECTIONS_SCHEMA_FILE.read_text())
            db.execute('PRAGMA user_version = 2')
            first = tracking._insert_snapshot(db, fixtures.info(100), collected_at=TIME)
            second = tracking._insert_snapshot(db, fixtures.info(900), collected_at='2026-10-08T03:00:00Z')
            batch = db.execute("INSERT INTO snapshot_batches(label, source_kind, capture_mode, started_at, collected_at, saved_at, video_count, scope_json, collection_json) VALUES ('Dataset', 'creator', 'loaded', ?, ?, ?, 1, '{}', '{}')", (TIME, TIME, TIME)).lastrowid
            db.execute('INSERT INTO snapshot_batch_items(batch_id, snapshot_id, position) VALUES (?, ?, 0)', (batch, second))
        tracking.initialize()
        self.assertEqual(tracking.history(BVID)['total'], 1)
        self.assertEqual(tracking.history(BVID)['snapshots'][0]['id'], first)
        self.assertEqual(tracking.collection_history(batch)['videos'][0]['views'], 900)
        self.assertEqual(tracking.analyse_history(BVID)['summary']['latest_value'], 100)

    def test_bounded_discovery_fails_without_a_partial_scan(self):
        watch = self.watch()
        tracking.record_creator_scan(watch['id'], [], checked_at=TIME)
        watch = tracking.get_creator_watch(watch['id'])
        pages = [{'list': {'vlist': [release(f'BV{i:010d}', '2026-10-08T03:00:00+00:00') for i in range(page * 50, (page + 1) * 50)]}} for page in range(10)]
        with patch.object(service.client, 'configure_bilibili_client'), patch.object(service.client, 'close_bilibili_client', new_callable=AsyncMock), patch.object(service.accounts, 'credential_from_env', return_value=None), patch.object(service.asyncio, 'sleep', new_callable=AsyncMock), patch.object(service.videos, 'fetch_creator_video_page', new_callable=AsyncMock, side_effect=pages) as fetch:
            with self.assertRaisesRegex(ValueError, '500 uploads'):
                asyncio.run(service.check_creator(watch['id']))
        self.assertEqual(fetch.await_count, 10)
        self.assertEqual(tracking.creator_seen_ids(watch['id']), set())
        self.assertEqual(tracking.overview()['counts']['trackers'], 0)
        self.assertIn('500 uploads', tracking.get_creator_watch(watch['id'])['last_error'])

    def test_pause_while_creator_scan_is_running_preserves_pause(self):
        watch = self.watch()
        async def pause(_):
            tracking.update_creator_watch(watch['id'], status='paused')
            return [release()]
        with patch.object(service, 'fetch_releases', side_effect=pause):
            asyncio.run(service.check_creator(watch['id']))
        row = tracking.get_creator_watch(watch['id'])
        self.assertEqual(row['status'], 'paused')
        self.assertIsNone(row['next_check_at'])
        self.assertIsNotNone(row['baseline_at'])
