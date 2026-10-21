#!/usr/bin/env python3
"""Codex Stop hook that wakes an idle top-level thread when one of its native subagents finishes.

Codex delivers a subagent's final answer with `trigger_turn: false`, so a thread
that has ended its turn does not run again until something else starts a turn.
The hook starts a detached watcher that runs `codex queue` to request a turn. The
watcher needs `codex` on PATH, skips subagent threads, stops when the user
interrupts the thread, and gives up after MAX_WATCH_SECONDS.
"""
import fcntl
import glob
import json
import os
import shutil
import subprocess
import sys
import time

POLL_SECONDS = 5
MAX_WATCH_SECONDS = 24 * 3600
QUEUE_SECONDS = 60


# Codex 0.163 rollouts are JSONL at $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<time>-<thread>.jsonl.
# A turn runs from a task_started event to task_complete or turn_aborted. The parent records each
# subagent in a SubAgentActivity item and each delivered answer in an agent_message whose author
# is the subagent's agent_path and whose text starts with "Message Type: FINAL_ANSWER". A
# subagent's rollout starts with its own session_meta, then copies the parent's history,
# including the parent's session_meta.
class Rollout:
    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.missing = False
        self.id = None
        self.parent = None
        self.running = False
        self.interrupted = False
        self.started = ''
        self.completed = ''
        self.subagents = {}
        self.answered = {}

    def read(self):
        try:
            with open(self.path, 'rb') as stream:
                stream.seek(self.offset)
                for line in stream:
                    if not line.endswith(b'\n'):
                        break
                    self.offset += len(line)
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and isinstance(event.get('payload'), dict):
                        self.update(event, event['payload'], str(event.get('timestamp') or ''))
        except FileNotFoundError:
            self.missing = True
        except OSError:
            pass

    def update(self, event, payload, timestamp):
        kind = payload.get('type')
        item = payload.get('item')
        if event.get('type') == 'session_meta':
            if self.id is not None:
                return
            self.id = payload.get('id')
            self.parent = payload.get('parent_thread_id')
        elif kind == 'task_started':
            if not self.running:
                self.started = timestamp
            self.running = True
            self.interrupted = False
        elif kind in ('task_complete', 'turn_aborted'):
            self.running = False
            self.interrupted = kind == 'turn_aborted'
            self.completed = '' if self.interrupted else timestamp
        elif isinstance(item, dict) and item.get('type') == 'SubAgentActivity':
            if isinstance(item.get('agent_thread_id'), str) and isinstance(item.get('agent_path'), str):
                self.subagents[item['agent_thread_id']] = item['agent_path']
        elif kind == 'agent_message' and isinstance(payload.get('content'), list):
            text = ''.join(part['text'] for part in payload['content']
                           if isinstance(part, dict) and isinstance(part.get('text'), str))
            if text.startswith('Message Type: FINAL_ANSWER') and isinstance(payload.get('author'), str):
                self.answered[payload.get('author')] = timestamp


class Watch:
    def __init__(self, transcript):
        self.thread = Rollout(transcript)
        self.sessions = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(transcript))))
        self.children = {}

    def poll(self):
        self.thread.read()
        running, finished = [], []
        for child_id, path in sorted(self.thread.subagents.items()):
            if child_id not in self.children:
                found = glob.glob(os.path.join(self.sessions, '*', '*', '*', '*-%s.jsonl' % glob.escape(child_id)))
                if not found:
                    continue
                self.children[child_id] = Rollout(found[0])
            child = self.children[child_id]
            child.read()
            if child.parent != self.thread.id:
                continue
            if child.running:
                running.append(path)
            elif child.completed > self.thread.started and self.thread.answered.get(path, '') < child.started:
                finished.append(path)
        return running, finished


def message(paths):
    return ('Subagent notification: {} finished. Continue the existing task with the result unless the user '
            'paused or redirected it. If the result is not in the conversation, read it with list_agents. '
            'This automatic notification is not a user request.'.format(', '.join(paths)))


def watch(state):
    deadline = time.time() + MAX_WATCH_SECONDS
    while time.time() < deadline:
        running, finished = state.poll()
        if state.thread.missing or state.thread.interrupted or not (running or finished):
            return
        if finished and not state.thread.running:
            codex = shutil.which('codex')
            if codex:
                subprocess.run([codex, 'queue', '--thread', state.thread.id, '--message', message(finished)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=QUEUE_SECONDS)
            return
        time.sleep(POLL_SECONDS)


def watch_once(transcript):
    home = os.environ.get('CODEX_HOME') or os.path.expanduser('~/.codex')
    directory = os.path.join(home, 'tmp', 'subagent-wake')
    os.makedirs(directory, exist_ok=True)
    lock_path = os.path.join(directory, os.path.basename(transcript) + '.lock')
    with open(lock_path, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return
        try:
            state = Watch(transcript)
            state.thread.read()
            if state.thread.id and not state.thread.parent:
                watch(state)
        finally:
            try:
                if os.path.samestat(os.fstat(lock.fileno()), os.stat(lock_path)):
                    os.unlink(lock_path)
            except OSError:
                pass


def main():
    if sys.argv[1:2] == ['watch']:
        watch_once(sys.argv[2])
        return
    transcript = json.load(sys.stdin).get('transcript_path')
    if transcript:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), 'watch', transcript],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)


if __name__ == '__main__':
    main()
