import os
import time
import unittest
from pathlib import Path
from unittest import mock
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

    def test_log_from_before_a_restart_is_lost_not_running(self):
        # 11/16: a PC restart killed replay_v1 mid-run and status said 'running' for half an hour.
        self.assertEqual(dev.job_state('started\n....', mtime=100.0, boot=200.0), ('lost', None))
        self.assertEqual(dev.job_state('started\n....', mtime=300.0, boot=200.0), ('running', None))
        self.assertEqual(dev.job_state('ok\ndev.py exit 0\n', mtime=100.0, boot=200.0), ('done', 0))
        self.assertEqual(dev.job_state('started\n....', mtime=100.0, boot=None), ('running', None))
        self.log('d', 'started\n...')
        old = time.time() - 3600
        os.utime(dev.log_path('d'), (old, old))
        with mock.patch.object(dev, 'boot_time', return_value=time.time() - 60):
            self.assertEqual(dev.wait('d', 1, tick=.01), 1)
        boot = dev.boot_time()
        self.assertTrue(boot is None or boot < time.time())

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
        pid = dev.start_detached([dev.PY, '-c', "print('hello from the background')"], dev.log_path('e2e'))
        self.assertIsInstance(pid, int)
        # The exit marker precedes interpreter/shell teardown. Wait on the actual process before deleting its log.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if handle:
            self.addCleanup(kernel.CloseHandle, handle)
            self.addCleanup(lambda: self.assertEqual(kernel.WaitForSingleObject(handle, 10000), 0,
                                                     'Background shell did not release its log before cleanup'))
        else:
            self.assertEqual(ctypes.get_last_error(), 87, 'Could not open the launched process for cleanup')
        self.assertEqual(dev.wait('e2e', 30, tick=.2), 0)
        text = dev.log_path('e2e').read_text(encoding='utf-8')
        self.assertIn('hello from the background', text)
        self.assertTrue(text.rstrip().endswith('dev.py exit 0'))


if __name__ == '__main__':
    unittest.main()
