"""Current resident memory measurements and bounded diagnostic estimates."""

import sys
import unittest
from unittest.mock import patch

from ascendant import memory
from ascendant.web.memory import _object_size, SAMPLE_ROWS


class MemoryMeasurementTests(unittest.TestCase):
    def test_darwin_reports_current_rss_in_bytes(self):
        with patch.object(memory.sys, 'platform', 'darwin'), patch.object(memory, '_darwin_rss', return_value=123456) as read:
            result = memory.process_memory()
        self.assertEqual(result['rss_bytes'], 123456)
        self.assertGreater(result['pid'], 0)
        self.assertIn('+00:00', result['sampled_at'])
        read.assert_called_once()

    def test_linux_uses_resident_pages_not_virtual_pages(self):
        with patch.object(memory.sys, 'platform', 'linux'), patch.object(memory.Path, 'read_text', return_value='9000 3 2 1 0'), patch.object(memory.os, 'sysconf', return_value=4096):
            self.assertEqual(memory.process_memory()['rss_bytes'], 3 * 4096)

    def test_missing_or_unsupported_measurement_is_not_reported_as_zero(self):
        with patch.object(memory.sys, 'platform', 'darwin'), patch.object(memory, '_darwin_rss', side_effect=OSError('unavailable')):
            self.assertIsNone(memory.process_memory()['rss_bytes'])
        with patch.object(memory.sys, 'platform', 'unknown'):
            self.assertIsNone(memory.process_memory()['rss_bytes'])
        with patch.object(memory.sys, 'platform', 'linux'), patch.object(memory.Path, 'read_text', return_value='bad'):
            self.assertIsNone(memory.process_memory()['rss_bytes'])

    @unittest.skipUnless(sys.platform == 'darwin' or sys.platform.startswith('linux'), 'Supported resident memory API')
    def test_actual_process_measurement_is_positive(self):
        self.assertGreater(memory.process_memory()['rss_bytes'], 0)

    def test_estimates_handle_shared_objects_and_cycles(self):
        child = {'text': 'some text'}
        single, duplicate = [child], [child, child]
        self.assertEqual(_object_size(duplicate, set()) - _object_size(single, set()), sys.getsizeof(duplicate) - sys.getsizeof(single))
        cycle = []
        cycle.append(cycle)
        self.assertEqual(_object_size(cycle, set()), sys.getsizeof(cycle))

    def test_large_dataset_estimation_visits_only_a_bounded_sample(self):
        visited = []
        class Row(dict):
            def items(self):
                visited.append(self['id'])
                return super().items()
        rows = [Row(id=index, title=f'Video {index}') for index in range(10000)]
        estimate = _object_size(rows, set())
        self.assertGreater(estimate, sys.getsizeof(rows))
        self.assertEqual(len(visited), SAMPLE_ROWS)
        self.assertEqual((visited[0], visited[-1]), (0, 9999))
