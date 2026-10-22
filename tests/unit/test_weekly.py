"""Known weekly cohort averages, eligibility, and duplicates."""
import unittest

from ascendant.weekly import summarize_weekly_items


def weekly_video(bvid, views, likes, creator=1):
    return {"bvid": bvid, "title": bvid, "owner": {"mid": creator, "name": f"Creator {creator}"},
            "stat": {"view": views, "like": likes, "reply": 0, "favorite": 0, "coin": 0, "share": 0}}


class WeeklySummaryTests(unittest.TestCase):
    def test_average_counts_and_equal_weight_vs_pooled_engagement(self):
        valid, result = summarize_weekly_items([weekly_video('A', 100, 10), weekly_video('B', 1000, 20),
                                                weekly_video('C', 0, 5, 2)])
        self.assertEqual(len(valid), 3)
        views = next(row for row in result['summaries'] if row['field'] == 'views')
        self.assertEqual(views['total'], 1100)
        self.assertAlmostEqual(views['mean'], 1100 / 3)
        self.assertEqual(views['median'], 100)
        likes = next(row for row in result['engagement'] if row['field'] == 'likes')
        self.assertAlmostEqual(likes['mean_per_video'], .06)
        self.assertAlmostEqual(likes['pooled'], 30 / 1100)
        self.assertEqual((likes['count'], likes['excluded']), (2, 1))
        self.assertEqual(result['counts']['creators'], 2)
        self.assertEqual(len(result['engagement']), 5)

    def test_invalid_and_duplicate_videos_do_not_bias_averages(self):
        good = weekly_video('A', 100, 10)
        rows = [good, good, {'bvid': 'B', 'stat': {'view': 10000}}, weekly_video('C', 1, -1),
                {**weekly_video('D', 10, 1), 'bvid': None}]
        valid, result = summarize_weekly_items(rows)
        self.assertEqual(valid, [good])
        self.assertEqual(result['counts'], {'listed': 5, 'included': 1, 'invalid': 3, 'duplicates': 1, 'creators': 1})
        self.assertEqual(result['summaries'][0]['mean'], 100)
        self.assertEqual(len(result['excluded']), 4)
        self.assertEqual(rows[2]['stat'], {'view': 10000})

    def test_empty_and_zero_view_cohorts(self):
        _, empty = summarize_weekly_items([])
        self.assertIsNone(empty['summaries'][0]['mean'])
        self.assertIsNone(empty['engagement'][0]['pooled'])
        _, zero = summarize_weekly_items([weekly_video('A', 0, 0)])
        self.assertEqual(zero['summaries'][0]['mean'], 0)
        self.assertEqual(zero['engagement'][0]['excluded'], 1)
        self.assertIsNone(zero['engagement'][0]['mean_per_video'])
