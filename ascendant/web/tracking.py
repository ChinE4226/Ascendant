"""Independent video monitoring and creator release discovery, owned by the server."""

from __future__ import annotations

import asyncio
from datetime import datetime
from threading import Event, Lock, Thread

from ascendant.api import video, user

from ascendant import accounts, client, tracking, videos
from ascendant.web import dataset
from ascendant.web.serializers import extract_bvid
from ascendant.errors import request_error_message

COLLECTION_LOCK = Lock()


class CollectionBusy(ValueError):
    pass


class ReleaseScanError(ValueError):
    """Safe, locally generated discovery errors suitable for the watch status."""


def error_message(exc):
    """Persist useful reasons without retaining raw responses or credentials."""
    return request_error_message(exc)


async def fetch_info(bvid):
    client.configure_bilibili_client()
    try:
        info = await video.Video(bvid=bvid, credential=accounts.credential_from_env()).get_info()
        if not isinstance(info, dict) or info.get('bvid') != bvid:
            raise ValueError('Bilibili returned no matching video information.')
        return info
    finally:
        await client.close_bilibili_client()


async def capture_observation(value, *, tracker_id=None, source='manual'):
    bvid = extract_bvid(value)
    if not COLLECTION_LOCK.acquire(blocking=False):
        raise CollectionBusy('Another tracking check is running. Try again when it finishes.')
    try:
        if not dataset.LOCK.acquire(blocking=False):
            raise CollectionBusy('Another video operation is running. Try again when it finishes.')
        try:
            if tracker_id is not None:
                row = tracking.get_tracker(tracker_id)
                if row['bvid'] != bvid:
                    raise ValueError('Tracker does not match this video.')
                if source == 'scheduled' and (row['status'] != 'active' or row['next_check_at'] > tracking.utc_time()):
                    return None
            try:
                info = await fetch_info(bvid)
            except Exception as exc:
                message = error_message(exc)
                tracking.record_error(bvid, message, tracker_id=tracker_id, source=source)
                raise ValueError(message) from None
            # Local disk errors must not be misreported as Bilibili failures.
            if not isinstance(info.get('stat'), dict) or not any(tracking._count(info['stat'].get(key)) is not None for key in tracking.METRICS.values()):
                message = 'No valid video metrics were returned; no observation was saved.'
                tracking.record_error(bvid, message, tracker_id=tracker_id, source=source)
                raise ValueError(message)
            return tracking.save_observation(info, tracker_id=tracker_id, source=source)
        finally:
            dataset.LOCK.release()
    finally:
        COLLECTION_LOCK.release()


capture_snapshot = capture_observation  # Original route compatibility.


async def fetch_releases(watch):
    """Bounded, newest-first discovery; first page establishes the initial baseline."""
    client.configure_bilibili_client()
    try:
        uploader = user.User(int(watch['uid']), credential=accounts.credential_from_env())
        found, seen = [], set()
        for page in range(1, 11):
            response = await videos.fetch_creator_video_page(uploader, pn=page, ps=50, order=user.VideoOrder.PUBDATE)
            await asyncio.sleep(client.request_delay_seconds())
            rows = response.get('list', {}).get('vlist') if isinstance(response, dict) and isinstance(response.get('list'), dict) else None
            if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
                raise ReleaseScanError('Bilibili returned an invalid creator release list. No scan was saved.')
            for item in rows:
                bvid = tracking.valid_bvid(item.get('bvid'))
                if tracking._count(item.get('created', item.get('pubdate'))) is None:
                    raise ReleaseScanError('A creator release has no publication time. No scan was saved.')
                if bvid not in seen:
                    seen.add(bvid)
                    found.append(item)
            # Examine the full page before stopping, including a pinned older item.
            if watch['baseline_at'] is None or not rows or len(rows) < 50 or tracking.creator_has_seen(watch['id'], rows[-1]['bvid']):
                return found
            if tracking._count(rows[-1].get('created', rows[-1].get('pubdate'))) <= datetime.fromisoformat(watch['baseline_at']).timestamp():
                return found
        raise ReleaseScanError('Release discovery exceeded 500 uploads before reaching its baseline. No scan was saved; this backlog exceeds the current discovery limit.')
    finally:
        await client.close_bilibili_client()


async def check_creator(watch_id, *, scheduled=False):
    if not COLLECTION_LOCK.acquire(blocking=False):
        raise CollectionBusy('Another tracking check is running.')
    try:
        if not dataset.LOCK.acquire(blocking=False):
            raise CollectionBusy('Another video operation is running. Try again when it finishes.')
        try:
            watch = tracking.get_creator_watch(watch_id)
            if scheduled and (watch['status'] != 'active' or watch['next_check_at'] > tracking.utc_time()):
                return None
            try:
                items = await fetch_releases(watch)
            except Exception as exc:
                reason = str(exc) if isinstance(exc, ReleaseScanError) else request_error_message(exc)
                message = f'Creator release check failed. {reason} No releases were recorded.'
                tracking.record_creator_error(watch_id, message)
                raise ValueError(message) from None
            return tracking.record_creator_scan(watch_id, items)
        finally:
            dataset.LOCK.release()
    finally:
        COLLECTION_LOCK.release()


class TrackingWorker:
    def __init__(self):
        self.stopped = Event()
        self.thread = Thread(target=self.run, name='video-tracking', daemon=True)

    def start(self):
        tracking.initialize()
        self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.thread.is_alive():
            self.thread.join(timeout=25)

    async def collect_until_stopped(self, operation):
        """Cancel active I/O on shutdown so service finally blocks release locks."""
        task = asyncio.create_task(operation)
        try:
            while not task.done():
                if self.stopped.is_set():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    return
                await asyncio.wait((task,), timeout=.1)
            return await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def collect_due(self):
        if self.stopped.is_set():
            return
        row = tracking.due_tracker()
        watch = tracking.due_creator_watch()
        if row is None and watch is None:
            return
        try:
            if watch is not None and (row is None or watch['next_check_at'] <= row['next_check_at']):
                asyncio.run(self.collect_until_stopped(check_creator(watch['id'], scheduled=True)))
            else:
                asyncio.run(self.collect_until_stopped(capture_observation(row['bvid'], tracker_id=row['id'], source='scheduled')))
        except CollectionBusy:
            pass  # Keep the original due time; this is not a failed observation.
        except ValueError:
            pass  # Collection services recorded the failed request and retry time.

    def run(self):
        while not self.stopped.is_set():
            try:
                self.collect_due()
                delay = max(1, client.request_delay_seconds())
            except Exception as exc:
                print(f'Tracking paused for 30 seconds after a local error ({type(exc).__name__}).')
                delay = 30
            self.stopped.wait(delay)
