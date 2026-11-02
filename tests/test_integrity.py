import unittest
from unittest.mock import Mock, patch

from benchmark_runner.integrity import cell_key, completed_in_window, expected_keys
from benchmark_runner.metrics import calculate_latencies
from benchmark_runner.status import classify_status


class IntegrityTests(unittest.TestCase):
    def test_matrix_has_162_unique_cell_keys(self):
        keys = list(expected_keys())
        self.assertEqual(len(keys), 162)
        self.assertEqual(len(set(keys)), 162)

    def test_concurrency_is_part_of_cell_identity(self):
        self.assertEqual(len({cell_key('mongodb', 'PROFILE_A_BASELINE', 'Q1', c, 1) for c in [1, 10, 50]}), 3)

    def test_only_successful_operations_fully_inside_window_are_counted(self):
        records = [
            {'start': 0.1, 'end': 0.8, 'ok': True},
            {'start': 0.1, 'end': 1.1, 'ok': True},
            {'start': 0.1, 'end': 0.8, 'ok': False},
        ]
        self.assertEqual(completed_in_window(records, 0, 1), [records[0]])

    def test_empty_latency_sample_is_unknown(self):
        self.assertEqual(calculate_latencies([]), {'p50': None, 'p95': None, 'p99': None})

    def test_percentiles_are_ordered(self):
        stats = calculate_latencies([3, 1, 2, 4, 5])
        self.assertLessEqual(stats['p50'], stats['p95'])
        self.assertLessEqual(stats['p95'], stats['p99'])

    def test_unknown_spill_or_oom_cannot_be_success(self):
        self.assertEqual(classify_status(None, False, False, True), 'error')
        self.assertEqual(classify_status(False, False, None, True), 'error')


if __name__ == '__main__':
    unittest.main()
