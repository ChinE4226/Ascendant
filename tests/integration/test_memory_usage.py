"""Memory diagnostics include all retained sources without fetching or saving."""

from collections import OrderedDict
from contextlib import ExitStack
import unittest
from unittest.mock import patch

from ascendant.web import dataset, memory, missions, plots
from ascendant.distributed.coordinator import Coordinator


class MemoryUsageTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.entry = {'data': ([{'bvid': 'example', 'stat': {'view': 100}}], 'First video', 1),
                      'meta': {'source_label': 'Creator 7', 'source_kind': 'creator', 'collected_at': '2026-10-09T00:00:00Z'}}
        self.core = Coordinator(clock=lambda: 100)
        for module, name, value in ((dataset, 'CURRENT', self.entry), (dataset, 'COHORTS', OrderedDict()),
                (dataset, 'MISSION_COLLECTIONS', OrderedDict()), (missions, 'MISSIONS', OrderedDict()),
                (plots, '_pending_plots', OrderedDict()), (memory, 'COORDINATOR', self.core)):
            self.stack.enter_context(patch.object(module, name, value))
        self.stack.enter_context(patch.object(memory, 'process_memory', return_value={'pid': 42, 'rss_bytes': 200000000, 'sampled_at': '2026-10-09T00:00:00Z'}))

    def test_retained_dataset_estimates_count_independent_mission_copies(self):
        dataset.COHORTS['sample'] = self.entry
        dataset.MISSION_COLLECTIONS['mission'] = {**self.entry, 'meta': {**self.entry['meta'], 'mission_id': 'mission'}}
        missions.MISSIONS['mission'] = {'results': {'analysis': 'kept'}}
        plots._pending_plots['chart'] = {}
        self.core.jobs['task'] = {}
        with patch('builtins.open', side_effect=AssertionError('No file writes or reads')):
            report = memory.overview()
        self.assertEqual(report['server']['rss_bytes'], 200000000)
        self.assertEqual(report['retained_videos'], 3)
        self.assertEqual([row['kind'] for row in report['collections']], ['creator', 'creator', 'mission'])
        self.assertEqual(report['dataset_estimated_bytes'], sum(row['estimated_bytes'] for row in report['collections']))
        self.assertEqual(report['cache_counts'], {'missions': 1, 'unsaved_charts': 1, 'node_tasks': 1})
        self.assertNotIn('example', str(report))
        self.assertEqual(missions.MISSIONS['mission']['results'], {'analysis': 'kept'})

    def test_release_reduces_dataset_estimate_without_claiming_rss_must_fall(self):
        dataset.COHORTS['sample'] = self.entry
        before = memory.overview()
        dataset.release_collection('sample', expected_collected_at=self.entry['meta']['collected_at'])
        after = memory.overview()
        self.assertLess(after['dataset_estimated_bytes'], before['dataset_estimated_bytes'])
        self.assertEqual(after['retained_videos'], 1)
        self.assertEqual(after['server'], before['server'])
        self.assertIs(dataset.CURRENT, self.entry)

    def test_node_reports_are_marked_stale_and_missing_is_not_zero(self):
        self.core.server = object()
        self.core.nodes = {'recent': {'id': 'recent', 'name': 'Node A', 'rss_bytes': 500, 'memory_received_at': 95},
                           'old': {'id': 'old', 'name': 'Node B', 'rss_bytes': 600, 'memory_received_at': 70},
                           'legacy': {'id': 'legacy', 'name': 'Older node'}}
        nodes = memory.overview()['nodes']
        self.assertEqual([row['stale'] for row in nodes], [False, True, True])
        self.assertIsNone(nodes[-1]['rss_bytes'])
        self.core.server = None
        self.assertTrue(all(row['stale'] for row in memory.overview()['nodes']))
