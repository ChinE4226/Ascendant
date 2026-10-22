"""Weekly source handling and collection with simulated API responses."""
from contextlib import ExitStack
import unittest
from unittest.mock import AsyncMock, patch

from ascendant.web import dataset, weekly
from tests.unit.test_weekly import weekly_video


class WeeklyFetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.original = {'creator': 'existing dataset'}
        self.stack.enter_context(patch.object(dataset, 'CURRENT', self.original))
        self.stack.enter_context(patch.object(weekly.accounts, 'credential_from_env', return_value=None))
        self.configure = self.stack.enter_context(patch.object(weekly.client, 'configure_bilibili_client'))
        self.close = self.stack.enter_context(patch.object(weekly.client, 'close_bilibili_client', new_callable=AsyncMock))
        self.stack.enter_context(patch.object(weekly.asyncio, 'sleep', new_callable=AsyncMock))
        self.response = {'config': {'number': 393, 'name': 'Issue 393'}, 'list': [weekly_video('A', 100, 10), weekly_video('B', 300, 30)]}
        self.fetch = self.stack.enter_context(patch.object(weekly.hot, 'get_weekly_hot_videos', new_callable=AsyncMock, return_value=self.response))
        self.detail = self.stack.enter_context(patch.object(weekly.videos, 'fetch_video_detail', new_callable=AsyncMock))

    def test_source_parser_accepts_issues_and_only_weekly_bilibili_links(self):
        for source in ('393', 393, ' https://www.bilibili.com/v/popular/weekly?num=393 ',
                       'https://bilibili.com/v/popular/weekly/?num=393'):
            self.assertEqual(weekly.parse_weekly_source(source), 393)
        for source in ('', None, 0, -1, True, '3.5', 'https://example.com/v/popular/weekly?num=393',
                       'https://www.bilibili.com/video/BVfoo?num=393', 'https://www.bilibili.com/v/popular/weekly',
                       'https://www.bilibili.com/v/popular/weekly?num=1&num=2'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                weekly.parse_weekly_source(source)

    async def test_complete_list_needs_one_fetch_and_keeps_creator_dataset(self):
        result = await weekly.fetch_weekly_analysis({'source': '393'})
        self.fetch.assert_awaited_once_with(393, credential=None)
        self.detail.assert_not_awaited()
        self.assertEqual(result['summaries'][0]['mean'], 200)
        self.assertEqual(result['counts']['included'], 2)
        self.assertEqual(result['videos'][0]['creator'], 'Creator 1')
        self.assertIs(dataset.CURRENT, self.original)
        self.close.assert_awaited_once()

    async def test_snapshot_only_returns_fresh_rows_without_retaining_them(self):
        with patch.object(dataset, 'retain_collection') as retain:
            rows, metadata = await weekly.fetch_weekly_analysis({'source': '393'}, snapshot_only=True)
        retain.assert_not_called()
        self.assertEqual(rows, self.response['list'])
        self.assertEqual(metadata['scope']['number'], 393)
        self.assertIs(dataset.CURRENT, self.original)
        self.assertFalse(dataset.LOCK.locked())

    async def test_incomplete_list_data_gets_details_once_per_video(self):
        incomplete = {'bvid': 'C', 'title': 'Unavailable', 'stat': {}}
        self.response['list'] += [incomplete, incomplete, {'bvid': 'D', 'stat': {}}]
        self.detail.side_effect = [weekly_video('C', 500, 50), {'bvid': 'D', 'stat': {}, 'detail_error': 'Missing'}]
        result = await weekly.fetch_weekly_analysis({'source': '393'})
        self.assertEqual(self.detail.await_count, 2)
        self.assertEqual(result['counts']['included'], 3)
        self.assertEqual(result['counts']['invalid'], 1)
        self.assertEqual(result['counts']['duplicates'], 1)
        self.assertEqual(result['summaries'][0]['mean'], 300)

    async def test_bad_input_overlapping_collection_and_network_errors(self):
        with self.assertRaises(ValueError):
            await weekly.fetch_weekly_analysis({'source': 'https://example.com/'})
        self.configure.assert_not_called()
        with dataset.LOCK, self.assertRaisesRegex(ValueError, 'Another video operation'):
            await weekly.fetch_weekly_analysis({'source': '393'})
        self.fetch.side_effect = RuntimeError('Unavailable')
        with self.assertRaisesRegex(RuntimeError, 'Unavailable'):
            await weekly.fetch_weekly_analysis({'source': '393'})
        self.assertIs(dataset.CURRENT, self.original)
        self.close.assert_awaited_once()
        self.assertFalse(dataset.LOCK.locked())

    async def test_server_rejection_during_details_aborts_without_replacing_data(self):
        self.response['list'] = [{'bvid': 'A', 'stat': {}}]
        self.detail.return_value = {'bvid': 'A', 'stat': {}, 'detail_error_code': -412}
        with self.assertRaisesRegex(ValueError, 'rejected'):
            await weekly.fetch_weekly_analysis({'source': '393'})
        self.assertIs(dataset.CURRENT, self.original)
        self.close.assert_awaited_once()
