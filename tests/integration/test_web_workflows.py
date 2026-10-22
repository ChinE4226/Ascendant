"""Browser workflows exercised with simulated Bilibili responses."""

from contextlib import ExitStack
from datetime import datetime
import unittest
from unittest.mock import AsyncMock, Mock, patch

from ascendant.web import actions, plots, state, videos


class WebWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.items = [
            {"bvid": f"BV{day}", "title": f"Video {day}",
             "pubdate": int(datetime(2026, 9, day).timestamp()),
             "stat": {"view": day * 10, "like": day, "reply": 0, "favorite": 0, "coin": 0, "share": 0}}
            for day in (3, 2, 1)
        ]
        self.uploader = Mock()
        self.uploader.get_videos = AsyncMock(return_value={"page": {"count": 3}, "list": {"vlist": self.items}})
        self.stack.enter_context(patch.object(videos.user, "User", return_value=self.uploader))
        self.stack.enter_context(patch.object(videos, "selected_creator", return_value={"uid": "42", "name": "Example"}))
        self.stack.enter_context(patch.object(videos.account_service, "credential_from_env", return_value=None))
        self.stack.enter_context(patch.object(videos.client, "configure_bilibili_client"))
        self.close = self.stack.enter_context(patch.object(videos.client, "close_bilibili_client", new_callable=AsyncMock))
        self.stack.enter_context(patch.object(videos.asyncio, "sleep", new_callable=AsyncMock))
        self.detail = self.stack.enter_context(patch.object(videos.video_service, "fetch_video_detail", new_callable=AsyncMock, side_effect=lambda item, credential: dict(item)))
        self.stack.enter_context(patch.object(state, "PROGRESS", {"running": False, "message": "Idle.", "percent": 0, "count": None}))

    async def test_all_selection_modes(self):
        cases = [
            ({"kind": "position", "start": 1, "end": 2}, ["BV2", "BV3"]),
            ({"kind": "published", "start_time": "2026-09-02", "end_time": "2026-09-03"}, ["BV2", "BV3"]),
            ({"kind": "metric", "metric": "views", "minimum": 10, "maximum": 30}, ["BV2"]),
        ]
        for selection, expected in cases:
            with self.subTest(selection=selection):
                items, _, total, _ = await videos.fetch_selected_video_items({"action": "analysis", "selection": selection})
                self.assertEqual([item["bvid"] for item in items], expected)
                self.assertEqual(total, 3)
        self.assertEqual(self.close.await_count, 3)

    async def test_invalid_selection_still_closes_client(self):
        with self.assertRaisesRegex(ValueError, "Start time and end time"):
            await videos.fetch_selected_video_items({"selection": {"kind": "published"}})
        self.close.assert_awaited_once()

    def paginate(self, count):
        self.items = [{"bvid": f"BV{i}", "pubdate": 1000 - i,
                       "stat": {"view": i, "like": 0, "reply": 0, "favorite": 0, "coin": 0, "share": 0}}
                      for i in range(count)]
        self.uploader.get_videos.side_effect = lambda pn, ps, order: {
            "page": {"count": count}, "list": {"vlist": self.items[(pn - 1) * ps:pn * ps]}}

    async def test_collects_100_valid_videos_after_102_details_across_pages(self):
        self.paginate(150)
        self.items[29]['stat'] = {}
        self.items[99]['stat']['share'] = None
        items, _, total, collection = await videos.fetch_selected_video_items({
            'selection': {'kind': 'position', 'start': 1, 'end': 100}})
        self.assertEqual(len(items), 100)
        self.assertEqual(total, 150)
        self.assertEqual(collection, {'requested': 100, 'examined': 102, 'skipped_invalid': 2,
                                      'skipped_duplicates': 0, 'shortfall': 0})
        self.assertEqual(self.detail.await_count, 102)
        self.assertEqual({i['bvid'] for i in items}, {f'BV{i}' for i in range(102)} - {'BV29', 'BV99'})
        self.assertEqual([c.kwargs['pn'] for c in self.uploader.get_videos.await_args_list], [1, 1, 2, 3, 4])
        self.close.assert_awaited_once()

    async def test_position_start_and_shortfall_are_not_silently_clamped(self):
        self.paginate(35)
        self.items[32]['stat'] = {}
        items, _, _, collection = await videos.fetch_selected_video_items({
            'selection': {'kind': 'position', 'start': 32, 'end': 41}})
        self.assertEqual({i['bvid'] for i in items}, {'BV31', 'BV33', 'BV34'})
        self.assertEqual(collection['requested'], 10)
        self.assertEqual(collection['shortfall'], 7)
        self.assertEqual(collection['examined'], 4)

    async def test_invalid_metrics_are_skipped_but_zero_is_valid(self):
        values = [None, -1, True, 1.5, float('nan'), float('inf'), 'unknown']
        self.paginate(len(values) + 1)
        for item, value in zip(self.items[1:], values):
            item['stat']['share'] = value
        items, _, _, collection = await videos.fetch_selected_video_items({
            'selection': {'kind': 'position', 'start': 1, 'end': 2}})
        self.assertEqual([i['bvid'] for i in items], ['BV0'])
        self.assertEqual(collection['skipped_invalid'], len(values))
        self.assertEqual(collection['shortfall'], 1)

    async def test_empty_creator_reports_full_shortfall_without_details(self):
        self.paginate(0)
        items, _, total, collection = await videos.fetch_selected_video_items({
            'selection': {'kind': 'position', 'start': 1, 'end': 100}})
        self.assertEqual(items, [])
        self.assertEqual(total, 0)
        self.assertEqual(collection['shortfall'], 100)
        self.detail.assert_not_awaited()

    async def test_duplicate_summaries_do_not_count_toward_target(self):
        self.paginate(4)
        self.items[1] = self.items[0]
        items, _, _, collection = await videos.fetch_selected_video_items({
            'selection': {'kind': 'position', 'start': 1, 'end': 3}})
        self.assertEqual(len(items), 3)
        self.assertEqual(collection['skipped_duplicates'], 1)
        self.assertEqual(self.detail.await_count, 3)

    async def test_summary_failure_and_server_rejection_stop_collection(self):
        self.paginate(40)
        page = self.uploader.get_videos.side_effect
        def fail_page(pn, ps, order):
            if pn == 2:
                raise RuntimeError('blocked')
            return page(pn, ps, order)
        self.uploader.get_videos.side_effect = fail_page
        with self.assertRaisesRegex(ValueError, 'stopped on page 2'):
            await videos.fetch_selected_video_items({'selection': {'kind': 'position', 'end': 35}})
        self.uploader.get_videos.side_effect = page
        self.detail.reset_mock()
        self.detail.side_effect = lambda item, credential: {**item, 'stat': {}, 'detail_error_code': -412}
        with self.assertRaisesRegex(ValueError, 'rejected'):
            await videos.fetch_selected_video_items({'selection': {'kind': 'position', 'end': 35}})
        self.detail.assert_awaited_once()
        self.assertEqual(self.close.await_count, 2)

    async def test_bounded_ranges_skip_invalid_without_widening_range(self):
        self.items[1]['stat']['share'] = None
        for selection in ({'kind': 'published', 'start_time': '2026-09-02', 'end_time': '2026-09-03'},
                          {'kind': 'metric', 'metric': 'views', 'minimum': 10, 'maximum': 40}):
            with self.subTest(selection=selection):
                items, _, _, collection = await videos.fetch_selected_video_items({'selection': selection})
                self.assertEqual([i['bvid'] for i in items], ['BV3'])
                self.assertEqual(collection['skipped_invalid'], 1)
                self.assertIsNone(collection['requested'])

    async def test_page_two_rejection_preserves_code_counts_and_stops_without_retry(self):
        from ascendant.errors import ResponseCodeException
        self.paginate(150)
        pages = self.uploader.get_videos.side_effect
        failure = ResponseCodeException(-352, 'Request rejected', {'private_cookie': 'must-not-appear'})
        def page(pn, ps, order):
            if pn == 2:
                raise failure
            return pages(pn, ps, order)
        self.uploader.get_videos.side_effect = page
        with self.assertRaisesRegex(ValueError, 'API code -352') as error:
            await videos.fetch_selected_video_items({'selection': {'kind': 'position', 'start': 1, 'end': 100}})
        self.assertIn('30/100 valid videos collected before failure', str(error.exception))
        self.assertNotIn('must-not-appear', str(error.exception))
        self.assertIs(error.exception.__cause__, failure)
        self.assertEqual(self.detail.await_count, 30)
        self.assertEqual([call.kwargs['pn'] for call in self.uploader.get_videos.await_args_list], [1, 1, 2])
        self.close.assert_awaited_once()

    async def test_summary_timeout_reports_transport_cause_and_received_summary_count(self):
        import httpx
        self.uploader.get_videos.side_effect = [
            {'list': {'vlist': self.items * 10}}, httpx.ReadTimeout('private request details')]
        with self.assertRaisesRegex(ValueError, 'timed out') as error:
            await videos.fetch_web_video_summaries(self.uploader, 60, 'order', progress_label='Listing',
                                                   start_percent=15, end_percent=50)
        self.assertIn('30/60 video summaries', str(error.exception))
        self.assertNotIn('private request details', str(error.exception))
        self.assertEqual(self.uploader.get_videos.await_count, 2)
        self.detail.assert_not_awaited()

    async def test_analysis_division_and_plot_results(self):
        self.stack.enter_context(patch.object(actions, "fetch_selected_video_items", new_callable=AsyncMock, return_value=(self.items, "all", 3, {})))
        self.stack.enter_context(patch.object(actions, "selected_creator", return_value={"uid": "42", "name": "Example"}))
        save = self.stack.enter_context(patch.object(plots, "save_web_plot_png", return_value="sample.png"))
        listing = await actions.execute_video_action({"action": "list"})
        self.assertEqual(listing["count"], 3)
        analysis = await actions.execute_video_action({"action": "analysis"})
        self.assertEqual(analysis["summaries"][0]["mean"], 20)
        division = await actions.execute_video_action({"action": "division", "mode": "aggregate"})
        self.assertEqual(division["ratio"], 0.1)
        division = await actions.execute_video_action({"action": "division", "mode": "single"})
        self.assertEqual([row["ratio"] for row in division["rows"]], [0.1] * 3)
        plot = await actions.execute_video_action({"action": "plot", "field": "views"})
        self.assertEqual([point["value"] for point in plot["points"]], [30, 20, 10])
        self.assertTrue(plot["plot_id"])
        self.assertNotIn("plot_file", plot)
        save.assert_not_called()
        self.assertFalse(state.PROGRESS["running"])
        self.assertEqual(state.PROGRESS["percent"], 100)

    async def test_single_video_lookup_reports_progress_and_closes_client(self):
        with patch.object(videos.video, "Video") as video:
            video.return_value.get_info = AsyncMock(return_value={"bvid": "BV1xx411c7mD", "stat": {"view": 0}})
            result = await videos.fetch_single_video("BV1xx411c7mD")
        self.assertEqual(result["views"], 0)
        self.assertFalse(state.PROGRESS["running"])
        self.assertEqual(state.PROGRESS["count"], 1)
        self.close.assert_awaited_once()
