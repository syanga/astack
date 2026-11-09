import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'skills/worktree-cleanup/scripts/retirements.py'


class WorktreeRetirementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / 'repo'
        self.worktree = self.root / 'finished lane'
        self.state = self.root / 'retirements'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Test')
        (self.repo / 'source.txt').write_text('retained source\n')
        self.git('add', 'source.txt')
        self.git('commit', '-m', 'Initial source')
        self.git('worktree', 'add', '-b', 'finished', str(self.worktree))

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(SCRIPT), '--state-dir', str(self.state), *args], capture_output=True, text=True)

    def queue(self, owner='thread:finished'):
        return self.run_cli('queue', '--worktree', str(self.worktree), '--owner', owner,
                            '--authorization', 'Owner requested lane cleanup; merged, files accounted for')

    def test_retirement_survives_the_closing_process(self):
        queued = self.queue()
        self.assertEqual(queued.returncode, 0, queued.stderr)
        listed = self.run_cli('list')
        self.assertEqual(listed.returncode, 0, listed.stderr)
        record = json.loads(listed.stdout)[0]
        self.assertEqual(record['owner'], 'thread:finished')
        self.assertEqual(record['worktree'], str(self.worktree))
        self.assertEqual(record['head'], self.git('rev-parse', 'finished'))
        self.assertEqual(record['status'], 'pending')
        self.assertEqual((self.worktree / 'source.txt').read_text(), 'retained source\n')

    def test_bad_records_do_not_hide_other_retirements(self):
        queued = self.queue()
        self.assertEqual(queued.returncode, 0, queued.stderr)
        incomplete = json.loads(queued.stdout)
        del incomplete['branch']
        nested = '[' * 10000 + ']' * 10000
        bad_records = {
            '00-broken.json': b'{',
            '01-array.json': b'[]',
            '02-utf8.json': b'\xff',
            '03-future.json': b'{"version": 2}',
            '04-missing-branch.json': json.dumps(incomplete).encode(),
            '05-nested.json': nested.encode(),
            '06-unexpected-field.json': (json.dumps(json.loads(queued.stdout))[:-1] +
                                         ', "unexpected": ' + nested + '}').encode(),
        }
        for name, contents in bad_records.items():
            (self.state / name).write_bytes(contents)
        listed = self.run_cli('list')
        self.assertEqual(listed.returncode, 0, listed.stderr)
        records = json.loads(listed.stdout)
        pending = next(row for row in records if row.get('worktree') == str(self.worktree))
        self.assertEqual(pending['status'], 'pending')
        self.assertEqual(pending['owner'], 'thread:finished')
        for name in bad_records:
            held = next(row for row in records if row.get('record') == str(self.state / name))
            self.assertEqual(held['status'], 'held')
            self.assertIn('error', held)
        self.git('worktree', 'remove', str(self.worktree))
        completed = self.run_cli('complete', '--worktree', str(self.worktree), '--owner', 'thread:finished',
                                 '--head', self.git('rev-parse', 'finished'))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)['status'], 'completed')
        self.assertEqual(self.git('show', 'finished:source.txt'), 'retained source')
        for name, contents in bad_records.items():
            self.assertEqual((self.state / name).read_bytes(), contents)

    def test_queue_must_survive_deletion_of_its_target(self):
        self.state = self.worktree / '.retirements'
        refused = self.queue()
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual((self.worktree / 'source.txt').read_text(), 'retained source\n')
        self.assertFalse(self.state.exists())
        self.state = self.root / 'external-state'
        queued = self.queue()
        self.assertEqual(queued.returncode, 0, queued.stderr)
        self.assertEqual(json.loads(self.run_cli('list').stdout)[0]['status'], 'pending')

    def test_completion_requires_removal_and_preserves_branch_history(self):
        self.assertEqual(self.queue().returncode, 0)
        head = self.git('rev-parse', 'finished')
        args = ['complete', '--worktree', str(self.worktree), '--owner', 'thread:finished', '--head', head]
        refused = self.run_cli(*args)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual((self.worktree / 'source.txt').read_text(), 'retained source\n')
        self.assertEqual(json.loads(self.run_cli('list').stdout)[0]['owner'], 'thread:finished')
        self.git('worktree', 'remove', str(self.worktree))
        completed = self.run_cli(*args)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)['status'], 'completed')
        self.assertEqual(json.loads(self.run_cli('list').stdout), [])
        self.assertEqual(self.git('show', 'finished:source.txt'), 'retained source')

    def test_changed_checkout_requires_a_new_authorization_record(self):
        self.assertEqual(self.queue().returncode, 0)
        old = self.git('rev-parse', 'finished')
        (self.worktree / 'source.txt').write_text('new work\n')
        subprocess.run(['git', '-C', str(self.worktree), 'commit', '-am', 'New work'], check=True, capture_output=True)
        record = json.loads(self.run_cli('list').stdout)[0]
        self.assertEqual(record['status'], 'changed')
        self.assertEqual(record['head'], old)
        self.assertNotEqual(self.queue().returncode, 0)
        withdrawn = self.run_cli('withdraw', '--worktree', str(self.worktree), '--owner', 'thread:finished', '--head', old)
        self.assertEqual(withdrawn.returncode, 0, withdrawn.stderr)
        self.assertEqual((self.worktree / 'source.txt').read_text(), 'new work\n')
        self.assertEqual(self.queue().returncode, 0)
        self.assertEqual(json.loads(self.run_cli('list').stdout)[0]['head'], self.git('rev-parse', 'finished'))

    def test_different_owner_cannot_replace_or_withdraw_a_retirement(self):
        self.assertEqual(self.queue().returncode, 0)
        self.assertNotEqual(self.queue('thread:other').returncode, 0)
        refused = self.run_cli('withdraw', '--worktree', str(self.worktree), '--owner', 'thread:other',
                               '--head', self.git('rev-parse', 'finished'))
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(json.loads(self.run_cli('list').stdout)[0]['owner'], 'thread:finished')
        self.assertEqual((self.worktree / 'source.txt').read_text(), 'retained source\n')

    def test_uncommitted_work_is_kept_out_of_the_retirement_queue(self):
        (self.worktree / 'source.txt').write_text('unsaved edit\n')
        self.assertNotEqual(self.queue().returncode, 0)
        self.assertEqual((self.worktree / 'source.txt').read_text(), 'unsaved edit\n')
        (self.worktree / 'source.txt').write_text('retained source\n')
        (self.worktree / 'notes.txt').write_text('untracked notes\n')
        self.assertNotEqual(self.queue().returncode, 0)
        self.assertEqual((self.worktree / 'notes.txt').read_text(), 'untracked notes\n')
        self.assertEqual(json.loads(self.run_cli('list').stdout), [])

    def test_index_flags_cannot_hide_new_work_from_retirement_checks(self):
        for flag in ['assume-unchanged', 'skip-worktree']:
            with self.subTest(flag=flag):
                self.assertEqual(self.queue().returncode, 0)
                subprocess.run(['git', '-C', str(self.worktree), 'update-index', '--' + flag, 'source.txt'],
                               check=True, capture_output=True)
                (self.worktree / 'source.txt').write_text('hidden local work\n')
                refused = self.queue()
                self.assertNotEqual(refused.returncode, 0)
                self.assertEqual(json.loads(self.run_cli('list').stdout)[0]['status'], 'held')
                self.assertEqual((self.worktree / 'source.txt').read_text(), 'hidden local work\n')
                subprocess.run(['git', '-C', str(self.worktree), 'update-index', '--no-' + flag, 'source.txt'],
                               check=True, capture_output=True)
                (self.worktree / 'source.txt').write_text('retained source\n')
                self.assertEqual(self.queue().returncode, 0)
                self.assertEqual(json.loads(self.run_cli('list').stdout)[0]['status'], 'pending')

    def test_locked_and_primary_checkouts_are_preserved(self):
        self.git('worktree', 'lock', str(self.worktree))
        self.assertNotEqual(self.queue().returncode, 0)
        primary = self.run_cli('queue', '--worktree', str(self.repo), '--owner', 'thread:main', '--authorization', 'Cleanup')
        self.assertNotEqual(primary.returncode, 0)
        self.assertEqual((self.repo / 'source.txt').read_text(), 'retained source\n')
        self.git('worktree', 'unlock', str(self.worktree))
        self.assertEqual(self.queue().returncode, 0)

    def test_custom_default_branch_worktree_is_preserved(self):
        subprocess.run(['git', '-C', str(self.worktree), 'branch', '-m', 'trunk'], check=True, capture_output=True)
        self.git('update-ref', 'refs/remotes/origin/trunk', 'main')
        self.git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/trunk')
        self.assertNotEqual(self.queue().returncode, 0)
        self.assertEqual((self.worktree / 'source.txt').read_text(), 'retained source\n')
        self.assertEqual(self.git('rev-parse', 'trunk'), self.git('rev-parse', 'main'))

    def test_upstream_default_branch_worktree_is_preserved(self):
        subprocess.run(['git', '-C', str(self.worktree), 'branch', '-m', 'trunk'], check=True, capture_output=True)
        self.git('update-ref', 'refs/remotes/upstream/trunk', 'main')
        self.git('symbolic-ref', 'refs/remotes/upstream/HEAD', 'refs/remotes/upstream/trunk')
        self.assertNotEqual(self.queue().returncode, 0)
        self.assertEqual((self.worktree / 'source.txt').read_text(), 'retained source\n')
        self.git('symbolic-ref', '--delete', 'refs/remotes/upstream/HEAD')
        self.assertEqual(self.queue().returncode, 0)
        self.assertEqual(json.loads(self.run_cli('list').stdout)[0]['status'], 'pending')

    def test_completion_keeps_record_until_commit_is_retained(self):
        (self.worktree / 'source.txt').write_text('unique commit\n')
        subprocess.run(['git', '-C', str(self.worktree), 'commit', '-am', 'Unique work'], check=True, capture_output=True)
        head = self.git('rev-parse', 'finished')
        self.assertEqual(self.queue().returncode, 0)
        self.git('worktree', 'remove', str(self.worktree))
        self.git('branch', '-D', 'finished')
        args = ['complete', '--worktree', str(self.worktree), '--owner', 'thread:finished', '--head', head]
        self.assertNotEqual(self.run_cli(*args).returncode, 0)
        self.assertEqual(json.loads(self.run_cli('list').stdout)[0]['head'], head)
        self.git('branch', 'retained', head)
        self.assertEqual(self.run_cli(*args).returncode, 0)
        self.assertEqual(self.git('show', 'retained:source.txt'), 'unique commit')


if __name__ == '__main__':
    unittest.main()
