"""Durable local video observations. No network calls or credential storage."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sqlite3
import json
from threading import RLock

from ascendant import config

SCHEMA_VERSION = 4
METRICS = {'views': 'view', 'likes': 'like', 'coins': 'coin', 'favorites': 'favorite',
           'replies': 'reply', 'shares': 'share', 'danmaku': 'danmaku'}
SCHEMA_FILE = Path(__file__).with_name('tracking_schema.sql')
COLLECTIONS_SCHEMA_FILE = Path(__file__).with_name('tracking_collections.sql')
WORKFLOW_SCHEMA_FILE = Path(__file__).with_name('tracking_workflow.sql')
REVISION_SCHEMA_FILE = Path(__file__).with_name('tracking_revision.sql')
SCHEMA_LOCK = RLock()


def utc_time(value=None):
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('Collection times must include a timezone.')
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


def later(value, seconds):
    return utc_time(datetime.fromisoformat(value) + timedelta(seconds=seconds))


def valid_bvid(value):
    if not isinstance(value, str) or not re.fullmatch(r'BV[0-9A-Za-z]{10}', value):
        raise ValueError('A 12-character BV ID is required.')
    return value


def positive_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError('A positive record ID is required.')
    return value


def interval(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 60 <= value <= 604800:
        raise ValueError('Collection interval must be 60–604800 whole seconds (1 minute to 7 days).')
    return value


@contextmanager
def connection(path=None):
    """Short-lived connections, transactional writes, and versioned initialization."""
    path = Path(path or config.TRACKING_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys = ON')
        with SCHEMA_LOCK:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2, 3, SCHEMA_VERSION):
                raise ValueError(f'Unsupported tracking database version: {version}.')
            if version < SCHEMA_VERSION:
                db.execute('PRAGMA journal_mode = WAL')
                # Recheck under a database write lock, including across processes.
                db.execute('BEGIN IMMEDIATE')
                version = db.execute('PRAGMA user_version').fetchone()[0]
                if version not in (0, 1, 2, 3, SCHEMA_VERSION):
                    raise ValueError(f'Unsupported tracking database version: {version}.')
                migrations = [(1, SCHEMA_FILE), (2, COLLECTIONS_SCHEMA_FILE), (3, WORKFLOW_SCHEMA_FILE),
                              (4, REVISION_SCHEMA_FILE)]
                for next_version, schema_file in migrations:
                    if version >= next_version:
                        continue
                    statement = ''
                    for line in schema_file.read_text(encoding='utf-8').splitlines(keepends=True):
                        statement += line
                        if sqlite3.complete_statement(statement):
                            db.execute(statement)
                            statement = ''
                    db.execute(f'PRAGMA user_version = {next_version}')
                    version = next_version
                db.commit()
        with db:
            yield db
    finally:
        db.close()


def initialize(path=None):
    with connection(path) as db:
        return {'path': str(Path(path or config.TRACKING_DB).resolve()),
                'schema_version': db.execute('PRAGMA user_version').fetchone()[0]}


def _ensure_video(db, bvid, timestamp):
    db.execute('INSERT INTO videos(bvid, first_seen_at, updated_at) VALUES (?, ?, ?) ON CONFLICT(bvid) DO NOTHING',
               (bvid, timestamp, timestamp))


def create_tracker(bvid, interval_seconds=600, *, now=None):
    bvid, interval_seconds, timestamp = valid_bvid(bvid), interval(interval_seconds), utc_time(now)
    with connection() as db:
        _ensure_video(db, bvid, timestamp)
        if db.execute('SELECT id FROM trackers WHERE bvid = ?', (bvid,)).fetchone():
            raise ValueError('This video already has a tracker. Use its pause/resume controls.')
        result = db.execute('INSERT INTO trackers(bvid, interval_seconds, created_at, next_check_at) VALUES (?, ?, ?, ?)',
                            (bvid, interval_seconds, timestamp, timestamp))
        return dict(db.execute('SELECT * FROM trackers WHERE id = ?', (result.lastrowid,)).fetchone())


def get_tracker(tracker_id):
    with connection() as db:
        row = db.execute('SELECT * FROM trackers WHERE id = ?', (positive_id(tracker_id),)).fetchone()
        if row is None:
            raise ValueError('Tracker was not found.')
        return dict(row)


def update_tracker(tracker_id, *, status, interval_seconds=None, now=None):
    if status not in ('active', 'paused'):
        raise ValueError('Tracker status must be active or paused.')
    tracker_id, timestamp = positive_id(tracker_id), utc_time(now)
    with connection() as db:
        row = db.execute('SELECT * FROM trackers WHERE id = ?', (tracker_id,)).fetchone()
        if row is None:
            raise ValueError('Tracker was not found.')
        seconds = row['interval_seconds'] if interval_seconds is None else interval(interval_seconds)
        db.execute('UPDATE trackers SET status = ?, interval_seconds = ?, next_check_at = ? WHERE id = ?',
                   (status, seconds, timestamp if status == 'active' else None, tracker_id))
        return dict(db.execute('SELECT * FROM trackers WHERE id = ?', (tracker_id,)).fetchone())


def due_tracker(*, now=None):
    with connection() as db:
        row = db.execute("SELECT * FROM trackers WHERE status = 'active' AND next_check_at <= ? ORDER BY next_check_at, id LIMIT 1",
                         (utc_time(now),)).fetchone()
        return dict(row) if row else None


def _count(value):
    # Preserve zero and unavailable metrics; never truncate fractions or booleans.
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    return value if isinstance(value, int) and 0 <= value <= 9223372036854775807 else None


def _finish_check(db, tracker_id, bvid, timestamp, error=None):
    if tracker_id is None:
        return
    row = db.execute('SELECT * FROM trackers WHERE id = ?', (positive_id(tracker_id),)).fetchone()
    if row is None or row['bvid'] != bvid:
        raise ValueError('Tracker does not match this video.')
    seconds = max(300, row['interval_seconds']) if error else row['interval_seconds']
    db.execute('UPDATE trackers SET last_checked_at = ?, last_success_at = ?, last_error = ?, next_check_at = ? WHERE id = ?',
               (timestamp, row['last_success_at'] if error else timestamp, error,
                later(timestamp, seconds) if row['status'] == 'active' else None, tracker_id))


def _insert_snapshot(db, info, *, source='manual', tracker_id=None, collected_at=None, reuse=False):
    if not isinstance(info, dict):
        raise ValueError('Video information is required.')
    bvid, timestamp = valid_bvid(info.get('bvid')), utc_time(collected_at)
    if source not in ('manual', 'scheduled'):
        raise ValueError('Invalid snapshot source.')
    stats = info.get('stat') if isinstance(info.get('stat'), dict) else {}
    counts = [_count(stats.get(key)) for key in METRICS.values()]
    if all(value is None for value in counts):
        raise ValueError('No valid video metrics were returned; no snapshot was saved.')
    owner = info.get('owner') if isinstance(info.get('owner'), dict) else {}
    title = str(info.get('title') or '') or None
    creator_uid = str(owner.get('mid')) if owner.get('mid') is not None else None
    creator_name = str(owner.get('name') or '') or None
    published_at = _count(info.get('pubdate'))
    if reuse:
        previous = db.execute('SELECT * FROM snapshots WHERE bvid = ? AND collected_at = ?', (bvid, timestamp)).fetchone()
        if previous is not None:
            expected = dict(zip(['title', 'creator_uid', 'creator_name', 'published_at', *METRICS],
                                [title, creator_uid, creator_name, published_at, *counts]))
            if any(previous[key] != value for key, value in expected.items()):
                raise ValueError('An observation at this collection time already exists with different data.')
            return previous['id']
    _ensure_video(db, bvid, timestamp)
    db.execute('''UPDATE videos SET aid = ?, title = ?, creator_uid = ?, creator_name = ?, published_at = ?, updated_at = ?
                  WHERE bvid = ? AND (updated_at <= ? OR title IS NULL)''',
               (_count(info.get('aid')), title, creator_uid, creator_name, published_at, timestamp, bvid, timestamp))
    fields = 'bvid, tracker_id, collected_at, source, title, creator_uid, creator_name, published_at, ' + ', '.join(METRICS)
    values = [bvid, tracker_id, timestamp, source, title, creator_uid, creator_name, published_at, *counts]
    row = db.execute(f'INSERT INTO snapshots ({fields}) VALUES ({", ".join("?" for _ in values)})', values)
    _finish_check(db, tracker_id, bvid, timestamp)
    return row.lastrowid


def save_observation(info, *, source='manual', tracker_id=None, collected_at=None):
    """A tracking check explicitly enrolls an observation in the time series."""
    with connection() as db:
        identity = _insert_snapshot(db, info, source=source, tracker_id=tracker_id, collected_at=collected_at)
        db.execute('INSERT INTO tracking_samples(snapshot_id) VALUES (?)', (identity,))
        return dict(db.execute('SELECT * FROM tracking_history WHERE id = ?', (identity,)).fetchone())


# Compatibility with the original single-video endpoint; collection saves use save_collection.
save_snapshot = save_observation


def save_collection(items, metadata, *, mode):
    """Save exactly one collection atomically, using its original observation time."""
    if mode not in ('loaded', 'fresh') or metadata.get('source_kind') not in ('creator', 'weekly', 'random'):
        raise ValueError('Choose a valid collection and snapshot mode.')
    if not items:
        raise ValueError('This collection has no videos to save.')
    timestamp = utc_time(metadata['collected_at'])
    started = utc_time(metadata.get('started_at') or timestamp)
    label = str(metadata.get('source_label') or metadata.get('selection') or 'Video collection')
    scope = metadata.get('scope') or {'uid': metadata.get('uid'), 'selection': metadata.get('selection')}
    with connection() as db:
        row = db.execute('''INSERT INTO snapshot_batches
            (label, source_kind, capture_mode, collection_id, started_at, collected_at, saved_at, video_count, scope_json, collection_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (label, metadata['source_kind'], mode, metadata.get('collection_id'), started, timestamp, utc_time(), len(items),
             json.dumps(scope, ensure_ascii=False), json.dumps(metadata.get('collection') or {}, ensure_ascii=False)))
        batch_id = row.lastrowid
        for position, item in enumerate(items):
            snapshot_id = _insert_snapshot(db, item, collected_at=timestamp, reuse=True)
            db.execute('INSERT INTO snapshot_batch_items(batch_id, snapshot_id, position) VALUES (?, ?, ?)',
                       (batch_id, snapshot_id, position))
        return dict(db.execute('SELECT * FROM snapshot_batches WHERE id = ?', (batch_id,)).fetchone())


def collection_history(batch_id, *, limit=500):
    batch_id = positive_id(batch_id)
    with connection() as db:
        batch = db.execute('SELECT * FROM snapshot_batches WHERE id = ?', (batch_id,)).fetchone()
        if batch is None:
            raise ValueError('Collection snapshot was not found.')
        rows = [dict(row) for row in db.execute('''SELECT s.* FROM snapshots s JOIN snapshot_batch_items i ON i.snapshot_id = s.id
            WHERE i.batch_id = ? ORDER BY i.position LIMIT ?''', (batch_id, limit))]
        return {'batch': dict(batch), 'videos': rows}


def record_error(bvid, message, *, source='manual', tracker_id=None, attempted_at=None):
    bvid, timestamp = valid_bvid(bvid), utc_time(attempted_at)
    if source not in ('manual', 'scheduled'):
        raise ValueError('Invalid snapshot source.')
    with connection() as db:
        _ensure_video(db, bvid, timestamp)
        db.execute('INSERT INTO collection_errors(bvid, tracker_id, attempted_at, source, message) VALUES (?, ?, ?, ?, ?)',
                   (bvid, tracker_id, timestamp, source, message))
        _finish_check(db, tracker_id, bvid, timestamp, message)


def _revision(db):
    row = db.execute('SELECT identity, revision FROM tracking_revision WHERE id = 1').fetchone()
    return f"{row['identity']}:{row['revision']}"


def revision():
    """Read one metadata row; no history scans or retained database connection."""
    with connection() as db:
        return {'revision': _revision(db)}


def overview():
    with connection() as db:
        # Read before the overview so a concurrent commit is noticed next poll.
        revision = _revision(db)
        counts = {name: db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                  for name in ('videos', 'trackers', 'snapshots', 'collection_errors', 'snapshot_batches')}
        counts['snapshots'] = counts['observations'] = db.execute('SELECT COUNT(*) FROM tracking_observations').fetchone()[0]
        counts['videos'] = db.execute('''SELECT COUNT(*) FROM videos WHERE bvid IN
            (SELECT bvid FROM trackers UNION SELECT bvid FROM tracking_observations UNION SELECT bvid FROM collection_errors)''').fetchone()[0]
        counts['creator_watches'] = db.execute('SELECT COUNT(*) FROM creator_watches').fetchone()[0]
        trackers = [dict(row) for row in db.execute('''SELECT trackers.*, t.title, t.views, t.collected_at
            FROM trackers LEFT JOIN latest_tracking_observations t USING(bvid)
            ORDER BY trackers.id DESC''')]
        videos = [dict(row) for row in db.execute('''SELECT t.* FROM latest_tracking_observations t
            ORDER BY collected_at DESC LIMIT 200''')]
        return {'path': str(config.TRACKING_DB.resolve()), 'schema_version': SCHEMA_VERSION, 'revision': revision,
                'counts': counts, 'trackers': trackers, 'videos': videos,
                'creator_watches': [dict(row) for row in db.execute('SELECT * FROM creator_watches ORDER BY id DESC')],
                'releases': [dict(row) for row in db.execute('''SELECT s.*, w.label, w.uid FROM creator_seen s
                    JOIN creator_watches w ON w.id = s.watch_id WHERE is_baseline = 0 ORDER BY discovered_at DESC LIMIT 100''')]}


def snapshot_overview():
    with connection() as db:
        return {'path': str(config.TRACKING_DB.resolve()), 'schema_version': SCHEMA_VERSION,
                'batches': [dict(row) for row in db.execute('SELECT * FROM snapshot_batches ORDER BY saved_at DESC, id DESC LIMIT 100')]}


def history(bvid, *, limit=500):
    bvid = valid_bvid(bvid)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
        raise ValueError('History limit must be 1–5000.')
    with connection() as db:
        rows = [dict(row) for row in db.execute('SELECT * FROM tracking_history WHERE bvid = ? ORDER BY collected_at DESC, id DESC LIMIT ?',
                                               (bvid, limit))]
        errors = [dict(row) for row in db.execute('SELECT * FROM collection_errors WHERE bvid = ? ORDER BY attempted_at DESC, id DESC LIMIT 50', (bvid,))]
        total = db.execute('SELECT COUNT(*) FROM tracking_observations WHERE bvid = ?', (bvid,)).fetchone()[0]
        return {'bvid': bvid, 'snapshots': rows, 'errors': errors, 'total': total}


def analyse_history(bvid, metric='views', *, limit=500):
    from ascendant.tracking_analysis import analyse_observations
    bvid = valid_bvid(bvid)
    if metric not in METRICS:
        raise ValueError('Choose a video metric for tracking analysis.')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
        raise ValueError('History limit must be 1–5000.')
    with connection() as db:
        rows = (dict(row) for row in db.execute('SELECT * FROM tracking_observations WHERE bvid = ? ORDER BY collected_at, id', (bvid,)))
        result = analyse_observations(rows, metric, point_limit=limit)
    return {'bvid': bvid, **result}


def valid_uid(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or not 0 < int(value) <= 9223372036854775807:
        raise ValueError('A positive numeric creator UID is required.')
    return str(int(value))


def create_creator_watch(uid, interval_seconds=600, video_interval_seconds=600, *, label=None, now=None):
    uid, seconds, video_seconds = valid_uid(uid), interval(interval_seconds), interval(video_interval_seconds)
    timestamp = utc_time(now)
    with connection() as db:
        if db.execute('SELECT id FROM creator_watches WHERE uid = ?', (uid,)).fetchone():
            raise ValueError('This creator already has a release watch. Use pause/resume.')
        row = db.execute('''INSERT INTO creator_watches(uid, label, interval_seconds, video_interval_seconds, created_at, next_check_at)
            VALUES (?, ?, ?, ?, ?, ?)''', (uid, str(label or f'Creator {uid}')[:200], seconds, video_seconds, timestamp, timestamp))
        return dict(db.execute('SELECT * FROM creator_watches WHERE id = ?', (row.lastrowid,)).fetchone())


def get_creator_watch(watch_id):
    with connection() as db:
        row = db.execute('SELECT * FROM creator_watches WHERE id = ?', (positive_id(watch_id),)).fetchone()
        if row is None:
            raise ValueError('Creator watch was not found.')
        return dict(row)


def update_creator_watch(watch_id, *, status, interval_seconds=None, video_interval_seconds=None, now=None):
    if status not in ('active', 'paused'):
        raise ValueError('Watch status must be active or paused.')
    with connection() as db:
        row = db.execute('SELECT * FROM creator_watches WHERE id = ?', (positive_id(watch_id),)).fetchone()
        if row is None:
            raise ValueError('Creator watch was not found.')
        seconds = row['interval_seconds'] if interval_seconds is None else interval(interval_seconds)
        video_seconds = row['video_interval_seconds'] if video_interval_seconds is None else interval(video_interval_seconds)
        db.execute('UPDATE creator_watches SET status = ?, interval_seconds = ?, video_interval_seconds = ?, next_check_at = ? WHERE id = ?',
                   (status, seconds, video_seconds, utc_time(now) if status == 'active' else None, watch_id))
        return dict(db.execute('SELECT * FROM creator_watches WHERE id = ?', (watch_id,)).fetchone())


def due_creator_watch(*, now=None):
    with connection() as db:
        row = db.execute("SELECT * FROM creator_watches WHERE status = 'active' AND next_check_at <= ? ORDER BY next_check_at, id LIMIT 1", (utc_time(now),)).fetchone()
        return dict(row) if row else None


def creator_seen_ids(watch_id):
    with connection() as db:
        return {row[0] for row in db.execute('SELECT bvid FROM creator_seen WHERE watch_id = ?', (positive_id(watch_id),))}


def creator_has_seen(watch_id, bvid):
    """Check the indexed scan boundary without loading the creator's full history."""
    with connection() as db:
        return db.execute('SELECT 1 FROM creator_seen WHERE watch_id = ? AND bvid = ?',
                          (positive_id(watch_id), valid_bvid(bvid))).fetchone() is not None


def _finish_creator_check(db, row, timestamp, error=None):
    seconds = max(300, row['interval_seconds']) if error else row['interval_seconds']
    db.execute('UPDATE creator_watches SET last_checked_at = ?, last_success_at = ?, last_error = ?, next_check_at = ? WHERE id = ?',
        (timestamp, row['last_success_at'] if error else timestamp, error,
         later(timestamp, seconds) if row['status'] == 'active' else None, row['id']))


def record_creator_scan(watch_id, items, *, checked_at=None):
    """First scan is a baseline; later releases enroll video trackers atomically."""
    timestamp = utc_time(checked_at)
    with connection() as db:
        row = db.execute('SELECT * FROM creator_watches WHERE id = ?', (positive_id(watch_id),)).fetchone()
        if row is None:
            raise ValueError('Creator watch was not found.')
        released = []
        for item in items:
            bvid = valid_bvid(item.get('bvid'))
            published = _count(item.get('created', item.get('pubdate')))
            if published is None:
                raise ValueError('A release has no valid publication time; the scan was not saved.')
            if db.execute('SELECT 1 FROM creator_seen WHERE watch_id = ? AND bvid = ?', (watch_id, bvid)).fetchone():
                continue
            baseline = row['baseline_at'] is None or published <= datetime.fromisoformat(row['baseline_at']).timestamp()
            tracker_id = None
            if not baseline:
                _ensure_video(db, bvid, timestamp)
                tracker = db.execute('SELECT id FROM trackers WHERE bvid = ?', (bvid,)).fetchone()
                if tracker is None:
                    tracker_id = db.execute('''INSERT INTO trackers(bvid, interval_seconds, created_at, next_check_at)
                        VALUES (?, ?, ?, ?)''', (bvid, row['video_interval_seconds'], timestamp, timestamp)).lastrowid
                else:
                    tracker_id = tracker['id']  # Preserve an existing paused tracker and its interval.
                released.append({'bvid': bvid, 'tracker_id': tracker_id})
            db.execute('''INSERT INTO creator_seen(watch_id, bvid, title, published_at, discovered_at, is_baseline, tracker_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)''', (watch_id, bvid, str(item.get('title') or ''), published, timestamp, int(baseline), tracker_id))
        if row['baseline_at'] is None:
            db.execute('UPDATE creator_watches SET baseline_at = ? WHERE id = ?', (timestamp, watch_id))
        _finish_creator_check(db, row, timestamp)
        return {'watch_id': watch_id, 'baseline': row['baseline_at'] is None, 'releases': released, 'checked_at': timestamp}


def record_creator_error(watch_id, message, *, attempted_at=None):
    timestamp = utc_time(attempted_at)
    with connection() as db:
        row = db.execute('SELECT * FROM creator_watches WHERE id = ?', (positive_id(watch_id),)).fetchone()
        if row is None:
            raise ValueError('Creator watch was not found.')
        db.execute('INSERT INTO creator_watch_errors(watch_id, attempted_at, message) VALUES (?, ?, ?)', (watch_id, timestamp, message))
        _finish_creator_check(db, row, timestamp, message)


def backup():
    """SQLite's backup API includes committed WAL changes, even during collection."""
    target = config.TRACKING_DB.parent / 'backups' / f'tracking-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")}.sqlite3'
    target.parent.mkdir(parents=True, exist_ok=True)
    with connection() as db:
        destination = sqlite3.connect(target)
        try:
            db.backup(destination)
        finally:
            destination.close()
    return {'path': str(target.resolve())}


if __name__ == '__main__':
    import json
    print(json.dumps(initialize(), indent=2))
