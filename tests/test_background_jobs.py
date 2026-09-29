import json
from datetime import datetime
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'skills/background-jobs/scripts/run.py'
THREAD = '01234567-89ab-4def-8123-456789abcdef'


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


class PlainJobTests(unittest.TestCase):
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
        read, write = os.pipe()
        self.addCleanup(os.close, read)
        self.addCleanup(os.close, write)
        completed = subprocess.run(self.argv('input()'), stdin=read,
                                   capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 1)
        self.assertIn('EOFError', completed.stderr)

    def test_deadline_stops_the_whole_job(self):
        started = time.monotonic()
        completed = subprocess.run(self.argv(SPAWN, '3', str(self.pidfile)),
                                   capture_output=True, text=True, timeout=20)
        self.assertEqual(completed.returncode, 124)
        self.assertIn('timed out after 3s', completed.stderr)
        self.assertLess(time.monotonic() - started, 15)
        self.assert_stops(int(self.pidfile.read_text()))

    def test_termination_stops_the_whole_job(self):
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signum.name):
                self.pidfile.unlink(missing_ok=True)
                runner = subprocess.Popen(self.argv(SPAWN, '30', str(self.pidfile)))
                self.addCleanup(runner.kill)
                deadline = time.monotonic() + 5
                while not self.pidfile.exists() or not self.pidfile.read_text():
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.05)
                runner.send_signal(signum)
                self.assertEqual(runner.wait(timeout=15), 128 + signum)
                self.assert_stops(int(self.pidfile.read_text()))

    def test_rejects_invalid_arguments(self):
        for argv in ([], ['--timeout', '0', '--', 'true'], ['--timeout', 'nan', '--', 'true'], ['--timeout', '5']):
            with self.subTest(argv=argv):
                completed = subprocess.run([sys.executable, str(SCRIPT), *argv],
                                           capture_output=True, text=True, timeout=15)
                self.assertEqual(completed.returncode, 2)


class CodexQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        codex = self.bin / 'codex'
        codex.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
from pathlib import Path
if sys.argv[1:] == ['queue', '--help']:
    print('Usage: codex queue --thread THREAD --message MESSAGE')
    sys.exit(int(os.environ.get('PROBE_EXIT', '0')))
with Path(os.environ['QUEUE_RECORD']).open('a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if os.environ.get('CANCEL_QUEUE'):
    import signal
    os.kill(os.getppid(), signal.SIGTERM)
    signal.pause()
if os.environ.get('INVALID_QUEUE_OUTPUT'):
    os.write(1, b'accepted \\xff\\n')
    os.write(2, b'diagnostic \\xfe\\n')
    sys.exit(0)
if os.environ.get('HANG_QUEUE'):
    import signal
    print('accepted', flush=True)
    signal.pause()
print('accepted' if os.environ.get('QUEUE_EXIT', '0') == '0' else 'queue unavailable')
sys.exit(int(os.environ.get('QUEUE_EXIT', '0')))
''', encoding='utf-8')
        codex.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        CODEX_THREAD_ID=THREAD, TMPDIR=str(self.root),
                        QUEUE_RECORD=str(self.root / 'queued.jsonl'))

    def argv(self, code, timeout='5', *extra):
        return [sys.executable, str(SCRIPT), '--codex', '--label', 'fixture', '--timeout', timeout,
                '--', sys.executable, '-c', code, *extra]

    def run_job(self, code, timeout='5', *extra):
        completed = subprocess.run(self.argv(code, timeout, *extra), cwd=self.root,
                                   env=self.env, capture_output=True, text=True, timeout=15)
        receipts = [json.loads(line) for line in completed.stdout.splitlines()]
        result = json.loads(Path(receipts[-1]['result']).read_text()) if receipts else None
        return completed, receipts, result

    def queued(self):
        path = self.root / 'queued.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_success_and_failure_preserve_output_and_queue_once_to_current_thread(self):
        for code in [0, 7]:
            with self.subTest(code=code):
                completed, receipts, result = self.run_job(
                    f'import sys; print("JOB_OUTPUT_91"); print("error output", file=sys.stderr); sys.exit({code})')
                self.assertEqual(completed.returncode, code, completed.stderr)
                self.assertEqual(receipts[0]['status'], 'running')
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(result['exit_code'], code)
                self.assertEqual(result['notification'], 'queued')
                times = [datetime.fromisoformat(result[key]) for key in
                         ('started_at', 'finished_at', 'notification_started_at',
                          'notification_finished_at')]
                self.assertTrue(all(value.utcoffset().total_seconds() == 0 for value in times))
                self.assertEqual(times, sorted(times))
                self.assertCountEqual(Path(result['log']).read_text().splitlines(),
                                      ['JOB_OUTPUT_91', 'error output'])
                queued = self.queued()
                self.assertEqual(queued[-1][:4], ['queue', '--thread', THREAD, '--message'])
                self.assertIn(f'exit code: {code}', queued[-1][4])
                self.assertIn(result['result'], queued[-1][4])
                self.assertNotIn('JOB_OUTPUT_91', queued[-1][4])
        self.assertEqual(len(self.queued()), 2)

    def test_arguments_are_literal_and_stdin_is_closed(self):
        completed, _, result = self.run_job(
            'import sys; print(repr(sys.argv[1])); print(repr(sys.stdin.read()))',
            '5', '$(touch unwanted); `false`')
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(Path(result['log']).read_text(), "'$(touch unwanted); `false`'\n''\n")
        self.assertFalse((self.root / 'unwanted').exists())

    def test_preflight_refuses_to_run_without_supported_queue_or_thread(self):
        for setting, value in [('CODEX_THREAD_ID', ''), ('PROBE_EXIT', '2')]:
            with self.subTest(setting=setting):
                original = self.env.copy()
                self.env[setting] = value
                completed, receipts, result = self.run_job('from pathlib import Path; Path("ran").touch()')
                self.assertEqual(completed.returncode, 2)
                self.assertIn('CODEX_THREAD_ID' if setting == 'CODEX_THREAD_ID' else 'queue is unavailable',
                              completed.stderr)
                self.assertEqual(receipts, [])
                self.assertIsNone(result)
                self.assertFalse((self.root / 'ran').exists())
                self.assertEqual(self.queued(), [])
                self.env = original

    def test_queue_failure_retains_job_result_without_repeating_the_job(self):
        self.env['QUEUE_EXIT'] = '9'
        completed, _, result = self.run_job('print("finished once")')
        self.assertEqual(completed.returncode, 75)
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['notification'], 'failed')
        self.assertEqual(result['queue_exit_code'], 9)
        self.assertEqual(Path(result['log']).read_text(), 'finished once\n')
        self.assertEqual(len(self.queued()), 1)

    def test_result_write_failure_still_delivers_completed_job_status(self):
        release = self.root / 'release'
        os.mkfifo(release)
        process = subprocess.Popen(self.argv(
            'import sys; open("release").read(); print("finished once"); sys.exit(7)'),
            cwd=self.root, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            receipt = json.loads(process.stdout.readline())
            result_path = Path(receipt['result'])
            result_path.with_suffix('.tmp').mkdir()
            release.write_text('finish')
            output, error = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 74, error)
            final = json.loads(output.splitlines()[-1])
            self.assertEqual(final['status'], 'completed')
            self.assertEqual(final['exit_code'], 7)
            self.assertEqual(final['notification'], 'queued')
            self.assertIn('result_write_error', final)
            self.assertIn('result_write_error', error)
            self.assertEqual(json.loads(result_path.read_text())['status'], 'running')
            self.assertEqual(Path(final['log']).read_text(), 'finished once\n')
            queued = self.queued()
            self.assertEqual(len(queued), 1)
            self.assertIn('Status: completed; exit code: 7', queued[0][4])
            self.assertIn('result.json may be stale', queued[0][4])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_invalid_utf8_queue_output_preserves_acceptance(self):
        self.env['INVALID_QUEUE_OUTPUT'] = '1'
        completed, _, result = self.run_job('print("finished once")')
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(result['notification'], 'queued')
        self.assertEqual(result['queue_output'], 'accepted \ufffd\n')
        self.assertEqual(result['queue_error'], 'diagnostic \ufffd\n')
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(Path(result['log']).read_text(), 'finished once\n')
        self.assertEqual(len(self.queued()), 1)

    def test_closed_receipt_pipe_does_not_stop_job_or_delivery(self):
        process = subprocess.Popen(self.argv('import sys; print("finished once"); sys.exit(7)'),
                                   cwd=self.root, env=self.env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        process.stdout.close()
        _, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 7, error)
        result_path, = self.root.glob('astack-codex-job-*/result.json')
        result = json.loads(result_path.read_text())
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['exit_code'], 7)
        self.assertEqual(result['notification'], 'queued')
        self.assertEqual(Path(result['log']).read_text(), 'finished once\n')
        self.assertEqual(len(self.queued()), 1)

    def test_queue_timeout_records_uncertainty_without_repeating_job(self):
        self.env['HANG_QUEUE'] = '1'
        completed = subprocess.run(self.argv(
            'open("runs", "a").write("run\\n"); print("finished once")'),
            cwd=self.root, env=self.env, capture_output=True, text=True, timeout=40)
        result = json.loads(completed.stdout.splitlines()[-1])
        self.assertEqual(completed.returncode, 75, completed.stderr)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['notification'], 'unknown')
        self.assertIn('timed out', result['queue_error'])
        self.assertEqual(Path(result['log']).read_text(), 'finished once\n')
        self.assertEqual((self.root / 'runs').read_text(), 'run\n')
        self.assertEqual(len(self.queued()), 1)

    def test_timeout_stops_job_and_queues_failure(self):
        completed, _, result = self.run_job('import signal; signal.pause()', '0.2')
        self.assertEqual(completed.returncode, 124, completed.stderr)
        self.assertEqual(result['status'], 'timed_out')
        self.assertEqual(result['notification'], 'queued')
        self.assertIn('exit code: 124', self.queued()[0][4])
        with self.assertRaises(ProcessLookupError):
            os.kill(result['command_pid'], 0)

    def test_interrupt_stops_job_without_waking_cancelled_work(self):
        process = subprocess.Popen(self.argv('import signal; signal.pause()'), cwd=self.root,
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            receipt = json.loads(process.stdout.readline())
            process.send_signal(signal.SIGTERM)
            _, error = process.communicate(timeout=10)
            result = json.loads(Path(receipt['result']).read_text())
            self.assertEqual(process.returncode, 143, error)
            self.assertEqual(result['status'], 'cancelled')
            self.assertEqual(result['notification'], 'skipped')
            self.assertEqual(self.queued(), [])
            with self.assertRaises(ProcessLookupError):
                os.kill(result['command_pid'], 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_interrupt_during_delivery_preserves_result_and_records_uncertainty(self):
        self.env['CANCEL_QUEUE'] = '1'
        completed, _, result = self.run_job('print("finished before cancellation")')
        self.assertEqual(completed.returncode, 143, completed.stderr)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['notification'], 'unknown')
        self.assertEqual(Path(result['log']).read_text(), 'finished before cancellation\n')
        self.assertEqual(len(self.queued()), 1)

    def test_launch_failure_is_reported_to_thread(self):
        command = self.argv('unused')
        command[-3:] = [str(self.root / 'missing-executable')]
        completed = subprocess.run(command, cwd=self.root, env=self.env, capture_output=True,
                                   text=True, timeout=10)
        result = json.loads(completed.stdout.splitlines()[-1])
        self.assertEqual(completed.returncode, 127, completed.stderr)
        self.assertEqual(result['status'], 'failed_to_start')
        self.assertEqual(result['notification'], 'queued')
        self.assertIn('exit code: 127', self.queued()[0][4])


if __name__ == '__main__':
    unittest.main()
