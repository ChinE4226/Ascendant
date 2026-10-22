"""Bounded collection, detail validation, and dataset isolation using simulated APIs."""

from contextlib import ExitStack
import unittest
from unittest.mock import AsyncMock, patch

from ascendant.web import dataset, sampling
from tests.unit.test_weekly import weekly_video


class SampleFetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.original = {'creator': 'existing dataset'}
        self.stack.enter_context(patch.object(dataset, 'CURRENT', self.original))
        self.stack.enter_context(patch.object(sampling.accounts, 'credential_from_env', return_value=None))
        self.configure = self.stack.enter_context(patch.object(sampling.client, 'configure_bilibili_client'))
        self.close = self.stack.enter_context(patch.object(sampling.client, 'close_bilibili_client', new_callable=AsyncMock))
        self.stack.enter_context(patch.object(sampling.asyncio, 'sleep', new_callable=AsyncMock))
        self.search = self.stack.enter_context(patch.object(sampling.search, 'search_by_type', new_callable=AsyncMock))
        self.detail = self.stack.enter_context(patch.object(sampling.videos, 'fetch_video_detail', new_callable=AsyncMock))
        self.detail.side_effect = lambda row, credential: {**weekly_video(row['bvid'], int(row['bvid']) * 100, 10), 'pubdate': 1790812800}

    def payload(self, **extra):
        return {'keyword': 'camera', 'sample_size': 5, 'pool_size': 25, 'seed': 'test', **extra}

    async def test_snapshot_only_returns_fresh_rows_without_retaining_them(self):
        self.search.return_value = {'result': [{'bvid': '1'}, {'bvid': '2'}]}
        with patch.object(dataset, 'retain_collection') as retain:
            rows, metadata = await sampling.fetch_random_sample(self.payload(sample_size=2, pool_size=2), snapshot_only=True)
        retain.assert_not_called()
        self.assertEqual({row['bvid'] for row in rows}, {'1', '2'})
        self.assertEqual(metadata['scope']['seed'], 'test')
        self.assertIs(dataset.CURRENT, self.original)
        self.assertFalse(dataset.LOCK.locked())

    async def test_collects_whole_pool_and_uses_refreshed_metric_filter(self):
        self.search.side_effect = [{'result': [{'bvid': str(i)} for i in range(20)]},
                                   {'result': [{'bvid': str(i)} for i in range(20, 40)]}]
        result = await sampling.fetch_random_sample(self.payload(minimum=2000))
        self.assertEqual(self.search.await_count, 2)
        self.assertEqual(self.detail.await_count, 25)
        self.assertEqual({row['bvid'] for row in result['videos']}, {'20', '21', '22', '23', '24'})
        self.assertEqual(result['sampling']['filtered_out'], 20)
        self.assertEqual(result['sampling']['shortfall'], 0)
        self.assertEqual(result['videos'][0]['creator'], 'Creator 1')
        self.assertIs(dataset.CURRENT, self.original)
        self.close.assert_awaited_once()
        self.assertFalse(dataset.LOCK.locked())

    async def test_missing_metrics_and_duplicates_produce_honest_shortfall(self):
        self.search.return_value = {'result': [{'bvid': '1'}, {'bvid': '1'}, {'bvid': '2'}, {'title': 'no identity'}]}
        self.detail.side_effect = [weekly_video('1', 100, 10), {'bvid': '2', 'stat': {}}]
        result = await sampling.fetch_random_sample(self.payload(pool_size=4, sample_size=3))
        self.assertEqual(self.detail.await_count, 2)
        self.assertEqual((result['sampling']['invalid'], result['sampling']['duplicates'], result['sampling']['shortfall']), (2, 1, 2))
        self.assertEqual(len(result['videos']), 1)

    async def test_repeating_pages_and_empty_pages_are_bounded(self):
        self.search.return_value = {'result': [{'bvid': '1'}] * 20}
        result = await sampling.fetch_random_sample(self.payload())
        self.assertEqual((self.search.await_count, self.detail.await_count), (2, 1))
        self.assertEqual(result['sampling']['duplicates'], 24)
        self.search.reset_mock()
        self.search.return_value = {'result': []}
        result = await sampling.fetch_random_sample(self.payload())
        self.search.assert_awaited_once()
        self.assertEqual(result['sampling']['shortfall'], 5)

    async def test_date_end_is_advanced_for_api_and_category_is_forwarded(self):
        self.search.return_value = {'result': []}
        await sampling.fetch_random_sample(self.payload(published_start='2026-10-01', published_end='2026-10-01', category_id='17'))
        params = self.search.call_args.kwargs
        self.assertEqual((params['time_start'], params['time_end'], params['video_zone_type']), ('2026-10-01', '2026-10-02', 17))
        self.assertEqual(params['search_type'], sampling.search.SearchObjectType.VIDEO)

    async def test_validation_lock_errors_and_server_rejection_preserve_dataset(self):
        with self.assertRaises(ValueError):
            await sampling.fetch_random_sample(self.payload(keyword=''))
        self.configure.assert_not_called()
        with dataset.LOCK, self.assertRaisesRegex(ValueError, 'Another video operation'):
            await sampling.fetch_random_sample(self.payload())
        self.search.return_value = {'result': [{'bvid': '1'}]}
        self.detail.side_effect = None
        self.detail.return_value = {'bvid': '1', 'stat': {}, 'detail_error_code': -412}
        with self.assertRaisesRegex(ValueError, 'rejected'):
            await sampling.fetch_random_sample(self.payload())
        self.assertIs(dataset.CURRENT, self.original)
        self.close.assert_awaited_once()
        self.assertFalse(dataset.LOCK.locked())

    async def test_network_and_malformed_results_release_lock(self):
        for result in (None, {'result': 'bad'}, {'result': [None]}):
            self.search.return_value = result
            with self.assertRaisesRegex(ValueError, 'invalid search'):
                await sampling.fetch_random_sample(self.payload())
            self.assertFalse(dataset.LOCK.locked())
        self.search.side_effect = RuntimeError('Search unavailable')
        with self.assertRaisesRegex(RuntimeError, 'Search unavailable'):
            await sampling.fetch_random_sample(self.payload())
        self.assertFalse(dataset.LOCK.locked())
        self.assertIs(dataset.CURRENT, self.original)
