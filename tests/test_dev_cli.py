import unittest
from pathlib import Path
import dev


class TestDevCli(unittest.TestCase):
    def test_suite_command_loads_the_test_package(self):
        self.assertEqual(dev.SUITE[-4:], ['-s', 'tests', '-t', '.'])   # -t . imports tests/__init__.py (network guard)

    def test_detached_command_sets_env_and_logs_everything(self):
        line = dev.detached_command(['python', '-m', 'qualify.lab', 'run'], Path('C:/x/data/_logs/lab.log'))
        self.assertIn('set PYTHONPATH=.&& set PYTHONIOENCODING=utf-8&& python -m qualify.lab run', line)
        self.assertTrue(line.endswith('lab.log 2>&1'))

    def test_log_names_are_plain(self):
        with self.assertRaises(SystemExit):
            dev.log_path('../escape')

    def test_summary_reads_unittest_results(self):
        self.assertEqual(dev.summarize('....\nRan 364 tests in 55.3s\n\nOK (skipped=17)\n'), 'OK (skipped=17) (364 tests, 55 s)')
        self.assertEqual(dev.summarize('Ran 3 tests in 0.1s\n\nFAILED (failures=1)\n'), 'FAILED (failures=1) (3 tests, 0 s)')
        self.assertEqual(dev.summarize('collect done\n'), 'collect done')

    def test_forward_record_always_publishes_the_public_ledger(self):
        self.assertEqual(dev.FORWARD['record'][-2:], ['--publish', 'docs/benchmarks/forward-ledger.jsonl'])
        self.assertNotIn('--publish', dev.FORWARD['score'])

    def test_dashboard_restart_waits_for_idle(self):
        idle = {'active_job': None, 'news': {'phase': 'idle'}, 'models': {'phase': 'downloaded'}}
        self.assertFalse(dev.dashboard_busy(idle))
        self.assertFalse(dev.dashboard_busy(None))             # not running: safe to start
        self.assertTrue(dev.dashboard_busy(dict(idle, active_job='run-1')))
        self.assertTrue(dev.dashboard_busy(dict(idle, news={'phase': 'running'})))
        self.assertTrue(dev.dashboard_busy(dict(idle, models={'phase': 'qualifying'})))


if __name__ == '__main__':
    unittest.main()
