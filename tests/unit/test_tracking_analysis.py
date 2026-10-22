"""Actual-time differences, counter corrections and gaps in a single-video series."""

from datetime import datetime, timedelta, timezone
import tracemalloc
import unittest
from ascendant.tracking_analysis import analyse_observations


def row(identity, hour, value):
    return {'id': identity, 'collected_at': f'2026-10-08T{hour:02}:00:00Z',
            'views': value, 'likes': value, 'published_at': 1791417600, 'source': 'scheduled'}


class TrackingAnalysisTests(unittest.TestCase):
    def test_bounded_points_preserve_full_history_summary_and_adjacent_rates(self):
        rows = [row(1, 2, 100), row(2, 3, 80), row(3, 4, None),
                row(4, 5, 160), row(5, 6, 220), row(6, 7, 300)]
        full = analyse_observations(rows)
        bounded = analyse_observations(iter(rows), point_limit=2)
        self.assertEqual(bounded['points'], full['points'][-2:])
        self.assertEqual(bounded['summary'], full['summary'])
        self.assertEqual(bounded['summary']['observations'], 6)
        self.assertEqual(bounded['summary']['baseline_at'], rows[0]['collected_at'])
        self.assertEqual(bounded['points'][0]['delta'], 60)
        self.assertEqual(analyse_observations([], point_limit=2)['points'], [])
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                analyse_observations([], point_limit=limit)

    def test_memory_stays_bounded_as_persisted_history_grows(self):
        baseline = datetime(2026, 10, 8, tzinfo=timezone.utc)
        def rows(count):
            for identity in range(count):
                yield {'id': identity, 'collected_at': (baseline + timedelta(seconds=identity)).isoformat(),
                       'views': identity, 'source': 'scheduled'}
        def peak(count):
            tracemalloc.start()
            try:
                result = analyse_observations(rows(count), point_limit=500)
                memory = tracemalloc.get_traced_memory()[1]
                self.assertEqual(len(result['points']), 500)
                self.assertEqual(result['summary']['observations'], count)
                self.assertEqual(result['points'][0]['id'], count - 500)
                return memory
            finally:
                tracemalloc.stop()
        self.assertLess(peak(10000), peak(1000) * 2)

    def test_irregular_intervals_use_actual_elapsed_time(self):
        result = analyse_observations([row(1, 2, 100), row(2, 3, 160), row(3, 6, 280)])
        self.assertEqual([p['rate_per_hour'] for p in result['points']], [None, 60, 40])
        self.assertEqual(result['points'][2]['rate_change_per_hour'], -20)
        self.assertEqual(result['points'][1]['growth_percent'], 60)
        self.assertEqual(result['points'][2]['video_age_hours'], 6)
        self.assertEqual(result['summary']['average_per_hour'], 45)

    def test_missing_values_break_adjacent_comparisons(self):
        result = analyse_observations([row(1, 2, 100), row(2, 3, None), row(3, 5, 300), row(4, 6, 400)])
        self.assertEqual([p['delta'] for p in result['points']], [None, None, None, 100])
        self.assertIsNone(result['points'][-1]['rate_change_per_hour'])
        self.assertEqual(result['summary']['missing_observations'], 1)

    def test_zero_baseline_and_decreasing_counter_are_honest(self):
        result = analyse_observations([row(1, 2, 0), row(2, 3, 100), row(3, 4, 80), row(4, 5, 110)])
        self.assertIsNone(result['points'][1]['growth_percent'])
        self.assertEqual(result['points'][2]['rate_per_hour'], -20)
        self.assertTrue(result['points'][2]['counter_decreased'])
        self.assertIsNone(result['points'][3]['rate_change_per_hour'])
        self.assertEqual(result['summary']['counter_decreases'], 1)

    def test_empty_single_equal_time_and_metric_validation(self):
        self.assertEqual(analyse_observations([])['summary']['observations'], 0)
        self.assertIsNone(analyse_observations([row(1, 2, 10)])['summary']['average_per_hour'])
        self.assertIsNone(analyse_observations([row(1, 2, 10), row(2, 2, 20)])['points'][1]['rate_per_hour'])
        self.assertEqual(analyse_observations([row(1, 2, 10)], 'likes')['summary']['latest_value'], 10)
        with self.assertRaises(ValueError):
            analyse_observations([], 'invalid')
        with self.assertRaises(ValueError):
            analyse_observations([row(1, 3, 20), row(2, 2, 10)])
