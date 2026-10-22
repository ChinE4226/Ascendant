"""Cancellation and resource ownership under active background work."""

import asyncio
from contextlib import ExitStack
from threading import Event
import unittest
from unittest.mock import patch

import httpx

from ascendant import client, tracking
from ascendant.web import dataset
from ascendant.web import tracking as service
from tests.integration import test_tracking as fixtures


class TrackingShutdownTests(unittest.TestCase):
    def setUp(self):
        fixtures.TrackingTests.setUp(self)

    def test_stop_cancels_active_request_releases_locks_and_does_not_save_an_error(self):
        self.check_shutdown('video')

    def test_stop_cancels_active_creator_scan_without_saving_partial_releases(self):
        self.check_shutdown('creator')

    def check_shutdown(self, kind):
        entered, release = Event(), Event()
        real_client = httpx.AsyncClient

        async def respond(request):
            entered.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            return httpx.Response(200, json={'code': 0, 'data': fixtures.info()})

        if kind == 'creator':
            row = tracking.create_creator_watch('7', 60, now=fixtures.TIME)
        else:
            row = tracking.create_tracker(fixtures.BVID, 60, now=fixtures.TIME)
        with ExitStack() as stack:
            stack.enter_context(patch.object(service.accounts, 'credential_from_env', return_value=None))
            stack.enter_context(patch.object(client.httpx, 'AsyncClient', side_effect=lambda **kw: real_client(
                **kw, transport=httpx.MockTransport(respond))))
            worker = service.TrackingWorker()
            worker.start()
            try:
                self.assertTrue(entered.wait(2), 'Background request did not start')
                worker.stopped.set()
                worker.thread.join(timeout=2)
                still_running = worker.thread.is_alive()
            finally:
                release.set()
                worker.stop()
        self.assertFalse(still_running, 'Stopping tracking left its active request running')
        self.assertFalse(dataset.LOCK.locked())
        self.assertFalse(service.COLLECTION_LOCK.locked())
        self.assertEqual(client._sessions, {})
        if kind == 'creator':
            watch = tracking.get_creator_watch(row['id'])
            self.assertIsNone(watch['baseline_at'])
            self.assertIsNone(watch['last_error'])
            self.assertEqual(tracking.overview()['releases'], [])
        else:
            self.assertEqual(tracking.history(fixtures.BVID)['total'], 0)
            self.assertEqual(tracking.history(fixtures.BVID)['errors'], [])
            self.assertIsNone(tracking.get_tracker(row['id'])['last_error'])
