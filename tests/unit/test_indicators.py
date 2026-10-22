"""Indicator math, warm-up windows, and zero-baseline behavior."""
import unittest
from ascendant.indicators import exponential_average, rolling_median, relative_performance


class IndicatorTests(unittest.TestCase):
    def test_ema_seed_and_recursive_weight(self):
        self.assertEqual(exponential_average([1, 2, 3, 10, 0], 3), [None, None, 2, 6, 3])
        for period in (10, 20):
            result = exponential_average([10] * period + [20], period)
            self.assertEqual(result[:period - 1], [None] * (period - 1))
            self.assertEqual(result[period - 1], 10)
            self.assertAlmostEqual(result[-1], 10 + 10 * 2 / (period + 1))
            self.assertEqual(exponential_average([0] * period, period)[-1], 0)

    def test_median_odd_even_and_outlier(self):
        self.assertEqual(rolling_median([1, 100, 2, 3, 4, 5], 5), [None] * 4 + [3, 4])
        self.assertEqual(rolling_median([1, 2, 3, 4], 4), [None] * 3 + [2.5])
        self.assertEqual(rolling_median([10] * 9 + [1000000], 10)[-1], 10)

    def test_relative_excludes_current_and_handles_zero(self):
        self.assertEqual(relative_performance([10] * 20 + [30]), [None] * 20 + [3])
        self.assertEqual(relative_performance([10] * 20 + [0])[-1], 0)
        self.assertEqual(relative_performance([0] * 20 + [30]), [None] * 21)
        self.assertEqual(relative_performance([0] * 20 + [30, 3])[-1], 2)
        self.assertEqual(relative_performance([1e-300] * 20 + [1e300])[-1], None)

    def test_short_empty_and_invalid_windows(self):
        for compute in (exponential_average, rolling_median, relative_performance):
            self.assertEqual(compute([], 10), [])
            self.assertEqual(compute([1, 2], 10), [None, None])
            for period in (0, -1, True, 1.5):
                with self.assertRaises(ValueError):
                    compute([1, 2], period)
