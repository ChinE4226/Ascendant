"""Regression tests for reusable calculations and input parsing."""

from datetime import datetime
import unittest

from ascendant import accounts, analysis, plotting, selection
from ascendant.web.creators import normalize_web_creator


class CalculationTests(unittest.TestCase):
    def setUp(self):
        self.items = [
            {"bvid": "B", "pubdate": 200, "stat": {"view": 100, "like": 10}},
            {"bvid": "A", "pubdate": 100, "stat": {"view": 0, "like": 0}},
            {"bvid": "C", "created": 300, "stat": {"view": 200}},
            {"bvid": "D", "stat": {}},
        ]

    def test_summary_ignores_missing_values_but_counts_zero(self):
        summaries = analysis.calculate_metric_summary(self.items)
        self.assertEqual(summaries[0], {"label": "Views", "count": 3, "mean": 100, "median": 100})
        self.assertEqual(summaries[1], {"label": "Likes", "count": 2, "mean": 5, "median": 5})
        self.assertEqual(summaries[2], {"label": "Replies", "count": 0, "mean": None, "median": None})

    def test_sorting_keeps_missing_values_last(self):
        for descending, expected in ((True, ["C", "B", "A", "D"]), (False, ["A", "B", "C", "D"])):
            mode = selection.video_sort_mode("views", "Views", "view", descending)
            self.assertEqual([item["bvid"] for item in selection.sort_video_items(self.items, mode)], expected)

    def test_filters_preserve_boundary_rules(self):
        self.assertEqual(selection.filter_items_by_published_time_range(self.items, 100, 200), self.items[:2])
        self.assertEqual(selection.filter_items_by_metric_range(self.items, "view", 0, 200), self.items[:1])
        self.assertEqual(selection.filter_items_by_metric_range(self.items, "view", None, None), self.items[:3])

    def test_ratio_handles_zero_and_missing_values(self):
        fields = {field["field"]: field for field in analysis.division_field_choices()}
        self.assertEqual(analysis.ratio_for_item(self.items[0], fields["likes"], fields["views"]), (10, 100, 0.1))
        self.assertEqual(analysis.ratio_for_item(self.items[1], fields["likes"], fields["views"]), (0, 0, None))
        self.assertEqual(analysis.ratio_for_item(self.items[2], fields["likes"], fields["views"]), (None, 200, None))
        self.assertEqual(
            analysis.aggregate_division_value(self.items, fields["followers"], {"follower": 50}),
            {"value": 200, "count": 4, "missing": 0, "base_value": 50},
        )

    def test_plot_points_are_ordered_and_missing_values_skipped(self):
        x, y, skipped = plotting.build_plot_points(self.items, lambda item: analysis.video_metric_value(item, "like"))
        self.assertEqual(x, [datetime.fromtimestamp(100), datetime.fromtimestamp(200)])
        self.assertEqual(y, [0.0, 10.0])
        self.assertEqual(skipped, 2)

    def test_parsing(self):
        self.assertEqual(selection.parse_int_range_value("2.5m"), 2_500_000)
        self.assertEqual(selection.parse_int_range_value("100,000"), 100_000)
        self.assertIsNone(selection.parse_int_range_value("-1"))
        self.assertIsNone(selection.parse_datetime_input("invalid"))
        self.assertEqual(selection.parse_datetime_input("2026-09-27", end_of_day=True), datetime(2026, 9, 27, 23, 59, 59))
        self.assertEqual(accounts.parse_cookie_header("key=a=b; other=c; ignored"), {"key": "a=b", "other": "c"})

    def test_web_creator_normalization(self):
        self.assertEqual(normalize_web_creator({"name": "Example", "space": "https://space.bilibili.com/42"}),
                         {"name": "Example", "space": "https://space.bilibili.com/42", "uid": "42"})
        self.assertEqual(normalize_web_creator({"uid": 42})["name"], "Creator-42")
        self.assertIsNone(normalize_web_creator(None))


if __name__ == "__main__":
    unittest.main()
