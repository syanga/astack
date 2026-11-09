import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
COMMAND = json.loads((REPO / 'settings/claude.json').read_text())['hooks']['Stop']['$entries'][0]['hooks'][0]['command']
HEADER = 'id\ttrack\tstate\tbranch\tpr\tsha\tbrief\tagent\n'
FULL_SEND = ('<command-message>full-send</command-message>\n<command-name>/full-send</command-name>\n'
             '<command-args>the plan</command-args>')
OPEN_GATE = '# Gates\n\n## G1\n\n- Status: open\n- Question: Ship?\n'


class FullSendGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'home'
        shutil.copytree(REPO / 'skills/claude-full-send-guard', self.home / '.claude/skills/claude-full-send-guard',
                        ignore=shutil.ignore_patterns('__pycache__'))
        self.env = {key: value for key, value in os.environ.items() if key != 'CLAUDE_CONFIG_DIR'}
        self.env.update(HOME=str(self.home))
        self.store = self.make_store('program', ('api', 'done'), ('ui', 'pending'))
        self.entries = []
        self.add('user', FULL_SEND)
        self.say('Store: `{}/assumed-decisions.md`'.format(self.store))

    def make_store(self, name, *units):
        store = self.root / 'project/.scratch' / name
        store.mkdir(parents=True)
        self.units(store, *units)
        return store

    def units(self, store, *rows):
        (store / 'units.tsv').write_text(HEADER + ''.join('{}\tmain\t{}\t\t\t\t\t\n'.format(*row) for row in rows))

    def add(self, kind, content, parent='last', **extra):
        uuid = 'u{}'.format(len(self.entries))
        if parent == 'last':
            parent = self.entries[-1]['uuid'] if self.entries else None
        self.entries.append({'type': kind, 'uuid': uuid, 'parentUuid': parent, 'isSidechain': False,
                             'message': {'role': kind, 'content': content}, **extra})
        return uuid

    def say(self, text, parent='last'):
        return self.add('assistant', [{'type': 'text', 'text': text}], parent=parent)

    def stop(self, **event):
        path = self.root / 'session.jsonl'
        path.write_text(''.join(json.dumps(entry) + '\n' for entry in self.entries))
        payload = {'session_id': 's', 'transcript_path': str(path), 'hook_event_name': 'Stop',
                   'stop_hook_active': False, 'background_tasks': [], 'session_crons': [], **event}
        result = subprocess.run(['sh', '-c', COMMAND], input=json.dumps(payload), capture_output=True, text=True,
                                env=self.env, timeout=30)
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        if not result.stdout:
            return None
        output = json.loads(result.stdout)
        self.assertEqual(output['decision'], 'block')
        return output['reason']

    def test_blocks_each_idle_turn_end_once(self):
        self.units(self.store, ('api', 'done'), ('ui', 'pending'), ('db', 'merge-ready'))
        (self.store / 'gates.md').write_text(OPEN_GATE + '\n## G2\n\n- Status: resolved\n- Question: Name?\n'
                                                         '- Answer: ui\n')
        reason = self.stop()
        self.assertIn(str(self.store), reason)
        self.assertIn('ui (pending), db (merge-ready)', reason)
        self.assertIn('G1', reason)
        self.assertNotIn('G2', reason)
        self.assertIsNone(self.stop(stop_hook_active=True))
        self.add('user', 'status?')
        self.say('Nothing is running.')
        self.assertIn('ui (pending), db (merge-ready)', self.stop())

    def test_work_that_will_wake_the_session_lets_the_turn_end(self):
        task = {'id': 'b1', 'type': 'shell', 'status': 'running', 'description': 'tests'}
        cron = {'id': 'c1', 'schedule': '*/5 * * * *', 'recurring': True, 'prompt': 'check'}
        self.assertIsNone(self.stop(background_tasks=[task]))
        self.assertIsNone(self.stop(session_crons=[cron]))
        self.units(self.store, ('api', 'running'), ('ui', 'pending'))
        self.assertIsNone(self.stop())
        self.units(self.store, ('api', 'done'), ('ui', 'needs-verify'))
        self.assertIn('ui (needs-verify)', self.stop())

    def test_blocked_units_wait_only_while_a_gate_is_open(self):
        self.units(self.store, ('api', 'done'), ('ui', 'abandoned'), ('db', 'blocked'), ('ops', 'zombie-reconciled'))
        (self.store / 'gates.md').write_text(OPEN_GATE)
        self.assertIsNone(self.stop())
        (self.store / 'gates.md').write_text(OPEN_GATE.replace('open', 'resolved') + '- Answer: yes\n')
        self.assertIn('db (blocked)', self.stop())

    def test_a_dispatch_pause_lets_the_turn_end_until_it_is_released(self):
        orders = '1. Use the plan.\n2. Dispatch paused: the user is away. Release when they return.\n'
        (self.store / 'preferences.md').write_text(orders)
        self.assertIsNone(self.stop())
        (self.store / 'preferences.md').write_text(orders + '3. Dispatch resumed: the user returned.\n')
        self.assertIn('ui (pending)', self.stop())

    def test_only_a_full_send_command_on_the_active_history_counts(self):
        self.entries[0]['message']['content'] = 'Why did ' + FULL_SEND + ' stop?'
        self.assertIsNone(self.stop())
        self.entries[0]['message']['content'] = [{'type': 'tool_result', 'tool_use_id': 't1', 'content': FULL_SEND}]
        self.assertIsNone(self.stop())
        self.entries[0]['message']['content'] = FULL_SEND
        self.entries[0]['isSidechain'] = True
        self.assertIsNone(self.stop())
        self.entries[0]['isSidechain'] = False
        self.entries.insert(0, ['uuid', FULL_SEND])
        self.assertIn('ui (pending)', self.stop())
        del self.entries[0]
        start = self.say('Ready.', parent=None)
        self.entries[0]['parentUuid'] = start
        self.add('user', 'Never mind; write the docs instead.', parent=start)
        self.assertIsNone(self.stop())

    def test_history_a_broken_resume_dropped_still_counts(self):
        self.add('user', 'Continue.', parent='never-written')
        self.say('Nothing is running.')
        self.assertIn('ui (pending)', self.stop())

    def test_file_snapshots_are_not_part_of_the_history(self):
        snapshot = {'type': 'system', 'subtype': 'file_snapshot', 'parentUuid': None, 'isSidechain': False}
        self.entries.append(dict(snapshot, uuid='before-break'))
        self.add('user', 'Continue.', parent='never-written')
        self.say('Nothing is running.')
        self.entries.append(dict(snapshot, uuid='newest'))
        self.assertIn('ui (pending)', self.stop())

    def test_the_store_is_the_first_one_the_coordinator_names_after_the_command(self):
        earlier = self.make_store('earlier', ('old', 'pending'))
        spaced = self.make_store('my program', ('new', 'pending'))
        named = self.entries[1]['message']['content'][0]
        named['text'] = 'Store: `{}/assumed-decisions.md`. Earlier: {}/assumed-decisions.md'.format(spaced, earlier)
        self.entries.insert(0, {'type': 'assistant', 'uuid': 'before', 'parentUuid': None, 'isSidechain': False,
                                'message': {'role': 'assistant', 'content': [
                                    {'type': 'text', 'text': '{}/assumed-decisions.md'.format(earlier)}]}})
        self.entries[1]['parentUuid'] = 'before'
        self.add('user', [{'type': 'tool_result', 'tool_use_id': 't1',
                           'content': '{}/assumed-decisions.md'.format(self.store)}])
        self.say('Later I may also read {}/assumed-decisions.md.'.format(self.store))
        self.assertIn('new (pending)', self.stop())
        named['text'] = 'Store: {}/assumed-decisions.md. Earlier: `{}/assumed-decisions.md`'.format(self.store, earlier)
        self.assertIn('ui (pending)', self.stop())
        named['text'] = 'Store: {}/assumed-decisions.md.'.format(self.root / 'project/.scratch/single')
        self.assertIsNone(self.stop())


if __name__ == '__main__':
    unittest.main()
