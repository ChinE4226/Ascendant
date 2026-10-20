"""Node fetching uses existing detail validation and bounded creator scans."""

from contextlib import ExitStack
from threading import Event
import unittest
from unittest.mock import AsyncMock, Mock, patch

from bilibili_ds.distributed import fetch
from tests.unit.test_nodes import bvid, item


class NodeFetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(fetch.accounts, 'credential_from_environment', return_value=None))
        self.stack.enter_context(patch.object(fetch.client, 'configure_bilibili_client'))
        self.close = self.stack.enter_context(patch.object(fetch.client, 'close_bilibili_client', new_callable=AsyncMock))
        self.stack.enter_context(patch.object(fetch.asyncio, 'sleep', new_callable=AsyncMock))
        self.original_detail = fetch.videos.fetch_video_detail
        self.detail = self.stack.enter_context(patch.object(fetch.videos, 'fetch_video_detail', new_callable=AsyncMock))
        self.progress = Mock()

    async def test_video_batches_keep_invalid_records_and_zero_metrics(self):
        self.detail.side_effect = [item(0), item(1, valid=False)]
        result = await fetch.fetch_unit({'kind': 'videos', 'payload': {'bvids': [bvid(0), bvid(1)]}}, '', 1, Event(), self.progress)
        self.assertEqual(len(result['items']), 2)
        self.assertEqual(result['items'][0]['stat']['view'], 0)
        self.assertIsNone(result['items'][1]['stat']['view'])
        self.close.assert_awaited_once()

    async def test_dataset_pacing_applies_to_nodes_without_exceeding_local_cap(self):
        from bilibili_ds.fetch_context import REQUEST_DELAY
        for requested, participants, cap, expected_delay in ((.5, 1, 1, 2), (4, 2, 1, 1), (.1, 2, 1, 20)):
            self.detail.reset_mock()
            async def detail(*args):
                self.assertEqual(REQUEST_DELAY.get(), expected_delay)
                return item(0)
            self.detail.side_effect = detail
            unit = {'kind': 'videos', 'payload': {'bvids': [bvid(0)], 'request_rate': requested, 'participants': participants}}
            await fetch.fetch_unit(unit, '', cap, Event(), self.progress)
            fetch.asyncio.sleep.assert_awaited_with(expected_delay)
            self.assertIsNone(REQUEST_DELAY.get())

    async def test_creator_skips_invalid_to_reach_the_requested_valid_count(self):
        uploader = Mock()
        uploader.get_videos = AsyncMock(side_effect=[{'page': {'count': 3}}, {'list': {'vlist': [{'bvid': bvid(i)} for i in range(3)]}}])
        self.detail.side_effect = [item(0, valid=False), item(1), item(2)]
        with patch.object(fetch.user, 'User', return_value=uploader):
            result = await fetch.fetch_unit({'kind': 'creator', 'payload': {'uid': '1', 'count': 2, 'start': 1}}, '', 1, Event(), self.progress)
        self.assertEqual(len(result['items']), 3)
        self.assertEqual(self.detail.await_count, 3)
        self.assertEqual(result['source_total'], 3)

    async def test_repeating_creator_pages_are_bounded(self):
        uploader = Mock()
        uploader.get_videos = AsyncMock(side_effect=[{'page': {'count': 100000}}, *[{'list': {'vlist': [{'bvid': bvid(1)}]}}] * 84])
        self.detail.return_value = item(1)
        with patch.object(fetch.user, 'User', return_value=uploader):
            result = await fetch.fetch_unit({'kind': 'creator', 'payload': {'uid': '1', 'count': 2, 'start': 1}}, '', 1, Event(), self.progress)
        self.assertEqual(uploader.get_videos.await_count, 85)
        self.detail.assert_awaited_once()
        self.assertEqual(len(result['items']), 1)

    async def test_rejection_and_cancel_stop_collection_and_close_client(self):
        self.detail.return_value = {'bvid': bvid(1), 'stat': {}, 'detail_error_code': -412}
        unit = {'kind': 'videos', 'payload': {'bvids': [bvid(1), bvid(2)]}}
        with self.assertRaisesRegex(ValueError, 'Bilibili rejected'):
            await fetch.fetch_unit(unit, '', 1, Event(), self.progress)
        self.detail.assert_awaited_once()
        canceled = Event()
        canceled.set()
        with self.assertRaisesRegex(ValueError, 'canceled'):
            await fetch.fetch_unit(unit, '', 1, canceled, self.progress)
        self.assertEqual(self.close.await_count, 2)

    async def test_http_rate_rejection_stops_before_requesting_more_videos(self):
        self.detail.return_value = {'bvid': bvid(1), 'stat': {}, 'detail_error_status': 429}
        with self.assertRaisesRegex(ValueError, 'Bilibili rejected'):
            await fetch.fetch_unit({'kind': 'videos', 'payload': {'bvids': [bvid(1), bvid(2)]}}, '', 1, Event(), self.progress)
        self.detail.assert_awaited_once()
        self.close.assert_awaited_once()

    async def test_http_status_is_preserved_by_the_shared_fetcher(self):
        from bilibili_ds.errors import NetworkException
        with patch.object(fetch.videos.video, 'Video', return_value=Mock(get_info=AsyncMock(side_effect=NetworkException(429, 'Too many requests')))):
            result = await self.original_detail({'bvid': 'BV1xx411c7mD'}, None)
        self.assertEqual(result['detail_error_status'], 429)

    async def test_workspace_selection_reuses_valid_count_algorithm_and_node_credential(self):
        uploader = Mock()
        uploader.get_videos = AsyncMock(side_effect=[{'page': {'count': 3}}, {'list': {'vlist': [{'bvid': bvid(i)} for i in range(3)]}}])
        self.detail.side_effect = [item(0, valid=False), item(1), item(2)]
        unit = {'kind': 'selection', 'payload': {'uid': '1', 'selection': {'kind': 'position', 'start': 1, 'end': 2}}}
        with patch.object(fetch.user, 'User', return_value=uploader) as user_type:
            result = await fetch.fetch_unit(unit, '', 2, Event(), self.progress)
        self.assertEqual(len(result['items']), 2)
        self.assertEqual(result['collection']['skipped_invalid'], 1)
        self.assertEqual(result['collection']['examined'], 3)
        self.assertEqual(user_type.call_args.kwargs['credential'], None)
        self.close.assert_awaited_once()

    async def test_workspace_time_and_metric_ranges_keep_existing_boundaries(self):
        for selection, count in (({'kind': 'published', 'start_time': '2026-10-01', 'end_time': '2026-10-01'}, 3),
                                 ({'kind': 'metric', 'metric': 'views', 'minimum': 50, 'maximum': 200}, 1)):
            uploader = Mock(get_videos=AsyncMock(side_effect=[{'page': {'count': 3}}, {'list': {'vlist': [{'bvid': bvid(i), 'created': 1790812800} for i in range(3)]}}]))
            self.detail.side_effect = [item(i) for i in range(3)]
            with patch.object(fetch.user, 'User', return_value=uploader):
                result = await fetch.fetch_unit({'kind': 'selection', 'payload': {'uid': '1', 'selection': selection}}, '', 1, Event(), self.progress)
            self.assertEqual(len(result['items']), count)

    async def test_creator_page_http_error_reaches_node_failure_without_raw_response(self):
        from bilibili_ds.errors import NetworkException
        failure = NetworkException(429, '<html>private response contents</html>')
        uploader = Mock(get_videos=AsyncMock(side_effect=[{'page': {'count': 100}},
            {'list': {'vlist': [{'bvid': bvid(i)} for i in range(30)]}}, failure]))
        self.detail.side_effect = [item(i) for i in range(30)]
        unit = {'kind': 'selection', 'payload': {'uid': '1', 'selection': {'kind': 'position', 'start': 1, 'end': 100}}}
        with patch.object(fetch.user, 'User', return_value=uploader), self.assertRaisesRegex(ValueError, 'HTTP 429') as error:
            await fetch.fetch_unit(unit, '', 1, Event(), self.progress)
        self.assertIn('30/100 valid videos', str(error.exception))
        self.assertNotIn('private response', str(error.exception))
        self.assertEqual(uploader.get_videos.await_count, 3)
        self.assertEqual(self.detail.await_count, 30)
        self.close.assert_awaited_once()
