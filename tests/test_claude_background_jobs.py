import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'skills/claude-background-jobs/scripts/run.py'
SPAWN = '''import subprocess, sys
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
open(sys.argv[1], 'w').write(str(child.pid))
child.wait()
'''


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class ClaudeBackgroundJobsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.pidfile = Path(self.temporary.name) / 'child.pid'

    def argv(self, code, timeout='5', *extra):
        return [sys.executable, str(SCRIPT), '--timeout', timeout, '--', sys.executable, '-c', code, *extra]

    def assert_stops(self, pid):
        deadline = time.monotonic() + 5
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(alive(pid))

    def test_passes_through_output_and_exit_code(self):
        completed = subprocess.run(self.argv('print("done"); raise SystemExit(3)'),
                                   capture_output=True, text=True, timeout=15)
        self.assertEqual((completed.returncode, completed.stdout), (3, 'done\n'))

    def test_closes_stdin_so_prompts_fail(self):
        completed = subprocess.run(self.argv('input()'), stdin=subprocess.PIPE,
                                   capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 1)
        self.assertIn('EOFError', completed.stderr)

    def test_deadline_stops_the_whole_job(self):
        started = time.monotonic()
        completed = subprocess.run(self.argv(SPAWN, '1', str(self.pidfile)),
                                   capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 124)
        self.assertIn('timed out after 1s', completed.stderr)
        self.assertLess(time.monotonic() - started, 10)
        self.assert_stops(int(self.pidfile.read_text()))

    def test_termination_stops_the_whole_job(self):
        runner = subprocess.Popen(self.argv(SPAWN, '30', str(self.pidfile)))
        self.addCleanup(runner.kill)
        deadline = time.monotonic() + 5
        while not self.pidfile.exists() or not self.pidfile.read_text():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)
        runner.send_signal(signal.SIGTERM)
        self.assertEqual(runner.wait(timeout=15), 128 + signal.SIGTERM)
        self.assert_stops(int(self.pidfile.read_text()))

    def test_rejects_invalid_arguments(self):
        for argv in ([], ['--timeout', '0', '--', 'true'], ['--timeout', 'nan', '--', 'true'], ['--timeout', '5']):
            with self.subTest(argv=argv):
                completed = subprocess.run([sys.executable, str(SCRIPT), *argv],
                                           capture_output=True, text=True, timeout=15)
                self.assertEqual(completed.returncode, 2)


if __name__ == '__main__':
    unittest.main()
