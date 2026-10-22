"""Durable history, transactions, backups, and scheduled collection without internet."""

import asyncio
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from ascendant import config, tracking
from ascendant.web import dataset
from ascendant.web import tracking as service

BVID = 'BV1xx411c7mD'
OTHER = 'BV1xx411c7mE'
TIME = '2026-10-08T02:00:00+00:00'


def info(views=100, title='Example'):
    return {'bvid': BVID, 'aid': 42, 'title': title, 'pubdate': 1791417600,
            'owner': {'mid': 7, 'name': 'Creator'},
            'stat': {'view': views, 'like': 0, 'coin': 2, 'favorite': 3, 'reply': 4, 'share': 5, 'danmaku': 6},
            'private_cookie': 'never-store-me'}


class TrackingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'data' / 'tracking.sqlite3'
        self.patch = patch.object(config, 'TRACKING_DB', self.path)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_initialize_is_repeatable_and_concurrent(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: tracking.initialize(), range(8)))
        self.assertTrue(all(result['schema_version'] == tracking.SCHEMA_VERSION for result in results))
        with tracking.connection() as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], 0)

    def test_append_only_history_metadata_and_growth(self):
        first = tracking.save_snapshot(info(), collected_at=TIME)
        second = tracking.save_snapshot(info(160, 'Updated title'), collected_at='2026-10-08T02:30:00Z')
        self.assertNotEqual(first['id'], second['id'])
        result = tracking.history(BVID, limit=1)
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['snapshots'][0]['views_change'], 60)
        self.assertAlmostEqual(result['snapshots'][0]['views_per_hour'], 120, places=3)
        self.assertEqual(result['snapshots'][0]['title'], 'Updated title')
        self.assertEqual(tracking.history(BVID)['snapshots'][1]['title'], 'Example')
        self.assertIsNone(first['views_per_hour'])
        overview = tracking.overview()
        self.assertEqual(overview['videos'][0]['title'], 'Updated title')
        self.assertEqual(overview['videos'][0]['views'], 160)
        self.assertNotIn('private_cookie', first)
        with tracking.connection() as db:
            self.assertEqual(db.execute('SELECT views FROM latest_snapshots').fetchone()[0], 160)

    def test_revision_changes_on_commits_including_external_edits_but_not_reads_or_rollbacks(self):
        initial = tracking.revision()
        self.assertEqual(tracking.revision(), initial)
        tracker = tracking.create_tracker(BVID, now=TIME)
        created = tracking.revision()
        self.assertNotEqual(created, initial)
        self.assertEqual(tracking.overview()['revision'], created['revision'])
        self.assertEqual(tracking.revision(), created)
        with self.assertRaises(RuntimeError), tracking.connection() as db:
            db.execute("UPDATE trackers SET status = 'paused', next_check_at = NULL WHERE id = ?", (tracker['id'],))
            raise RuntimeError('Rollback this change')
        self.assertEqual(tracking.revision(), created)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE trackers SET status = 'paused', next_check_at = NULL WHERE id = ?", (tracker['id'],))
            db.commit()
        paused = tracking.revision()
        self.assertNotEqual(paused, created)
        tracking.save_observation(info(), collected_at=TIME)
        observed = tracking.revision()
        self.assertNotEqual(observed, paused)
        tracking.record_error(BVID, 'Offline', attempted_at=TIME)
        self.assertNotEqual(tracking.revision(), observed)

    def test_v3_revision_migration_preserves_history_and_uses_only_metadata_for_polling(self):
        tracking.save_observation(info(), collected_at=TIME)
        before = tracking.history(BVID)
        with tracking.connection() as db:
            triggers = db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
            for trigger in triggers:
                db.execute(f'DROP TRIGGER {trigger[0]}')
            db.execute('DROP TABLE tracking_revision')
            db.execute('PRAGMA user_version = 3')
        tracking.initialize()
        self.assertEqual(tracking.history(BVID), before)
        queries = []
        with tracking.connection() as db:
            db.set_trace_callback(queries.append)
            revision = tracking._revision(db)
        self.assertEqual(revision, tracking.revision()['revision'])
        self.assertEqual(len(queries), 1)
        self.assertIn('FROM tracking_revision', queries[0])

    def test_zero_missing_and_decreasing_counts_are_preserved(self):
        tracking.save_snapshot(info(100), collected_at=TIME)
        response = info(0)
        response['stat'].update(like=None, coin=-1, favorite=True, reply=1.5, danmaku='7')
        row = tracking.save_snapshot(response, collected_at='2026-10-08T02:10:00+00:00')
        self.assertEqual(row['views'], 0)
        self.assertEqual(row['views_change'], -100)
        self.assertIsNone(row['like_ratio'])
        for field in ('likes', 'coins', 'favorites', 'replies'):
            self.assertIsNone(row[field])
        self.assertEqual(row['danmaku'], 7)
        with self.assertRaisesRegex(ValueError, 'No valid video metrics'):
            tracking.save_snapshot({'bvid': BVID, 'stat': {}})
        self.assertEqual(tracking.history(BVID)['total'], 2)

    def test_tracking_pause_resume_and_restart(self):
        row = tracking.create_tracker(BVID, 600, now=TIME)
        self.assertEqual(tracking.due_tracker(now=TIME)['id'], row['id'])
        with self.assertRaisesRegex(ValueError, 'already has a tracker'):
            tracking.create_tracker(BVID)
        tracking.save_snapshot(info(), tracker_id=row['id'], source='scheduled', collected_at=TIME)
        self.assertIsNone(tracking.due_tracker(now='2026-10-08T02:09:59Z'))
        self.assertEqual(tracking.due_tracker(now='2026-10-08T02:10:00Z')['id'], row['id'])
        tracking.update_tracker(row['id'], status='paused')
        self.assertIsNone(tracking.due_tracker(now='2030-01-01T00:00:00Z'))
        tracking.initialize()  # New connections/reinitialization do not reset persisted settings.
        self.assertEqual(tracking.get_tracker(row['id'])['status'], 'paused')
        tracking.update_tracker(row['id'], status='active', interval_seconds=3600, now=TIME)
        self.assertEqual(tracking.get_tracker(row['id'])['interval_seconds'], 3600)

    def test_failure_is_separate_and_backed_off(self):
        row = tracking.create_tracker(BVID, 60, now=TIME)
        tracking.record_error(BVID, 'Unavailable', tracker_id=row['id'], source='scheduled', attempted_at=TIME)
        result = tracking.history(BVID)
        self.assertEqual(result['total'], 0)
        self.assertEqual(result['errors'][0]['message'], 'Unavailable')
        self.assertIsNone(tracking.get_tracker(row['id'])['last_success_at'])
        self.assertIsNone(tracking.due_tracker(now='2026-10-08T02:04:59Z'))
        tracking.save_snapshot(info(), tracker_id=row['id'], collected_at='2026-10-08T02:05:00Z')
        self.assertIsNone(tracking.get_tracker(row['id'])['last_error'])
        self.assertEqual(len(tracking.history(BVID)['errors']), 1)

    def test_invalid_input_and_mismatch_roll_back(self):
        row = tracking.create_tracker(OTHER, now=TIME)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            tracking.save_snapshot(info(), tracker_id=row['id'], collected_at=TIME)
        self.assertEqual(tracking.overview()['counts']['videos'], 1)
        self.assertEqual(tracking.overview()['counts']['snapshots'], 0)
        for value in (True, 1.5, 0, 59, 604801, '600'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                tracking.create_tracker(BVID, value)
        with self.assertRaises(ValueError):
            tracking.save_snapshot(info(), collected_at='2026-10-08T02:00:00')

    def test_backup_contains_committed_wal_history(self):
        tracking.save_snapshot(info(), collected_at=TIME)
        with tracking.connection() as original:
            target = Path(tracking.backup()['path'])
            with closing(sqlite3.connect(target)) as backup:
                self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(backup.execute('SELECT views FROM snapshots').fetchone()[0], 100)
                self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0], tracking.SCHEMA_VERSION)
            self.assertEqual(original.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], 1)

    def test_scheduled_capture_and_busy_operations(self):
        row = tracking.create_tracker(BVID, now=TIME)
        worker = service.TrackingWorker()
        with patch.object(service, 'fetch_info', new_callable=AsyncMock, return_value=info()) as fetch:
            dataset.LOCK.acquire()
            try:
                worker.collect_due()
            finally:
                dataset.LOCK.release()
            fetch.assert_not_awaited()
            self.assertEqual(tracking.overview()['counts']['collection_errors'], 0)
            worker.collect_due()
            fetch.assert_awaited_once_with(BVID)
            worker.collect_due()
            fetch.assert_awaited_once()
        self.assertEqual(tracking.history(BVID)['snapshots'][0]['source'], 'scheduled')
        self.assertIsNotNone(tracking.get_tracker(row['id'])['last_success_at'])

    def test_capture_failure_does_not_leak_raw_error_or_save_zero(self):
        with patch.object(service, 'fetch_info', new_callable=AsyncMock, side_effect=RuntimeError('private cookie here')):
            with self.assertRaisesRegex(ValueError, 'RuntimeError'):
                asyncio.run(service.capture_snapshot(BVID))
        result = tracking.history(BVID)
        self.assertEqual(result['total'], 0)
        self.assertNotIn('private cookie', result['errors'][0]['message'])
        with patch.object(service, 'fetch_info', new_callable=AsyncMock, return_value={'bvid': BVID, 'stat': {}}):
            with self.assertRaisesRegex(ValueError, 'No valid video metrics'):
                asyncio.run(service.capture_snapshot(BVID))
        self.assertEqual(len(tracking.history(BVID)['errors']), 2)

    def test_pause_during_collection_is_preserved(self):
        row = tracking.create_tracker(BVID, now=TIME)
        async def pause(_):
            tracking.update_tracker(row['id'], status='paused')
            return info()
        with patch.object(service, 'fetch_info', side_effect=pause):
            service.TrackingWorker().collect_due()
        result = tracking.get_tracker(row['id'])
        self.assertEqual(result['status'], 'paused')
        self.assertIsNone(result['next_check_at'])
        self.assertIsNotNone(result['last_success_at'])
