"""Filtering, exact sample size, and reproducible draws from the whole eligible pool."""

from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from ascendant.sampling import parse_sample_options, sample_candidates
from tests.unit.test_weekly import weekly_video


class SamplingTests(unittest.TestCase):
    def options(self, **extra):
        return parse_sample_options({"keyword": "camera", "seed": "42", **extra})

    def test_exact_unique_sample_after_invalid_and_duplicate_entries(self):
        good = [weekly_video(str(i), i * 100, i) for i in range(110)]
        rows = [{"bvid": "invalid-1", "stat": {}}, {"bvid": "invalid-2", "stat": {}}, *good, good[0]]
        sampled, report = sample_candidates(rows, self.options())
        s = report['sampling']
        self.assertEqual((len(sampled), s['eligible'], s['invalid'], s['duplicates'], s['shortfall']), (100, 110, 2, 1, 0))
        self.assertEqual(len({item['bvid'] for item in sampled}), 100)
        self.assertTrue(any(int(item['bvid']) >= 100 for item in sampled), 'Must draw from the whole pool, not only the first requested entries')
        self.assertEqual(report['summaries'][0]['mean'], sum(item['stat']['view'] for item in sampled) / 100)
        self.assertEqual(sample_candidates(rows, self.options())[0], sampled)
        self.assertNotEqual(sample_candidates(rows, self.options(seed='another seed'))[0], sampled)

    def test_inclusive_metric_filter_zero_valid_and_shortfall(self):
        rows = [weekly_video(str(i), i, 0) for i in range(4)]
        sampled, report = sample_candidates(rows, self.options(minimum='1', maximum='2'))
        self.assertEqual({item['bvid'] for item in sampled}, {'1', '2'})
        self.assertEqual(report['sampling']['filtered_out'], 2)
        self.assertEqual(report['sampling']['shortfall'], 98)
        sampled, report = sample_candidates(rows, self.options(minimum=0, maximum=0))
        self.assertEqual(sampled, [rows[0]])
        self.assertEqual(report['engagement'][0]['excluded'], 1)

    def test_date_filter_includes_entire_end_day_in_beijing_time(self):
        dates = ['2026-09-30 23:59:59', '2026-10-01 00:00:00', '2026-10-01 23:59:59', '2026-10-02 00:00:00']
        rows = [{**weekly_video(str(i), i, 0), 'pubdate': int(datetime.strptime(date, '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())}
                for i, date in enumerate(dates)]
        rows.append(weekly_video('missing date', 100, 1))
        sampled, report = sample_candidates(rows, self.options(published_start='2026-10-01', published_end='2026-10-01'))
        self.assertEqual({item['bvid'] for item in sampled}, {'1', '2'})
        self.assertEqual(report['sampling']['filtered_out'], 3)

    def test_empty_pool_is_an_explicit_shortfall_with_empty_summaries(self):
        sampled, report = sample_candidates([], self.options())
        self.assertEqual(sampled, [])
        self.assertEqual(report['sampling']['shortfall'], 100)
        self.assertIsNone(report['summaries'][0]['mean'])

    def test_options_validate_before_collection(self):
        cases = [{"keyword": ""}, {"sample_size": 0}, {"sample_size": 1.2}, {"sample_size": True},
                 {"pool_size": 501}, {"sample_size": 101, "pool_size": 100}, {"metric": "unknown"},
                 {"minimum": "bad"}, {"minimum": "nan"}, {"maximum": "inf"}, {"minimum": -1},
                 {"minimum": '1.1'}, {"minimum": 2, "maximum": 1}, {"published_start": "2026-02-30"},
                 {"published_start": "2026-10-03", "published_end": "2026-10-02"},
                 {"order": "invalid"}, {"seed": 'a' * 129}, {"category_id": 'abc'}]
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.options(**extra)
        parsed = self.options(minimum='1.5k', maximum='2m', category_id='17')
        self.assertEqual((parsed['minimum'], parsed['maximum'], parsed['category_id']), (1500, 2000000, 17))
        self.assertTrue(self.options(seed='')['seed'])
