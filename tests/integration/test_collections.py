"""Collected cohorts reuse the normal tools without network, disk or creator replacement."""
from collections import OrderedDict
from contextlib import ExitStack
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch

from ascendant.web import actions, dataset, sampling, weekly, plots
from tests.unit.test_weekly import weekly_video


class CollectionAnalysisTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(dataset, 'COHORTS', OrderedDict()))
        self.previous = {'key': ('42', None, '{}'), 'data': ([weekly_video('old', 50, 5)], 'old', 1),
                         'meta': {'count': 1}}
        self.stack.enter_context(patch.object(dataset, 'CURRENT', deepcopy(self.previous)))
        self.stack.enter_context(patch.object(dataset, 'selected_creator', return_value=None))
        self.stack.enter_context(patch.object(actions, 'selected_creator', return_value=None))
        self.fetch = self.stack.enter_context(patch.object(actions, 'fetch_workspace_items', new_callable=AsyncMock))
        self.followers = self.stack.enter_context(patch.object(actions, 'fetch_followers', new_callable=AsyncMock))
        self.stack.enter_context(patch.object(plots, '_pending_plots', OrderedDict()))
        self.rows = [dict(weekly_video('A', 0, 5), pubdate=300),
                     dict(weekly_video('B', 100, 10), pubdate=100),
                     dict(weekly_video('C', 300, 30, 2), pubdate=200)]

    def retain(self, kind='weekly'):
        return dataset.retain_collection(self.rows, kind=kind, label='Weekly popular · Issue 393' if kind == 'weekly' else 'Random sample · camera',
            started_at='2026-10-04T00:00:00Z', collected_at='2026-10-04T00:00:10Z',
            scope={'number': 393} if kind == 'weekly' else {'seed': 'test', 'eligible': 10},
            collection={'requested': 3, 'examined': 5, 'skipped_invalid': 1, 'skipped_duplicates': 1, 'shortfall': 0})

    async def test_all_tools_use_exact_collected_rows_and_preserve_creator(self):
        for kind in ('weekly', 'random'):
            meta = self.retain(kind)
            request = {'collection_id': meta['collection_id'], 'reuse_only': True}
            analysis = await actions.execute_video_action({'action': 'analysis', **request})
            self.assertEqual(analysis['count'], 3)
            self.assertAlmostEqual(analysis['summaries'][0]['mean'], 400 / 3)
            self.assertEqual(analysis['dataset']['scope'], meta['scope'])
            ratio = await actions.execute_video_action({'action': 'division', 'mode': 'aggregate', **request})
            self.assertEqual((ratio['eligible_count'], ratio['excluded_count'], ratio['ratio']), (2, 1, .1))
            chart = await actions.execute_video_action({'action': 'plot', 'plot_mode': 'field', **request})
            self.assertEqual([point['value'] for point in chart['points']], [100, 300, 0])
            self.assertIsNone(chart['selected_creator'])
            self.assertIsNotNone(chart['plot_id'], 'Cohorts without a selected creator support explicit PNG saving')
            self.assertEqual(plots._pending_plots[chart['plot_id']]['selected']['name'], meta['source_label'])
            self.assertEqual(dataset.CURRENT, self.previous)
        self.fetch.assert_not_awaited()
        self.followers.assert_not_awaited()

    async def test_filters_change_active_count_without_mutating_retained_rows(self):
        meta = self.retain()
        response = await actions.execute_video_action({'action': 'analysis', 'collection_id': meta['collection_id'],
            'local_filter': {'minimum_views': 100, 'maximum_views': 200}})
        self.assertEqual((response['count'], response['dataset']['count']), (1, 3))
        self.assertEqual(response['dataset']['filter_counts'], {'fetched': 3, 'included': 1, 'excluded': 2, 'view_range': 2})
        all_rows = await actions.execute_video_action({'action': 'list', 'collection_id': meta['collection_id']})
        self.assertEqual(len(all_rows['videos']), 3)
        self.assertEqual(all_rows['dataset']['collection']['examined'], 5)
        self.assertEqual(dataset.CURRENT, self.previous)

    async def test_no_implicit_refetch_and_no_wrong_creator_followers(self):
        meta = self.retain()
        for payload, message in [({'refresh': True}, 'Recollect'), ({'numerator': 'followers', 'action': 'division'}, 'Follower ratios')]:
            with self.assertRaisesRegex(ValueError, message):
                await actions.execute_video_action({'action': 'analysis', 'collection_id': meta['collection_id'], **payload})
        self.fetch.assert_not_awaited()
        self.followers.assert_not_awaited()

    async def test_bounded_retention_expiry_and_deep_copy(self):
        first = self.retain()
        for _ in range(dataset.COHORT_LIMIT):
            self.retain('random')
        self.assertEqual(len(dataset.collection_entries()), dataset.COHORT_LIMIT)
        with self.assertRaisesRegex(ValueError, 'expired'):
            await actions.execute_video_action({'action': 'analysis', 'collection_id': first['collection_id']})
        identity = dataset.collection_entries()[0]['collection_id']
        rows, _ = await dataset.acquire({'collection_id': identity}, self.fetch)
        rows[0][0]['stat']['view'] = 99999
        rows, _ = await dataset.acquire({'collection_id': identity}, self.fetch)
        self.assertEqual(rows[0][0]['stat']['view'], 100)
        self.fetch.assert_not_awaited()

    async def test_collectors_retain_raw_rows_and_failure_keeps_previous_cohorts(self):
        with ExitStack() as stack:
            for module in (weekly, sampling):
                stack.enter_context(patch.object(module.accounts, 'credential_from_env', return_value=None))
                stack.enter_context(patch.object(module.client, 'configure_bilibili_client'))
                stack.enter_context(patch.object(module.client, 'close_bilibili_client', new_callable=AsyncMock))
            stack.enter_context(patch('asyncio.sleep', new_callable=AsyncMock))
            hot = stack.enter_context(patch.object(weekly.hot, 'get_weekly_hot_videos', new_callable=AsyncMock,
                return_value={'config': {'number': 393}, 'list': self.rows + [self.rows[0], {'bvid': 'invalid', 'stat': {}}]}))
            stack.enter_context(patch.object(weekly.videos, 'fetch_video_detail', new_callable=AsyncMock, return_value={'bvid': 'invalid', 'stat': {}}))
            result = await weekly.fetch_weekly_analysis({'source': '393'})
            restored = dataset.workspace_data()['reports']['weekly']
            self.assertEqual(restored['issue'], result['issue'])
            self.assertEqual(restored['counts'], result['counts'])
            self.assertEqual(restored['excluded'], result['excluded'])
            self.assertEqual({row['bvid'] for row in restored['videos']}, {row['bvid'] for row in result['videos']})
            restored['issue']['number'] = 999
            self.assertEqual(dataset.workspace_data()['reports']['weekly']['issue']['number'], 393)
            response = await actions.execute_video_action({'action': 'list', 'collection_id': result['dataset']['collection_id']})
            self.assertEqual({row['bvid'] for row in response['videos']}, {'A', 'B', 'C'})
            self.assertEqual(response['dataset']['collection']['skipped_invalid'], 1)
            hot.side_effect = ValueError('HTTP 412')
            with self.assertRaisesRegex(ValueError, '412'):
                await weekly.fetch_weekly_analysis({'source': '394'})
            self.assertEqual(len(dataset.collection_entries()), 1)
            search = stack.enter_context(patch.object(sampling.search, 'search_by_type', new_callable=AsyncMock,
                return_value={'result': self.rows}))
            detail = stack.enter_context(patch.object(sampling.videos, 'fetch_video_detail', new_callable=AsyncMock, side_effect=lambda row, credential: row))
            result = await sampling.fetch_random_sample({'keyword': 'camera', 'sample_size': 2, 'pool_size': 3, 'seed': 'fixed'})
            restored = dataset.workspace_data()['reports']['random']
            self.assertEqual(restored['sampling'], result['sampling'])
            self.assertEqual(restored['summaries'], result['summaries'])
            self.assertEqual(len(restored['videos']), 2)
            response = await actions.execute_video_action({'action': 'list', 'collection_id': result['dataset']['collection_id']})
            self.assertEqual({row['bvid'] for row in response['videos']}, {row['bvid'] for row in result['videos']})
            self.assertEqual(dataset.CURRENT, self.previous)

    async def test_cleanup_failure_does_not_publish_or_evict_a_collection(self):
        previous = self.retain()
        with ExitStack() as stack:
            stack.enter_context(patch.object(weekly.accounts, 'credential_from_env', return_value=None))
            stack.enter_context(patch.object(weekly.client, 'configure_bilibili_client'))
            close = stack.enter_context(patch.object(weekly.client, 'close_bilibili_client', new_callable=AsyncMock, side_effect=ValueError('Close failed')))
            stack.enter_context(patch('asyncio.sleep', new_callable=AsyncMock))
            stack.enter_context(patch.object(weekly.hot, 'get_weekly_hot_videos', new_callable=AsyncMock,
                return_value={'config': {'number': 393}, 'list': self.rows}))
            with self.assertRaisesRegex(ValueError, 'Close failed'):
                await weekly.fetch_weekly_analysis({'source': '393'})
            close.assert_awaited_once()
            self.assertEqual([entry['collection_id'] for entry in dataset.collection_entries()], [previous['collection_id']])
            self.assertEqual(dataset.CURRENT, self.previous)
            self.assertFalse(dataset.LOCK.locked())
