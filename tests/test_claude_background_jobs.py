import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SKILLS = Path(__file__).resolve().parents[1] / 'skills'
LAUNCHER = 'python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/claude-background-jobs/scripts/hook.py" || exit 1'
CORE = 'skills/run-job/scripts/run_job.py'


def background(command):
    return {'hook_event_name': 'PreToolUse', 'cwd': '/tmp', 'tool_name': 'Bash',
            'tool_input': {'command': command, 'description': 'job', 'run_in_background': True}}


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

    def hook(self, event, **env):
        stdin = event if isinstance(event, str) else json.dumps(event)
        return subprocess.run(['sh', '-c', LAUNCHER], input=stdin, capture_output=True, text=True,
                              env={**self.env, **env}, timeout=15)

    def decision(self, command, **env):
        completed = self.hook(background(command), **env)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        if not completed.stdout:
            return 'allow'
        return json.loads(completed.stdout)['hookSpecificOutput']['permissionDecision']

    def test_allows_only_commands_that_run_through_the_installed_core(self):
        core = self.install(self.home / '.claude')
        allowed = [
            'python3 {} --timeout 1800 -- make test'.format(core),
            'python3.12 {} --timeout 60 -- make test'.format(core),
            '/opt/homebrew/bin/python3.12 {} --no-timeout -- npm run dev'.format(core),
            'exec python3 {} --timeout 60 -- make test'.format(core),
            "cd '/tmp/a b' && CI=1 NAME='x y' exec python3 {} --timeout 60 -- make test".format(core),
            'python3 ~/.claude/{} --timeout 60 -- make test'.format(CORE),
            'python3 $HOME/.claude/{} --timeout 60 -- make test'.format(CORE),
            'python3 "${{HOME}}/.claude/{}" --timeout 60 -- make test'.format(CORE),
            'python3 "${{CLAUDE_CONFIG_DIR:-$HOME/.claude}}/{}" --timeout 60 -- make test'.format(CORE),
            "python3 {} --timeout 60 -- sh -c 'make 2>&1 | tee log; echo $(date) `id` && exit'".format(core),
            'python3 {} --timeout 60 -- sh -c "make test || true; wait"'.format(core),
            'python3 {} --timeout 60 -- echo \\; \\| \\& \\> \\$\\(date\\)'.format(core),
            'python3 {} --timeout 60 -- make test # ; | > note'.format(core),
        ]
        denied = [
            'make test',
            'sleep 600',
            'nohup python3 {} --timeout 60 -- make test'.format(core),
            'python {} --timeout 60 -- make test'.format(core),
            'python3 -u {} --timeout 60 -- make test'.format(core),
            'sh -c "python3 {} --timeout 60 -- make test"'.format(core),
            'python3 {} --timeout 60 -- make test; echo done'.format(core),
            'python3 {} --timeout 60 -- make test | tee log'.format(core),
            'python3 {} --timeout 60 -- make test &'.format(core),
            'python3 {} --timeout 60 -- make test || true'.format(core),
            'python3 {} --timeout 60 -- make test && echo done'.format(core),
            'cd /tmp && cd /var && python3 {} --timeout 60 -- make test'.format(core),
            'cd /tmp; python3 {} --timeout 60 -- make test'.format(core),
            '(python3 {} --timeout 60 -- make test)'.format(core),
            'python3 {} --timeout 60 -- make test\necho done'.format(core),
            'python3 {} --timeout 60 -- make test > log'.format(core),
            'python3 {} --timeout 60 -- make test 2>&1'.format(core),
            'python3 {} --timeout 60 -- make test < /dev/null'.format(core),
            'python3 {} --timeout 60 -- cat <<EOF\nx\nEOF'.format(core),
            'python3 {} --timeout 60 -- echo $(date)'.format(core),
            'python3 {} --timeout 60 -- echo "$(date)"'.format(core),
            'python3 {} --timeout 60 -- echo `date`'.format(core),
            'python3 {} --timeout 60 -- echo "unterminated'.format(core),
            "python3 {} --timeout 60 -- make #'\necho done\n#'".format(core),
            "python3 {} --timeout 60 -- echo ${{x:-'}}'}};echo done;\\'".format(core),
            'python3 run_job.py --timeout 60 -- make test',
            'python3 /tmp/run_job.py --timeout 60 -- make test',
            'python3 {}/elsewhere/{} --timeout 60 -- make test'.format(self.root, CORE),
            'python3 {}.bak --timeout 60 -- make test'.format(core),
            "python3 '~/.claude/{}' --timeout 60 -- make test".format(CORE),
            "python3 '$HOME/.claude/{}' --timeout 60 -- make test".format(CORE),
            'python3 $PWD/.claude/{} --timeout 60 -- make test'.format(CORE),
            'python3 ~/.claude/skills/*/scripts/run_job.py --timeout 60 -- make test',
        ]
        for command in allowed:
            with self.subTest(command=command):
                self.assertEqual(self.decision(command), 'allow')
        for command in denied:
            with self.subTest(command=command):
                self.assertEqual(self.decision(command), 'deny')

    def test_follows_a_custom_config_directory(self):
        config = self.root / 'config'
        core = self.install(config)
        self.assertEqual(self.decision('python3 {} --timeout 60 -- make'.format(core),
                                       CLAUDE_CONFIG_DIR=str(config)), 'allow')
        self.assertEqual(self.decision('python3 "${{CLAUDE_CONFIG_DIR:-$HOME/.claude}}/{}" --timeout 60 -- make'
                                       .format(CORE), CLAUDE_CONFIG_DIR=str(config)), 'allow')
        self.assertEqual(self.decision('python3 ~/.claude/{} --timeout 60 -- make'.format(CORE),
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


if __name__ == '__main__':
    unittest.main()
