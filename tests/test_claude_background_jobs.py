import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / 'skills'
LAUNCHER = next(hook['command'] for entry in json.loads((REPO / 'settings/claude.json').read_text())['hooks']['PreToolUse']['$entries']
                for hook in entry['hooks'] if 'claude-background-jobs' in hook['command'])
CORE = 'skills/run-job/scripts/run_job.py'


def background(command, timeout=None):
    tool_input = {'command': command, 'description': 'job', 'run_in_background': True}
    if timeout is not None:
        tool_input['timeout'] = timeout
    return {'hook_event_name': 'PreToolUse', 'cwd': '/tmp', 'tool_name': 'Bash', 'tool_input': tool_input}


class HookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'home'
        self.env = {key: value for key, value in os.environ.items() if key != 'CLAUDE_CONFIG_DIR'}
        self.env['HOME'] = str(self.home)

    def install(self, config):
        for name in ('claude-background-jobs', 'run-job'):
            shutil.copytree(SKILLS / name, config / 'skills' / name,
                            ignore=shutil.ignore_patterns('__pycache__'))
        return '{}/{}'.format(config, CORE)

    def hook(self, event, launcher=LAUNCHER, **env):
        stdin = event if isinstance(event, str) else json.dumps(event)
        return subprocess.run(['sh', '-c', launcher], input=stdin, capture_output=True, text=True,
                              env={**self.env, **env}, timeout=15)

    def output(self, command, launcher=LAUNCHER, timeout=None, **env):
        completed = self.hook(background(command, timeout), launcher, **env)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)['hookSpecificOutput'] if completed.stdout else None

    def decision(self, command, launcher=LAUNCHER, timeout=None, **env):
        output = self.output(command, launcher, timeout, **env)
        return 'allow' if output is None else output['permissionDecision']

    def test_allows_only_literal_commands_that_run_the_installed_core(self):
        core = self.install(self.home / '.claude')
        allowed = [
            'python3 {} --timeout 1770 -- make test'.format(core),
            'python3 {} --timeout 1770 -- make test\n'.format(core),
            'python3   {}  --timeout=60  --  make  test'.format(core),
            'python3.12 {} --timeout 60 -- make test'.format(core),
            '/opt/homebrew/bin/python3.12 {} --no-timeout -- npm run dev'.format(core),
            "'/opt/python bin:3/python3' {} --timeout 60 -- make test".format(core),
            'python3 {} --timeout 60 -- make test'.format(os.path.realpath(core)),
            "python3 {} --timeout 60 -- sh -c 'cd /repo && make 2>&1 | tee log; echo $(date) `id`'".format(core),
            "python3 {} --timeout 60 -- env CI=1 NAME='x y' make test".format(core),
            "python3 {} --timeout 60 -- grep -e 'a*b?[c]{{d}}~$HOME' log".format(core),
        ]
        denied = [
            'make test',
            'sleep 600',
            'nohup python3 {} --timeout 60 -- make test'.format(core),
            'python {} --timeout 60 -- make test'.format(core),
            'bin/python3 {} --timeout 60 -- make test'.format(core),
            '/usr/bin/python3-config {} --timeout 60 -- make test'.format(core),
            'python3 -u {} --timeout 60 -- make test'.format(core),
            'exec python3 {} --timeout 60 -- make test'.format(core),
            'cd /repo && python3 {} --timeout 60 -- make test'.format(core),
            'CI=1 python3 {} --timeout 60 -- make test'.format(core),
            'python3 ~/.claude/{} --timeout 60 -- make test'.format(CORE),
            'python3 $HOME/.claude/{} --timeout 60 -- make test'.format(CORE),
            'python3 "{}" --timeout 60 -- make test'.format(core),
            'python3 {} --timeout 60 -- echo "$(date)"'.format(core),
            'python3 {} --timeout 60 -- make test; sleep 600'.format(core),
            'python3 {} --timeout 60 -- make test && sleep 600'.format(core),
            'python3 {} --timeout 60 -- make test | tee log'.format(core),
            'python3 {} --timeout 60 -- make test &'.format(core),
            'python3 {} --timeout 60 -- make test > log'.format(core),
            'python3 {} --timeout 60 -- make < /dev/null'.format(core),
            'python3 {} --timeout 60 -- make test\nsleep 600'.format(core),
            'python3 {} --timeout 60 -- echo `id`'.format(core),
            'python3 {} --timeout 60 -- echo \\; sleep 600'.format(core),
            "python3 {} --timeout 60 -- echo 'unterminated".format(core),
            "A=$\\\n'\\'' ; sleep 600 ; B=\\' python3 {} --timeout 60 -- true".format(core),
            "cd $'\\'' ; sleep 600 ; true \\' && python3 {} --timeout 60 -- make".format(core),
            "python3 {} --timeout 60 -- true $[a['$(sleep 600)']]".format(core),
            '/tmp/glob/*/python3 {} --timeout 60 -- true'.format(core),
            '/tmp/glob/{{a,b}}/python3 {} --timeout 60 -- true'.format(core),
            'python3 {}/*/../run_job.py --timeout 60 -- true'.format(os.path.dirname(core)),
            'python3 {} --timeout 60 -- true ${{a[b]}}'.format(core),
            'python3 {} --timeout 60 -- true $HOME'.format(core),
            'python3 {} --timeout 60 -- true *.log'.format(core),
            'python3 {} --timeout 60 -- true ~'.format(core),
            'python3 {} --timeout 60 -- true #comment'.format(core),
            'python3 {} --timeout 60 -- true !!'.format(core),
            'python3 {} --timeout 60 -- true\r'.format(core),
            'python3 {}.bak --timeout 60 -- make'.format(core),
            'python3 /tmp/skills/run-job/scripts/run_job.py --timeout 60 -- make',
        ]
        for command in allowed:
            with self.subTest(command=command):
                self.assertEqual(self.decision(command), 'allow')
        for command in denied:
            with self.subTest(command=command):
                self.assertEqual(self.decision(command), 'deny')

    def test_denies_a_tool_timeout_that_ends_before_the_deadline(self):
        core = self.install(self.home / '.claude')
        job = 'python3 {} --timeout {} -- make test'
        cases = [
            (1800, None, 'deny'),
            (5400, None, 'deny'),
            (1770, None, 'allow'),
            (3600, 3629999, 'deny'),
            (3600, 3630000, 'allow'),
            (7171, 7200000, 'deny'),
            (7170, 7200000, 'allow'),
            (1e308, 7200000, 'deny'),
            (0.5, None, 'allow'),
            (3600.4, 3630399, 'deny'),
            (3600.4, 3630400, 'allow'),
        ]
        for seconds, timeout, expected in cases:
            with self.subTest(seconds=seconds, timeout=timeout):
                self.assertEqual(self.decision(job.format(core, seconds), timeout=timeout), expected)
        for command in ('python3 {} --no-timeout -- npm run dev'.format(core),
                        "python3 {} --timeout 60 -- sh -c 'make --timeout 9999'".format(core),
                        'python3 {} --timeout 0 -- make test'.format(core),
                        'python3 {} --timeout 5400 --no-timeout -- make test'.format(core)):
            with self.subTest(command=command):
                self.assertEqual(self.decision(command), 'allow')

    def test_deadline_deny_names_the_timeout_to_send(self):
        core = self.install(self.home / '.claude')
        job = 'python3 {} --timeout {} -- make test'
        self.assertIn('5430000', self.output(job.format(core, 5400))['permissionDecisionReason'])
        self.assertIn('7170', self.output(job.format(core, 7200))['permissionDecisionReason'])

    def test_reading_the_deadline_leaves_no_bytecode_in_the_installed_skills(self):
        core = self.install(self.home / '.claude')
        self.env.pop('PYTHONDONTWRITEBYTECODE', None)
        self.assertEqual(self.decision('python3 {} --timeout 60 -- make test'.format(core)), 'allow')
        self.assertEqual(list((self.home / '.claude' / 'skills').rglob('__pycache__')), [])

    def test_allowed_commands_run_the_installed_core_in_each_shell(self):
        core = self.install(self.home / '.claude')
        command = "python3 {} --timeout 60 -- printf '%s|' 'a b' '$HOME' '*' 'x;y'".format(core)
        self.assertEqual(self.decision(command), 'allow')
        for shell in ('sh', 'bash', 'zsh'):
            if shutil.which(shell):
                with self.subTest(shell=shell):
                    ran = subprocess.run([shell, '-c', command], capture_output=True, text=True,
                                         env=self.env, timeout=30)
                    self.assertEqual((ran.returncode, ran.stdout), (0, 'a b|$HOME|*|x;y|'))

    def test_decides_long_malformed_commands_quickly(self):
        core = self.install(self.home / '.claude')
        for command in ('a' * 100000 + '$', 'python3 {} '.format(core) + 'x ' * 50000 + ';', "'" + 'a' * 100000):
            with self.subTest(length=len(command)):
                started = time.monotonic()
                self.assertEqual(self.decision(command), 'deny')
                self.assertLess(time.monotonic() - started, 5)

    def test_follows_a_custom_config_directory(self):
        config = self.root / 'config'
        core = self.install(config)
        self.install(self.home / '.claude')
        self.assertEqual(self.decision('python3 {} --timeout 60 -- make'.format(core),
                                       CLAUDE_CONFIG_DIR=str(config)), 'allow')
        self.assertEqual(self.decision('python3 {}/.claude/{} --timeout 60 -- make'.format(self.home, CORE),
                                       CLAUDE_CONFIG_DIR=str(config)), 'deny')

    def test_deny_gives_the_commands_to_run_instead(self):
        core = self.install(self.home / '.claude')
        completed = self.hook(background('make test | tee log'))
        self.assertEqual(completed.returncode, 0)
        output = json.loads(completed.stdout)['hookSpecificOutput']
        self.assertEqual((output['hookEventName'], output['permissionDecision']), ('PreToolUse', 'deny'))
        reason = output['permissionDecisionReason']
        self.assertIn('python3 {} --timeout SECONDS -- COMMAND'.format(core), reason)
        self.assertIn('python3 {} --no-timeout -- COMMAND'.format(core), reason)
        self.assertIn("sh -c '...'", reason)
        self.assertIn('run-job', reason)

    def test_allows_calls_that_are_not_background_bash(self):
        self.install(self.home / '.claude')
        foreground = {'tool_name': 'Bash', 'tool_input': {'command': 'make test | tee log'}}
        attended = {'tool_name': 'Bash', 'tool_input': {'command': 'make test', 'run_in_background': False}}
        other = {'tool_name': 'Monitor', 'tool_input': {'command': 'make test', 'run_in_background': True}}
        for event in (foreground, attended, other):
            with self.subTest(event=event):
                completed = self.hook(event)
                self.assertEqual((completed.returncode, completed.stdout), (0, ''))
        self.assertEqual(self.decision('make test'), 'deny')

    def test_fails_open_with_exit_1_on_a_malformed_event(self):
        self.install(self.home / '.claude')
        missing_command = {'tool_name': 'Bash', 'tool_input': {'run_in_background': True}}
        listed_command = {'tool_name': 'Bash', 'tool_input': {'command': ['make'], 'run_in_background': True}}
        for event in ('not json', {}, [], missing_command, listed_command):
            with self.subTest(event=event):
                completed = self.hook(event)
                self.assertEqual((completed.returncode, completed.stdout), (1, ''))

    def test_fails_open_with_exit_1_when_the_hook_is_not_installed(self):
        completed = self.hook(background('make test'), CLAUDE_CONFIG_DIR=str(self.root / 'empty'))
        self.assertEqual((completed.returncode, completed.stdout), (1, ''))

    def test_installed_settings_run_the_installed_hook(self):
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'python3').symlink_to(sys.executable)
        subprocess.run([str(REPO / 'install.sh'), '--home', str(self.home), '--target', 'claude'],
                       check=True, capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, PATH=os.pathsep.join([str(bin_dir), '/usr/bin', '/bin'])))
        settings = json.loads((self.home / '.claude/settings.json').read_text())
        command, = [hook['command'] for group in settings['hooks']['PreToolUse'] for hook in group['hooks']
                    if 'claude-background-jobs' in hook['command']]
        core = self.home / '.claude' / CORE
        for tool_command, expected in (('make test', 'deny'),
                                       ('python3 {} --timeout 60 -- make test'.format(core), 'allow')):
            with self.subTest(command=tool_command):
                self.assertEqual(self.decision(tool_command, command), expected)


if __name__ == '__main__':
    unittest.main()
