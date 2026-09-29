import argparse
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / 'skills/run-job/scripts/run_job.py'
WRITE_PID = '''
def write_pid(path, pid):
    with open(path + '.tmp', 'w') as file:
        file.write(str(pid))
    os.replace(path + '.tmp', path)
'''
GRANDCHILD = 'import os, subprocess, sys\n' + WRITE_PID + '''
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
write_pid(sys.argv[1], child.pid)
child.wait()
'''
LEFTOVER = 'import os, subprocess, sys\n' + WRITE_PID + '''
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
write_pid(sys.argv[1], child.pid)
'''
ZOMBIE_MEMBER = 'import os, subprocess, sys, time\n' + WRITE_PID + '''
group = os.getpgid(0)
holder = os.fork()
if holder == 0:
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    os.dup2(null, 2)
    os.setpgid(0, 0)
    member = os.fork()
    if member == 0:
        os.setpgid(0, group)
        os._exit(0)
    while not subprocess.run(['ps', '-o', 'stat=', '-p', str(member)],
                             capture_output=True, text=True).stdout.strip().startswith('Z'):
        time.sleep(0.01)
    write_pid(sys.argv[1], os.getpid())
    time.sleep(60)
    os._exit(0)
while not os.path.exists(sys.argv[1]):
    time.sleep(0.01)
'''


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def kill(pid):
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


def load():
    spec = importlib.util.spec_from_file_location('run_job', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RunJobTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.pidfile = self.directory / 'job.pid'

    def run_helper(self, *argv, timeout=30):
        return subprocess.run([sys.executable, str(SCRIPT), *argv],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)

    def python(self, code, *extra):
        return ['--', sys.executable, '-c', code, *extra]

    def job_pid(self):
        deadline = time.monotonic() + 10
        while not self.pidfile.exists():
            self.assertLess(time.monotonic(), deadline, 'job did not write its pid')
            time.sleep(0.02)
        pid = int(self.pidfile.read_text())
        self.addCleanup(kill, pid)
        return pid

    def assert_stops(self, pid):
        deadline = time.monotonic() + 5
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(alive(pid), 'process {} is still running'.format(pid))

    def test_passes_through_output_and_exit_code(self):
        completed = self.run_helper('--timeout', '10', *self.python('print("done"); raise SystemExit(3)'))
        self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (3, 'done\n', ''))

    def test_long_lived_mode_runs_until_the_command_exits(self):
        completed = self.run_helper('--no-timeout', *self.python('import time; time.sleep(0.5); raise SystemExit(5)'))
        self.assertEqual((completed.returncode, completed.stderr), (5, ''))

    def test_reports_a_command_killed_by_a_signal_as_128_plus_the_signal(self):
        completed = self.run_helper('--timeout', '10',
                                    *self.python('import os, signal; os.kill(os.getpid(), signal.SIGTERM)'))
        self.assertEqual(completed.returncode, 128 + signal.SIGTERM)

    def test_closes_stdin_so_prompts_fail(self):
        read, write = os.pipe()
        self.addCleanup(os.close, read)
        self.addCleanup(os.close, write)
        completed = subprocess.run([sys.executable, str(SCRIPT), '--timeout', '10', *self.python('input()')],
                                   stdin=read, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 1)
        self.assertIn('EOFError', completed.stderr)

    def test_deadline_stops_the_whole_process_group(self):
        started = time.monotonic()
        completed = self.run_helper('--timeout', '1', *self.python(GRANDCHILD, str(self.pidfile)))
        self.assertEqual(completed.returncode, 124)
        self.assertEqual(completed.stderr, 'run-job: deadline 1s reached; stopped the process group\n')
        self.assertLess(time.monotonic() - started, 10)
        self.assert_stops(self.job_pid())

    def test_deadline_kills_a_job_that_ignores_termination(self):
        ignoring = 'import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN)\n' + GRANDCHILD
        completed = self.run_helper('--timeout', '1', *self.python(ignoring, str(self.pidfile)))
        self.assertEqual(completed.returncode, 124)
        self.assert_stops(self.job_pid())

    def test_stops_leftover_processes_after_the_command_exits(self):
        completed = self.run_helper('--timeout', '30', *self.python(LEFTOVER, str(self.pidfile)))
        self.assertEqual((completed.returncode, completed.stderr), (0, 'run-job: stopped leftover processes\n'))
        self.assert_stops(self.job_pid())

    def test_treats_a_group_of_zombies_as_stopped(self):
        completed = self.run_helper('--timeout', '30', *self.python(ZOMBIE_MEMBER, str(self.pidfile)))
        self.job_pid()
        self.assertEqual((completed.returncode, completed.stderr), (0, ''))

    def test_signals_stop_the_whole_process_group(self):
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signum.name):
                self.pidfile.unlink(missing_ok=True)
                runner = subprocess.Popen([sys.executable, str(SCRIPT), '--timeout', '30',
                                           *self.python(GRANDCHILD, str(self.pidfile))],
                                          stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.addCleanup(runner.wait)
                self.addCleanup(kill, runner.pid)
                pid = self.job_pid()
                runner.send_signal(signum)
                self.assertEqual(runner.wait(timeout=15), 128 + signum)
                self.assert_stops(pid)

    def test_reports_a_missing_command(self):
        completed = self.run_helper('--timeout', '10', '--', 'astack-missing-command')
        self.assertEqual(completed.returncode, 127)
        self.assertIn('run-job: cannot start astack-missing-command', completed.stderr)

    def test_reports_a_command_that_is_not_executable(self):
        script = self.directory / 'script'
        script.write_text('echo hi\n')
        completed = self.run_helper('--timeout', '10', '--', str(script))
        self.assertEqual(completed.returncode, 126)
        self.assertIn('run-job: cannot start {}'.format(script), completed.stderr)

    def test_rejects_invalid_arguments_before_starting_anything(self):
        marker = self.directory / 'started'
        touch = ['--', 'touch', str(marker)]
        cases = ([], ['--timeout', '5'], ['--no-timeout'], touch,
                 ['--timeout', '0', *touch], ['--timeout', '-1', *touch], ['--timeout', 'inf', *touch],
                 ['--timeout', 'nan', *touch], ['--timeout', 'soon', *touch],
                 ['--timeout', '5', '--no-timeout', *touch])
        for argv in cases:
            with self.subTest(argv=argv):
                completed = self.run_helper(*argv)
                self.assertEqual(completed.returncode, 2)
                self.assertFalse(marker.exists())

    def test_stops_the_group_when_the_start_callback_fails(self):
        run_job = load()
        parser = argparse.ArgumentParser()
        run_job.add_arguments(parser)
        spec = run_job.spec_from(parser.parse_args(['--timeout', '30', '--', 'sleep', '60']))
        started = []

        def fail(process):
            started.append(process.pid)
            raise RuntimeError('could not write the receipt')

        with self.assertRaisesRegex(RuntimeError, 'could not write the receipt'):
            run_job.supervise(spec, None, None, fail)
        self.addCleanup(kill, started[0])
        self.assert_stops(started[0])

    def test_reports_a_group_that_cannot_be_verified_empty(self):
        run_job = load()
        stderr = io.StringIO()
        with mock.patch.object(run_job, 'live_members', return_value=1), contextlib.redirect_stderr(stderr):
            code = run_job.main(['--timeout', '30', *self.python(ZOMBIE_MEMBER, str(self.pidfile))])
        self.job_pid()
        self.assertEqual(code, 125)
        self.assertRegex(stderr.getvalue(), r'run-job: could not stop process group \d+\n$')


if __name__ == '__main__':
    unittest.main()
