"""Known-value checks for descriptive statistics and paired engagement."""
import unittest
from ascendant.distributions import analyse_dataset, distribution, metric


class DistributionTests(unittest.TestCase):
    def test_quantiles_and_histogram(self):
        result = distribution([0, 10, 20, 30])
        self.assertEqual((result['q1'], result['median'], result['q3'], result['p90']), (7.5, 15, 22.5, 27))
        self.assertEqual(sum(b['count'] for b in result['bins']), 4)
        self.assertEqual(result['iqr'], 15)

    def test_empty_constant_and_singleton(self):
        self.assertIsNone(distribution([])['median'])
        self.assertEqual(distribution([7])['bins'], [{'low': 7, 'high': 7, 'count': 1}])
        self.assertIsNone(distribution([7])['lower_fence'])
        self.assertEqual(distribution([7] * 5)['iqr'], 0)

    def test_missing_invalid_and_zero(self):
        for value in [None, True, -1, 'nan', 'inf', 1.5, {}, 'bad']:
            self.assertIsNone(metric({'stat': {'view': value}}, 'view'))
        self.assertEqual(metric({'stat': {'view': 0}}, 'view'), 0)

    def test_outlier_and_duplicate_quality(self):
        items = [{'bvid': 'same', 'stat': {'view': value}} for value in [10, 10, 10, 10, 1000]]
        result = analyse_dataset(items)
        self.assertEqual(result['quality']['duplicate_rows'], 4)
        self.assertEqual(len(result['outliers']), 1)
        self.assertEqual(result['outliers'][0]['value'], 1000)
        self.assertEqual(result['summaries'][0]['count'], 5)

    def test_engagement_matched_rows(self):
        items = [{'stat': stat} for stat in [{'view': 100, 'like': 10}, {'view': 1000, 'like': 20},
                                            {'view': 900}, {'like': 999}, {'view': 0, 'like': 3}]]
        result = analyse_dataset(items)
        likes = result['engagement'][0]
        self.assertEqual(likes['count'], 2)
        self.assertEqual(likes['excluded'], 3)
        self.assertAlmostEqual(likes['pooled'], 30 / 1100)
        self.assertAlmostEqual(likes['median'], .06)
        self.assertIsNone(result['engagement_rows'][-1]['likes'])
