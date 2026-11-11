import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_run_job import SYSTEM_PYTHON, system_python_before_3_10


REPO = Path(__file__).resolve().parents[1]
ENTRIES = json.loads((REPO / 'settings/claude.json').read_text())['hooks']['PreToolUse']['$entries']
LAUNCHER = next(hook['command'] for entry in ENTRIES for hook in entry['hooks'] if 'claude-merge-gate' in hook['command'])
HEAD = 'a' * 40
OLD = 'b' * 40
URL = 'https://github.com/o/r/pull/5'
VIEW_FIELDS = ['--json', 'url,headRefOid,reviews']



def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pr = load('pr', REPO / 'skills/harden-pr/scripts/pr.py')
SIGNED = pr.format_body('claude-opus-5-5', 'Reviewed {}. Clean.'.format(HEAD), 'Alan Yang')

FAKE_GH = '''#!{python}
import json, os, sys, time
state = os.environ["FAKE_GH_DIR"]
with open(os.path.join(state, "calls.jsonl"), "a") as log:
    log.write(json.dumps({{"args": sys.argv[1:], "cwd": os.getcwd()}}) + "\\n")
with open(os.path.join(state, "response.json")) as handle:
    response = json.load(handle)
time.sleep(response["sleep"])
sys.stdout.write(response["stdout"])
sys.stderr.write(response["stderr"])
sys.exit(response["code"])
'''


def bash(command, cwd='/tmp'):
    return {'hook_event_name': 'PreToolUse', 'cwd': cwd, 'tool_name': 'Bash',
            'tool_input': {'command': command, 'description': 'merge'}}


def review(body=SIGNED, oid=HEAD, association='OWNER', state='COMMENTED'):
    return {'body': body, 'commit': {'oid': oid}, 'authorAssociation': association, 'state': state}


class MergeGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'home'
        shutil.copytree(REPO / 'skills/claude-merge-gate', self.home / '.claude/skills/claude-merge-gate',
                        ignore=shutil.ignore_patterns('__pycache__'))
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        if system_python_before_3_10():
            (self.bin / 'python3').symlink_to(SYSTEM_PYTHON)
        gh = self.bin / 'gh'
        gh.write_text(FAKE_GH.format(python=sys.executable))
        gh.chmod(0o755)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.env = {key: value for key, value in os.environ.items() if key != 'CLAUDE_CONFIG_DIR'}
        self.env.update(HOME=str(self.home), FAKE_GH_DIR=str(self.state),
                        PATH='{}{}{}'.format(self.bin, os.pathsep, os.environ['PATH']))
        self.respond(reviews=[])

    def respond(self, reviews=None, code=0, stderr='', head=HEAD, sleep=0):
        stdout = json.dumps({'url': URL, 'headRefOid': head, 'reviews': reviews}) if code == 0 else ''
        (self.state / 'response.json').write_text(json.dumps(
            {'code': code, 'stdout': stdout, 'stderr': stderr, 'sleep': sleep}))

    def calls(self):
        log = self.state / 'calls.jsonl'
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def run_hook(self, event):
        (self.state / 'calls.jsonl').unlink(missing_ok=True)
        completed = subprocess.run(['sh', '-c', LAUNCHER], input=json.dumps(event), capture_output=True,
                                   text=True, env=self.env, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        if not completed.stdout:
            return 'allow', '', self.calls()
        output = json.loads(completed.stdout)['hookSpecificOutput']
        return output['permissionDecision'], output['permissionDecisionReason'], self.calls()

    def test_allows_a_lone_merge_when_a_signed_review_exists_on_the_head(self):
        self.respond(reviews=[review()])
        work = self.root / 'work'
        work.mkdir()
        cases = {
            'gh pr merge 5 --repo o/r --squash --match-head-commit {}'.format(HEAD): ['5', '--repo', 'o/r'],
            'gh pr merge 5 --repo=o/r -s -d --match-head-commit={}'.format(HEAD): ['5', '--repo', 'o/r'],
            'gh pr merge 5 --repo a/b -R o/r --match-head-commit {}'.format(HEAD): ['5', '--repo', 'o/r'],
            '/opt/homebrew/bin/gh pr merge {} --admin --match-head-commit {}'.format(URL, HEAD): [URL],
            '  gh pr merge --squash --match-head-commit {}  '.format(HEAD): [],
        }
        for command, view_args in cases.items():
            with self.subTest(command):
                decision, _, calls = self.run_hook(bash(command, cwd=str(work)))
                self.assertEqual(decision, 'allow')
                self.assertEqual([(call['args'], os.path.realpath(call['cwd'])) for call in calls],
                                 [(['pr', 'view', *view_args, *VIEW_FIELDS], os.path.realpath(work))])

    def test_allows_a_merge_with_a_review_signed_by_pr_py_for_any_model_id(self):
        command = 'gh pr merge 5 --squash --match-head-commit {}'.format(HEAD)
        for model in ('claude-opus-5-5', 'claude-opus-5-5[1m]', 'claude opus 5.5'):
            with self.subTest(model):
                body = pr.build_review(model, {'body': 'Clean.', 'comments': []}, HEAD, 'Alan Yang')['body']
                self.respond(reviews=[review(body=body)])
                self.assertEqual(self.run_hook(bash(command))[0], 'allow')

    def test_denies_a_merge_without_a_signed_review_on_the_head(self):
        cases = {
            'no reviews': [],
            'signed review on an older commit': [review(oid=OLD)],
            'unsigned review on the head': [review(body='LGTM')],
            'signature below the first line': [review(body='Looks fine.\n\n' + SIGNED)],
            'signed by an outside account': [review(association='NONE')],
            'pending signed review': [review(state='PENDING')],
        }
        for name, reviews in cases.items():
            with self.subTest(name):
                self.respond(reviews=reviews)
                decision, reason, _ = self.run_hook(bash('gh pr merge 5 --squash'))
                self.assertEqual(decision, 'deny')
                self.assertIn('No signed review exists on {} at head {}.'.format(URL, HEAD), reason)
                self.assertIn(str(self.home / '.claude/skills/harden-pr/scripts/pr.py'), reason)
                self.assertIn(str(self.home / '.claude/skills/harden-pr/posting.md'), reason)

    def test_denies_a_reviewed_merge_that_does_not_pin_the_head(self):
        self.respond(reviews=[review()])
        for command in ('gh pr merge 5 --squash', 'gh pr merge 5 --squash --match-head-commit {}'.format(OLD)):
            with self.subTest(command):
                self.assertEqual(self.run_hook(bash(command))[:2], (
                    'deny', 'Pass --match-head-commit {} so the merge lands the reviewed head of {}.'.format(HEAD, URL)))

    def test_denies_when_gh_cannot_show_the_pr(self):
        self.respond(code=1, stderr='HTTP 401: Bad credentials')
        self.assertEqual(self.run_hook(bash('gh pr merge 5'))[:2],
                         ('deny', 'Could not check the reviews on PR 5: HTTP 401: Bad credentials'))
        self.respond(reviews=[], head=None)
        self.assertEqual(self.run_hook(bash('gh pr merge 5'))[:2],
                         ('deny', 'Could not check the reviews on PR 5: gh returned no head commit'))

    def test_denies_when_gh_hangs(self):
        hook = load('merge_gate_hook', REPO / 'skills/claude-merge-gate/scripts/hook.py')
        self.respond(reviews=[review()], sleep=5)
        with patch.dict(os.environ, {'PATH': self.env['PATH'], 'FAKE_GH_DIR': str(self.state)}), \
                patch.object(hook, 'GH_TIMEOUT_SECONDS', 0.5):
            reason = hook.decide(bash('gh pr merge 5'))
        self.assertRegex(reason, r'^Could not check the reviews on PR 5: .*timed out after 0\.5 seconds')

    def test_hook_timeout_outlasts_the_gh_check(self):
        hook = load('merge_gate_hook', REPO / 'skills/claude-merge-gate/scripts/hook.py')
        timeout = next(entry['hooks'][0]['timeout'] for entry in ENTRIES if 'claude-merge-gate' in entry['hooks'][0]['command'])
        self.assertGreater(timeout, hook.GH_TIMEOUT_SECONDS)

    def test_denies_any_other_command_that_mentions_a_pr_merge(self):
        commands = [
            'gh pr merge 5 --squash && gh pr view 5',
            'cd /tmp/other && gh pr merge 5',
            'x=$(gh pr merge 4)',
            'out="$(gh pr merge 4)"',
            'x=`gh pr merge 4`',
            'gh pr merge \\\n  5 --squash',
            "bash -lc 'gh pr merge 6'",
            "echo 'gh pr merge 5' | sh",
            "bash <<'EOF'\ngh pr merge 5\nEOF",
            'gh pr -R o/r merge 5',
            'GH_REPO=o/r gh pr merge 5',
            'gh pr merge "$PR"',
            'gh pr merge -sR o/r 5',
            'gh pr merge 5 6',
            'gh api -X PUT repos/o/r/pulls/9/merge',
            'gh api -X PUT repos/o/r/pulls/$N/merge',
            "gh api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: \"x\"}) { clientMutationId } }'",
            'git commit -m "feat: deny gh pr merge without a signed review"',
            'gh pr merge 41 --squash --subject part#1; gh pr merge 42 --admin',
            'gh pr merge -b\r 6',
            'gh pr merge 5 --squash --disable-auto --disable-auto=false',
            'gh pr merge 5 --help --help=false',
            'gh pr merge 5 --squash=false --match-head-commit {}'.format(HEAD),
            'gh pr merge 5 --squash 2>&1',
            'gh pr \\\n  merge 5 --squash',
            'gh pr "merge" 5',
            'gh pr m\\erge 5',
            'gh pr merge 5*',
            'gh pr merge ~/5',
            "gh api graphql -f query='mutation { enqueuePullRequest(input: {pullRequestId: \"x\"}) { clientMutationId } }'",
        ]
        for command in commands:
            with self.subTest(command):
                decision, reason, calls = self.run_hook(bash(command))
                self.assertEqual((decision, calls), ('deny', []))
                self.assertIn('gh pr merge <number> [--repo <owner>/<repo>] --match-head-commit <sha>', reason)

    def test_ignores_commands_that_do_not_merge_a_pr(self):
        quiet = [
            bash('git merge origin/main'),
            bash('git branch -D my-pr merge-test'),
            bash('gh api repos/o/r/merges -f base=main -f head=topic'),
            bash('gh pr view 5 --json mergeStateStatus,mergeable'),
            bash('gh pr list --state merged --search "is:merged"'),
            bash('grep -rn "merge gate" skills'),
            bash('grep -rn "pr merg." skills'),
            bash('grep -rn -e pr -e merg. skills'),
            bash('grep -rnE "pulls/.*/merg." skills'),
            bash('gh pr merge --disable-auto 5'),
            bash('gh pr merge --help'),
            {'hook_event_name': 'PreToolUse', 'cwd': '/tmp', 'tool_name': 'Write',
             'tool_input': {'file_path': '/tmp/x', 'content': 'gh pr merge 5'}},
        ]
        for event in quiet:
            with self.subTest(event['tool_input']):
                self.assertEqual(self.run_hook(event), ('allow', '', []))
        decision, _, calls = self.run_hook(bash('gh pr merge 5'))
        self.assertEqual((decision, len(calls)), ('deny', 1))


if __name__ == '__main__':
    unittest.main()
