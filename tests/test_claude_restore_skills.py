from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / 'skills/claude-restore-skills/scripts/hook.py'
ENTRY = json.loads((REPO / 'settings/claude.json').read_text())['hooks']['SessionStart']['$entries'][0]
LAUNCHERS = [hook['command'] for hook in ENTRY['hooks']]
HEADER = ('Earlier history of this session is no longer in context. Before it dropped out, the user invoked '
          'these skills, in this order: {}. The text of each follows. Continue each unfinished task from '
          'where its work stands, and skip steps already done.')


class Transcript:
    def __init__(self, home):
        self.home = home
        self.entries = []

    def add(self, kind, content, parent='last', **extra):
        uuid = 'u{}'.format(len(self.entries))
        if parent == 'last':
            parent = self.entries[-1]['uuid'] if self.entries else None
        entry = {'parentUuid': parent, 'isSidechain': False, 'type': kind, 'uuid': uuid, **extra}
        if content is not None:
            entry['message'] = {'role': 'assistant' if kind == 'assistant' else 'user', 'content': content}
        self.entries.append(entry)
        return uuid

    def base(self, name):
        return 'Base directory for this skill: {}/.claude/skills/{}'.format(self.home, name)

    def command(self, name, args=None, recorded='Recorded text.', parent='last'):
        tags = '<command-message>{0}</command-message>\n<command-name>/{0}</command-name>'.format(name)
        if args is not None:
            tags += '\n<command-args>{}</command-args>'.format(args)
        self.add('user', tags, parent=parent)
        body = '{}\n\n# {}\n\n{}'.format(self.base(name), name.title(), recorded)
        self.add('user', [{'type': 'text', 'text': '/{} {}'.format(name, args or '')},
                          {'type': 'text', 'text': body}], isMeta=True)
        self.add('assistant', [{'type': 'text', 'text': 'Working.'}])

    def model_skill(self, name):
        self.add('assistant', [{'type': 'tool_use', 'id': 'tool-' + name, 'name': 'Skill', 'input': {'skill': name}}])
        self.add('user', [{'type': 'tool_result', 'tool_use_id': 'tool-' + name, 'content': 'Launching skill: ' + name}])
        self.add('user', [{'type': 'text', 'text': self.base(name) + '\n\n# ' + name}], isMeta=True,
                 sourceToolUseID='tool-' + name)

    def compact(self, *copies):
        self.add('system', None, parent=None, subtype='compact_boundary', logicalParentUuid=self.entries[-1]['uuid'])
        self.add('user', 'This session is being continued from a previous conversation.', isCompactSummary=True)
        if copies:
            self.add('attachment', None, attachment={'type': 'invoked_skills', 'skills': [
                {'name': name, 'path': 'userSettings:' + name, 'content': '{}\n\n{}'.format(self.base(name), text)}
                for name, text in copies]})

    def write(self, path):
        lines = [json.dumps(entry) for entry in self.entries]
        lines.insert(1, '{"type":"queue{"type":"queue-operation"}')
        lines.insert(2, json.dumps({'type': 'last-prompt', 'lastPrompt': 'x'}))
        path.write_text('\n'.join(lines) + '\n')


class RestoreSkillsHookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'home'
        self.temp = self.root / 'tmp'
        self.temp.mkdir()
        shutil.copytree(REPO / 'skills/claude-restore-skills', self.home / '.claude/skills/claude-restore-skills',
                        ignore=shutil.ignore_patterns('__pycache__'))
        self.env = {key: value for key, value in os.environ.items() if key != 'CLAUDE_CONFIG_DIR'}
        self.env.update(HOME=str(self.home), TMPDIR=str(self.temp))
        self.transcript = Transcript(self.home)

    def skill(self, name, body):
        folder = self.home / '.claude/skills' / name
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text('---\nname: {}\ndescription: test\n---\n\n{}\n'.format(name, body))

    def parts(self, source='resume'):
        """Run every part as Claude Code does, in parallel, and collect their output in exit order."""
        path = self.root / 'session.jsonl'
        self.transcript.write(path)
        event = json.dumps({'session_id': 's', 'transcript_path': str(path), 'hook_event_name': 'SessionStart',
                            'source': source, 'context_tokens': 1234})

        def run(launcher):
            return subprocess.run(['sh', '-c', launcher], input=event, capture_output=True, text=True,
                                  env=self.env, timeout=30)

        outputs = []
        with ThreadPoolExecutor(len(LAUNCHERS)) as pool:
            for future in as_completed([pool.submit(run, launcher) for launcher in LAUNCHERS]):
                completed = future.result()
                self.assertEqual((completed.returncode, completed.stderr), (0, ''))
                if completed.stdout:
                    output = json.loads(completed.stdout)['hookSpecificOutput']
                    self.assertEqual(output['hookEventName'], 'SessionStart')
                    self.assertLess(len(output['additionalContext']), 10000)
                    outputs.append(output['additionalContext'])
        self.assertEqual([path for path in self.temp.rglob('*') if path.is_file()], [])
        return outputs

    def context(self, source='resume'):
        return '\n'.join(self.parts(source))

    def block(self, name, text):
        return '<skill name="{}">\nBase directory for this skill: {}/.claude/skills/{}\n\n{}\n</skill>'.format(
            name, self.home, name, text)

    def test_settings_registers_each_part_once(self):
        spec = importlib.util.spec_from_file_location('restore_hook', HOOK)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual([int(re.search(r'hook\.py" (\d+) ', launcher).group(1)) for launcher in LAUNCHERS],
                         list(range(1, module.PARTS + 1)))

    def test_resume_after_a_broken_chain_restores_user_invoked_skills(self):
        self.skill('alpha', '# Alpha\n\nCurrent alpha text.')
        self.skill('gamma', '# Gamma')
        self.transcript.command('alpha', 'go\n  far ')
        self.transcript.model_skill('gamma')
        self.transcript.command('beta', recorded='Recorded beta text.')
        self.transcript.add('user', 'Next task.', parent='never-written')
        self.transcript.add('assistant', [{'type': 'text', 'text': 'Done.'}])
        self.assertEqual(self.context(), '\n\n'.join([
            HEADER.format('`/alpha go far`, `/beta`'),
            self.block('alpha', '# Alpha\n\nCurrent alpha text.\n\n\nARGUMENTS: go\n  far'),
            self.block('beta', '# Beta\n\nRecorded beta text.'),
        ]))

    def test_restores_only_what_the_last_compaction_dropped(self):
        self.skill('alpha', '# Alpha')
        self.skill('beta', '# Beta')
        self.transcript.command('alpha')
        self.transcript.compact()
        self.transcript.command('beta')
        self.assertEqual(self.context('resume'),
                         '\n\n'.join([HEADER.format('`/alpha`'), self.block('alpha', '# Alpha')]))
        self.assertEqual(self.context('compact'), '\n\n'.join([
            HEADER.format('`/alpha`, `/beta`'), self.block('alpha', '# Alpha'), self.block('beta', '# Beta')]))
        self.assertEqual(self.context('startup'), '')
        self.assertEqual(self.context('clear'), '')

    def test_compact_before_its_boundary_is_written_restores_everything(self):
        self.skill('alpha', '# Alpha')
        self.transcript.command('alpha')
        self.assertEqual(self.context('resume'), '')
        self.assertEqual(self.context('compact'),
                         '\n\n'.join([HEADER.format('`/alpha`'), self.block('alpha', '# Alpha')]))

    def test_a_restoration_already_in_context_is_not_repeated(self):
        self.skill('alpha', '# Alpha')
        self.transcript.command('alpha')
        self.transcript.compact()
        self.transcript.add('attachment', None, attachment={
            'type': 'hook_additional_context', 'content': [HEADER.format('`/alpha`') + '\n\n<skill name="alpha">']})
        self.transcript.add('user', 'Continue.')
        self.assertEqual(self.context('resume'), '')
        self.transcript.compact()
        self.assertEqual(self.context('resume'),
                         '\n\n'.join([HEADER.format('`/alpha`'), self.block('alpha', '# Alpha')]))

    def test_only_a_complete_copy_from_claude_code_counts_as_in_context(self):
        self.skill('alpha', '# Alpha\n\nFull text.')
        self.skill('beta', '# Beta\n\nFull text.')
        self.transcript.command('alpha')
        self.transcript.command('beta')
        self.transcript.compact(('alpha', '# Alpha\n\nFull text.'),
                                ('beta', '# Beta\n\n[... skill content truncated for compaction; use Read]'))
        self.assertEqual(self.context('resume'), '\n\n'.join([
            HEADER.format('`/beta`'), self.block('beta', '# Beta\n\nFull text.')]))

    def test_a_skill_abandoned_by_a_rewind_is_not_restored(self):
        self.skill('alpha', '# Alpha')
        self.skill('beta', '# Beta')
        start = self.transcript.add('user', 'Start.')
        self.transcript.command('alpha', 'ship it')
        self.transcript.command('beta', parent=start)
        self.transcript.compact()
        self.assertEqual(self.context('resume'),
                         '\n\n'.join([HEADER.format('`/beta`'), self.block('beta', '# Beta')]))

    def test_a_rewind_before_a_broken_chain_keeps_the_abandoned_skill_out(self):
        self.skill('alpha', '# Alpha')
        self.skill('send', '# Send')
        self.transcript.command('alpha')
        kept = self.transcript.entries[-1]['uuid']
        self.transcript.command('send', 'merge it')
        self.transcript.add('user', 'Do not merge.', parent=kept)
        self.transcript.add('assistant', [{'type': 'text', 'text': 'Understood.'}])
        self.transcript.add('user', 'Continue.', parent='never-written')
        self.assertEqual(self.context('resume'),
                         '\n\n'.join([HEADER.format('`/alpha`'), self.block('alpha', '# Alpha')]))

    def test_arguments_expand_into_the_placeholder_and_match_claude_codes_copy(self):
        self.skill('task', '# Task\n\nDo $ARGUMENTS.')
        self.transcript.command('task', 'C:\\work', recorded='Do C:\\work.')
        self.transcript.compact(('task', '# Task\n\nDo C:\\work.'))
        self.assertEqual(self.context('resume'), '')
        self.assertEqual(self.context('compact'), '\n\n'.join([
            HEADER.format('`/task C:\\work`'), self.block('task', '# Task\n\nDo C:\\work.')]))

    def test_an_indexed_placeholder_stays_as_written_and_the_arguments_follow(self):
        self.skill('task', '# Task\n\nUse $ARGUMENTS[0].')
        self.transcript.command('task', 'alpha beta', recorded='Use alpha.')
        self.transcript.compact()
        self.assertEqual(self.context('resume'), '\n\n'.join([
            HEADER.format('`/task alpha beta`'),
            self.block('task', '# Task\n\nUse $ARGUMENTS[0].\n\n\nARGUMENTS: alpha beta')]))

    def test_long_skills_arrive_whole_and_in_order(self):
        body = '\n'.join('Line {:05d} {}'.format(number, 'x' * 60) for number in range(400))
        self.skill('long', body)
        self.transcript.command('long')
        self.transcript.compact()
        self.assertEqual(self.context('resume'), '\n\n'.join([HEADER.format('`/long`'), self.block('long', body)]))

    def test_oldest_skills_give_way_when_all_parts_are_full(self):
        body = '\n'.join('y' * 99 for _ in range(200))
        for name in ('one', 'two', 'three', 'four', 'five'):
            self.skill(name, body)
            self.transcript.command(name, 'build the\nthing' if name == 'one' else None)
        self.transcript.compact()
        header = HEADER.format('`/one build the thing`, `/two`, `/three`, `/four`, `/five`')
        self.assertEqual(self.context('resume'), '\n\n'.join([
            header.replace('The text of each follows.', 'The text of each that fit follows.'),
            '`/one` did not fit. Read {}/.claude/skills/one/SKILL.md if its task is unfinished.\n'
            'ARGUMENTS: build the\nthing'.format(self.home),
            '`/two` did not fit. Read {}/.claude/skills/two/SKILL.md if its task is unfinished.'.format(self.home),
            self.block('three', body), self.block('four', body), self.block('five', body)]))

    def test_one_oversized_skill_is_cut_with_a_pointer_to_its_file(self):
        body = '\n'.join('z' * 99 for _ in range(900))
        self.skill('huge', body)
        self.transcript.command('huge')
        self.transcript.compact()
        parts = self.parts('resume')
        notice = '\n[Cut for space. Read the rest in {}/.claude/skills/huge/SKILL.md.]\n</skill>'.format(self.home)
        whole = '\n\n'.join([HEADER.format('`/huge`'), self.block('huge', body)])
        restored = '\n'.join(parts)
        kept = whole[:len(restored) - len(notice)]
        self.assertEqual(len(parts), len(LAUNCHERS))
        self.assertEqual(restored, kept + notice)
        self.assertTrue(kept.endswith('z' * 99))


if __name__ == '__main__':
    unittest.main()
