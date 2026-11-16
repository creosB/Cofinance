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


class TestBackgroundJobs(unittest.TestCase):
    def setUp(self):
        import tempfile
        from unittest import mock
        self.tmp = tempfile.TemporaryDirectory()
        patch = mock.patch.object(dev, 'LOGS', Path(self.tmp.name))
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def log(self, name, text):
        dev.log_path(name).write_text(text, encoding='utf-8')

    def test_exit_marker_decides_state(self):
        self.assertEqual(dev.job_state('started\n....'), ('running', None))
        self.assertEqual(dev.job_state('Ran 3 tests in 1s\n\nOK\ndev.py exit 0\n'), ('done', 0))
        self.assertEqual(dev.job_state('boom\ndev.py exit 1\n'), ('failed', 1))
        self.assertEqual(dev.summarize('Ran 3 tests in 1s\n\nOK\ndev.py exit 0\n'), 'OK (3 tests, 1 s)')

    def test_wait_answers_once_with_a_plain_code(self):
        self.log('a', 'Ran 2 tests in 1s\n\nOK\ndev.py exit 0\n')
        self.log('b', 'Traceback\ndev.py exit 3\n')
        self.log('c', 'started\n...')
        self.assertEqual(dev.wait('a', 1, tick=.01), 0)
        self.assertEqual(dev.wait('b', 1, tick=.01), 1)
        self.assertEqual(dev.wait('c', .05, tick=.01), 2)

    def test_wait_cap_stays_under_the_bridge_limit(self):
        import time
        self.log('slow', 'started\n')
        started = time.monotonic()
        with unittest.mock.patch.object(dev, 'WAIT_MAX', .1):
            self.assertEqual(dev.wait('slow', 9999, tick=.01), 2)
        self.assertLess(time.monotonic() - started, 1)
        self.assertLess(dev.WAIT_MAX, 300)

    def test_child_ends_the_log_with_the_exit_code(self):
        import contextlib
        import io
        import sys
        for code in (0, 4):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(dev.child([sys.executable, '-c', f'raise SystemExit({code})']), code)
            self.assertEqual(out.getvalue().strip().splitlines()[-1], f'dev.py exit {code}')

    def test_detached_job_end_to_end(self):
        import os
        if os.name != 'nt':
            self.skipTest('Windows launch path')
        dev.start_detached([dev.PY, '-c', "print('hello from the background')"], dev.log_path('e2e'))
        self.assertEqual(dev.wait('e2e', 30, tick=.2), 0)
        text = dev.log_path('e2e').read_text(encoding='utf-8')
        self.assertIn('hello from the background', text)
        self.assertTrue(text.rstrip().endswith('dev.py exit 0'))


if __name__ == '__main__':
    unittest.main()
