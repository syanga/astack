import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from test_run_job import SYSTEM_PYTHON, system_python_before_3_10

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/codex-background-jobs/scripts/subagent_wake.py'
FIXTURE = Path(__file__).resolve().parent / 'fixtures/codex-0.163-subagent'
PARENT = '01a15a59-7963-74e3-acd6-de30cbdaf9b7'
CHILD = '01a15a59-8ca4-7c12-9e1d-0c4be6f9a2d1'
OTHER = '01a15a59-aaaa-7c12-9e1d-0c4be6f9a2d1'


def at(clock):
    return '2026-10-20T{}.000Z'.format(clock)


def meta(clock, thread, parent=None, version='0.163.0'):
    return {'timestamp': at(clock), 'type': 'session_meta',
            'payload': {'id': thread, 'parent_thread_id': parent, 'cli_version': version}}


def turn(clock, kind, message=None):
    payload = {'type': kind}
    if message is not None:
        payload['last_agent_message'] = message
    return {'timestamp': at(clock), 'type': 'event_msg', 'payload': payload}


def asked_async(clock):
    return {'timestamp': at(clock), 'type': 'response_item', 'payload': {
        'type': 'function_call', 'name': 'request_user_input_async', 'arguments': '{}'}}


def spawned(clock, thread, path):
    return {'timestamp': at(clock), 'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {
        'type': 'SubAgentActivity', 'kind': 'started', 'agent_thread_id': thread, 'agent_path': path}}}


def final_answer(clock, path):
    return {'timestamp': at(clock), 'type': 'response_item', 'payload': {
        'type': 'agent_message', 'author': path, 'recipient': '/root',
        'content': [{'type': 'input_text', 'text': 'Message Type: FINAL_ANSWER\nSender: {}\n'.format(path)}]}}


class SubagentWakeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'codex-home'
        self.day = self.home / 'sessions/2026/10/20'
        self.day.mkdir(parents=True)
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        codex = bin_dir / 'codex'
        codex.write_text('#!{}\nimport json, os, sys\nwith open(os.environ["QUEUE_RECORD"], "a") as stream:\n'
                         '    stream.write(json.dumps(sys.argv[1:]) + "\\n")\n'.format(sys.executable))
        codex.chmod(0o755)
        self.env = dict(os.environ, CODEX_HOME=str(self.home), QUEUE_RECORD=str(self.root / 'queued.jsonl'),
                        PATH=str(bin_dir) + os.pathsep + os.environ['PATH'])

    def rollout(self, thread, events):
        path = self.day / 'rollout-2026-10-20T10-00-00-{}.jsonl'.format(thread)
        path.write_text(''.join(json.dumps(event) + '\n' for event in events))
        return path

    def coordinator(self, *later, ending=None, version='0.163.0'):
        return self.rollout(PARENT, [
            meta('10:00:00', PARENT, version=version), turn('10:00:00', 'task_started'),
            spawned('10:00:10', CHILD, '/root/audit'), ending or turn('10:00:20', 'task_complete'), *later])

    def worker(self, *ending, parent=PARENT):
        return self.rollout(CHILD, [meta('10:00:10', CHILD, parent), meta('10:00:00', PARENT),
                                    turn('10:00:10', 'task_started'), *ending])

    def watch(self, transcript, python=sys.executable):
        subprocess.run([python, str(SCRIPT), 'watch', str(transcript)], env=self.env, check=True, timeout=30)

    def queued(self):
        record = self.root / 'queued.jsonl'
        return [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []

    def test_wakes_the_idle_coordinator_once_for_an_undelivered_completion(self):
        self.worker(turn('10:05:00', 'task_complete'))
        transcript = self.coordinator()
        self.watch(transcript)
        (command,) = self.queued()
        self.assertEqual(command[:4], ['queue', '--thread', PARENT, '--message'])
        self.assertIn('/root/audit finished', command[4])
        with transcript.open('a') as stream:
            for event in (turn('10:05:07', 'task_started'), turn('10:05:30', 'task_complete')):
                stream.write(json.dumps(event) + '\n')
        self.watch(transcript)
        self.assertEqual(len(self.queued()), 1)
        self.assertEqual(list((self.home / 'tmp/subagent-wake').iterdir()), [])

    def test_malformed_lines_do_not_stop_the_wake(self):
        self.worker(turn('10:05:00', 'task_complete'))
        transcript = self.coordinator()
        with transcript.open('a') as stream:
            stream.write('{"payload": "text"}\n[1, 2]\nnot json\n')
            stream.write(json.dumps({'timestamp': at('10:01:00'), 'type': 'response_item', 'payload': {
                'type': 'agent_message', 'author': ['/root/audit'], 'content': [{'text': None}, 'part']}}) + '\n')
            stream.write(json.dumps({'timestamp': at('10:01:00'), 'type': 'event_msg', 'payload': {
                'type': 'item_completed', 'item': 'SubAgentActivity'}}) + '\n')
        self.watch(transcript)
        self.assertEqual([command[:3] for command in self.queued()], [['queue', '--thread', PARENT]])

    def test_no_wake_when_nothing_is_left_to_deliver(self):
        cases = {
            'answer delivered': ([turn('10:05:00', 'task_complete')], [final_answer('10:04:59', '/root/audit')]),
            'worker aborted': ([turn('10:05:00', 'turn_aborted')], []),
            'coordinator resumed after the completion': (
                [turn('10:05:00', 'task_complete')],
                [turn('10:06:00', 'task_started'), turn('10:07:00', 'task_complete')]),
            'user interrupted the coordinator': (
                [turn('10:05:00', 'task_complete')],
                [turn('10:01:00', 'task_started'), turn('10:02:00', 'turn_aborted')]),
            'subagent belongs to another thread': ([turn('10:05:00', 'task_complete')], []),
        }
        for name, (ending, later) in cases.items():
            with self.subTest(name):
                self.worker(*ending, parent=OTHER if name == 'subagent belongs to another thread' else PARENT)
                self.watch(self.coordinator(*later))
                self.assertEqual(self.queued(), [])

    def test_no_wake_after_a_turn_that_asked_the_user(self):
        cases = {
            'question in the last message': [turn('10:00:20', 'task_complete', 'Which dataset?\nRecommendation: A.')],
            'request_user_input_async': [asked_async('10:00:15'), turn('10:00:20', 'task_complete', 'Asked.')],
        }
        for name, ending in cases.items():
            with self.subTest(name):
                self.worker(turn('10:05:00', 'task_complete'))
                self.watch(self.rollout(PARENT, [
                    meta('10:00:00', PARENT), turn('10:00:00', 'task_started'),
                    spawned('10:00:10', CHILD, '/root/audit'), *ending]))
                self.assertEqual(self.queued(), [])

    def test_wakes_after_the_user_answers_or_a_question_mark_is_in_a_url(self):
        cases = {
            'answered': [turn('10:01:00', 'task_started'), turn('10:02:00', 'task_complete', 'Checking both.')],
            'url': [],
        }
        for name, later in cases.items():
            with self.subTest(name):
                self.worker(turn('10:05:00', 'task_complete'))
                ending = turn('10:00:20', 'task_complete',
                              'Which one?' if name == 'answered' else 'See https://example.com/run?id=4.')
                self.watch(self.coordinator(*later, ending=ending))
                self.assertEqual([command[:3] for command in self.queued()], [['queue', '--thread', PARENT]])
                (self.root / 'queued.jsonl').unlink()

    def test_no_watch_before_codex_0_162(self):
        for version in ('0.161.9', None):
            with self.subTest(version):
                self.worker(turn('10:05:00', 'task_complete'))
                self.watch(self.coordinator(version=version))
                self.assertEqual(self.queued(), [])

    def test_wakes_from_a_real_codex_0_163_rollout(self):
        day = self.home / 'sessions/2026/10/19'
        shutil.copytree(FIXTURE, day)
        (coordinator,) = [path for path in day.iterdir() if '01a15520-91d4' in path.name]
        self.watch(coordinator)
        (command,) = self.queued()
        self.assertEqual(command[:3], ['queue', '--thread', '01a15520-91d4-79d0-bb1c-054925aedd85'])
        self.assertIn('/root/sleep_then_done finished', command[4])

    def test_watcher_exits_when_the_thread_rollout_disappears(self):
        child = self.worker()
        transcript = self.coordinator()
        watcher = subprocess.Popen([sys.executable, str(SCRIPT), 'watch', str(transcript)], env=self.env)
        self.addCleanup(watcher.kill)
        time.sleep(1)
        transcript.unlink()
        with child.open('a') as stream:
            stream.write(json.dumps(turn('10:05:00', 'task_complete')) + '\n')
        self.assertEqual(watcher.wait(timeout=15), 0)
        self.assertEqual(self.queued(), [])

    def test_a_subagent_thread_is_not_woken(self):
        self.worker(turn('10:05:00', 'task_complete'))
        self.coordinator()
        transcript = self.rollout(OTHER, [
            meta('10:00:00', OTHER, PARENT), turn('10:00:00', 'task_started'),
            spawned('10:00:10', CHILD, '/root/audit'), turn('10:00:20', 'task_complete')])
        self.watch(transcript)
        self.assertEqual(self.queued(), [])

    def test_stop_hook_returns_at_once_and_wakes_after_a_running_worker_completes(self):
        child = self.worker()
        transcript = self.coordinator()
        start = time.monotonic()
        subprocess.run([sys.executable, str(SCRIPT)], env=self.env, check=True, timeout=10,
                       input=json.dumps({'hook_event_name': 'Stop', 'session_id': PARENT,
                                         'transcript_path': str(transcript)}).encode())
        self.assertLess(time.monotonic() - start, 5)
        time.sleep(1)
        self.assertEqual(self.queued(), [])
        with child.open('a') as stream:
            stream.write(json.dumps(turn('10:05:00', 'task_complete')) + '\n')
        deadline = time.monotonic() + 20
        while not self.queued() and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertEqual([command[:3] for command in self.queued()], [['queue', '--thread', PARENT]])

    def test_wakes_under_the_system_python_before_3_10(self):
        if not system_python_before_3_10():
            self.skipTest('needs {} older than 3.10'.format(SYSTEM_PYTHON))
        self.worker(turn('10:05:00', 'task_complete'))
        self.watch(self.coordinator(), SYSTEM_PYTHON)
        self.assertEqual([command[:3] for command in self.queued()], [['queue', '--thread', PARENT]])


if __name__ == '__main__':
    unittest.main()
