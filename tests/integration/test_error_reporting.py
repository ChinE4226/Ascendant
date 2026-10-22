"""Simulated upstream failures; no real requests or production history."""

import asyncio
from datetime import datetime, timedelta
import unittest
import httpx
from unittest.mock import AsyncMock, Mock, patch

from ascendant.errors import NetworkException, ResponseCodeException

from ascendant import tracking, videos
from ascendant.errors import BilibiliRequestError
from ascendant.web import dataset, state
from ascendant.web import tracking as service
from tests.integration import test_tracking as fixtures


class ErrorReportingTests(unittest.TestCase):
    def setUp(self):
        fixtures.TrackingTests.setUp(self)

    def test_creator_412_reaches_durable_status_with_page_context_and_no_partial_scan(self):
        watch = tracking.create_creator_watch('7', 60, now=fixtures.TIME)
        failure = NetworkException(412, '<html>private-cookie</html>').with_request_context('creator_page', 'guest')
        uploader = Mock(get_videos=AsyncMock(side_effect=failure))
        with patch.object(service.client, 'configure_bilibili_client'), patch.object(service.client, 'close_bilibili_client', new_callable=AsyncMock) as close, patch.object(service.accounts, 'credential_from_env', return_value=None), patch.object(service.user, 'User', return_value=uploader):
            with self.assertRaisesRegex(ValueError, 'HTTP 412') as error:
                asyncio.run(service.check_creator(watch['id']))
        message = tracking.get_creator_watch(watch['id'])['last_error']
        self.assertIn('page 1', message)
        self.assertIn('Request: Creator page access ID', message)
        self.assertIn('Credentials: guest', message)
        self.assertIn('lower collection requests per second', message)
        self.assertIn('No releases were recorded', message)
        self.assertNotIn('private-cookie', message)
        self.assertEqual(str(error.exception), message)
        self.assertIsNone(tracking.get_creator_watch(watch['id'])['baseline_at'])
        self.assertEqual(tracking.overview()['releases'], [])
        uploader.get_videos.assert_awaited_once()
        close.assert_awaited_once()
        self.assertFalse(dataset.LOCK.locked())

    def test_scheduled_video_failure_keeps_history_and_waits_at_least_five_minutes(self):
        tracker = tracking.create_tracker(fixtures.BVID, 60, now=fixtures.TIME)
        tracking.save_observation(fixtures.info(), tracker_id=tracker['id'], collected_at=fixtures.TIME)
        attempted = tracking.utc_time()
        with patch.object(service, 'fetch_info', new_callable=AsyncMock, side_effect=ResponseCodeException(-412, 'private-cookie', {'token': 'secret'})) as fetch:
            with self.assertRaisesRegex(ValueError, 'API code -412'):
                asyncio.run(service.capture_observation(fixtures.BVID, tracker_id=tracker['id']))
            service.TrackingWorker().collect_due()
        fetch.assert_awaited_once()
        row = tracking.get_tracker(tracker['id'])
        self.assertGreaterEqual(datetime.fromisoformat(row['next_check_at']), datetime.fromisoformat(attempted) + timedelta(seconds=300))
        self.assertIn('API code -412', row['last_error'])
        self.assertEqual(tracking.history(fixtures.BVID)['total'], 1)
        self.assertEqual(len(tracking.history(fixtures.BVID)['errors']), 1)
        self.assertNotIn('secret', row['last_error'])

    def test_detail_error_contains_no_raw_upstream_response_and_collection_stops(self):
        failure = NetworkException(412, '<html>private-cookie</html>')
        with patch.object(videos.video, 'Video', return_value=Mock(get_info=AsyncMock(side_effect=failure))) as video:
            item = asyncio.run(videos.fetch_video_detail({'bvid': fixtures.BVID}, None))
        self.assertEqual(item['detail_error_status'], 412)
        self.assertIn('HTTP 412', item['detail_error'])
        self.assertNotIn('private-cookie', item['detail_error'])
        from ascendant.errors import check_detail_rejection
        with self.assertRaisesRegex(BilibiliRequestError, 'HTTP 412'):
            check_detail_rejection(item)
        video.return_value.get_info.assert_awaited_once()

    def test_single_lookup_412_keeps_code_in_user_error_and_progress(self):
        from ascendant.web import videos as web_videos
        with patch.object(web_videos.client, 'configure_bilibili_client'), patch.object(web_videos.client, 'close_bilibili_client', new_callable=AsyncMock) as close, patch.object(web_videos.account_service, 'credential_from_env', return_value=None), patch.object(web_videos.video, 'Video', return_value=Mock(get_info=AsyncMock(side_effect=NetworkException(412, 'private-cookie')))), patch.object(state, 'PROGRESS', {}):
            with self.assertRaisesRegex(BilibiliRequestError, 'HTTP 412') as raised:
                asyncio.run(web_videos.fetch_single_video(fixtures.BVID))
            self.assertEqual(raised.exception.status, 412)
            self.assertIn('HTTP 412', state.PROGRESS['message'])
            self.assertFalse(state.PROGRESS['running'])
            self.assertNotIn('private-cookie', state.PROGRESS['message'])
            close.assert_awaited_once()

    def test_transport_failures_stop_instead_of_being_counted_as_invalid_video_metrics(self):
        from ascendant.errors import check_detail_rejection
        for failure, phrase in ((httpx.ReadTimeout('private-cookie'), 'timed out'),
                                (httpx.ConnectError('private-cookie'), 'network request failed'),
                                (NetworkException(503, 'private-cookie'), 'HTTP 503')):
            with self.subTest(failure=type(failure).__name__), patch.object(videos.video, 'Video', return_value=Mock(get_info=AsyncMock(side_effect=failure))):
                item = asyncio.run(videos.fetch_video_detail({'bvid': fixtures.BVID}, None))
                with self.assertRaisesRegex(BilibiliRequestError, phrase) as raised:
                    check_detail_rejection(item)
                self.assertNotIn('private-cookie', str(raised.exception))
        with patch.object(videos.video, 'Video', return_value=Mock(get_info=AsyncMock(side_effect=ResponseCodeException(-404, 'Unavailable', {})))):
            unavailable = asyncio.run(videos.fetch_video_detail({'bvid': fixtures.BVID}, None))
        check_detail_rejection(unavailable)  # One removed video can still be skipped.
