import unittest
from unittest.mock import Mock, patch

from benchmark_runner.observe import _parse_mongo_plan_spill, check_container_oom
from benchmark_runner.status import classify_status
from benchmark_runner.metrics import PrometheusCollector


class SpillClassificationTests(unittest.TestCase):
    def test_mongodb_plan_spill_reads_boolean_fields_not_text(self):
        self.assertFalse(_parse_mongo_plan_spill({'stages': [{'$sort': {'usedDisk': False}}]}))
        self.assertTrue(_parse_mongo_plan_spill({'stages': [{'$sort': {'usedDisk': True}}]}))
        self.assertFalse(_parse_mongo_plan_spill({'note': 'usedDisk: true'}))


class StatusClassificationTests(unittest.TestCase):
    def test_incomplete_or_unknown_evidence_is_error(self):
        self.assertEqual(classify_status(False, False, None, True), 'error')
        self.assertEqual(classify_status(False, False, False, False), 'error')
        self.assertEqual(classify_status(None, False, False, True), 'error')

    def test_oom_has_priority_and_spill_requires_measured_evidence(self):
        self.assertEqual(classify_status(True, True, None, False), 'oom')
        self.assertEqual(classify_status(False, False, True, True), 'spill')
        self.assertEqual(classify_status(False, False, False, True), 'ok')


class OOMWindowTests(unittest.TestCase):
    def test_oom_is_scoped_to_docker_events_not_sticky_state(self):
        responses = [Mock(stdout='container-id\n'), Mock(stdout='{}\n')]
        with patch('benchmark_runner.observe.subprocess.run', side_effect=responses) as run:
            self.assertTrue(check_container_oom('benchmark_postgresql', 100, 200))
        event_call = run.call_args_list[1].args[0]
        self.assertIn('--since', event_call)
        self.assertIn('100', event_call)
        self.assertIn('--until', event_call)
        self.assertIn('200', event_call)

    def test_event_query_failure_is_unknown(self):
        with patch('benchmark_runner.observe.subprocess.run', side_effect=RuntimeError('docker unavailable')):
            self.assertIsNone(check_container_oom('benchmark_postgresql', 100, 200))


class IntervalTelemetryTests(unittest.TestCase):
    def test_missing_cfs_endpoints_do_not_fall_back_to_cumulative_value(self):
        collector = PrometheusCollector()
        with patch.object(collector, '_get_container_id', return_value='cid'):
            with patch.object(collector, 'query_instant', side_effect=[None, 4.0]) as query:
                with self.assertRaises(RuntimeError):
                    collector.get_container_throttled_seconds('db', 100, 200)
                self.assertEqual(query.call_count, 2)

    def test_sparse_rss_window_does_not_fall_back_to_point_sample(self):
        collector = PrometheusCollector()
        with patch.object(collector, '_get_container_id', return_value='cid'):
            with patch.object(collector, 'query_range_values', return_value=[10.0]):
                with patch.object(collector, 'query_instant') as instant:
                    with self.assertRaises(RuntimeError):
                        collector.get_container_max_rss_mb('db', 100, 200)
                    instant.assert_not_called()


if __name__ == '__main__':
    unittest.main()
