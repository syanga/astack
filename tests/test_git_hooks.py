"""Real Git repositories; fake scanner for lifecycle failures, real scanner when supplied."""

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills/open-pr/scripts"
sys.path.insert(0, str(SCRIPTS))
import git_hooks


class HookFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="astack hook café ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repository"
        self.repo.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("GIT_", "GITLEAKS_", "FAKE_"))}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.com",
                        GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.com")
        self.git("init", "-b", "main")
        self.base = self.commit("file.txt", "clean\n")
        self.hooks = self.repo / ".git/hooks"
        self.binary = self.root / "fake-gitleaks"
        self.binary.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
if sys.argv[1] == "version":
 print("8.30.1")
 sys.exit(0)
if os.environ.get("FAKE_LOG"):
 pathlib.Path(os.environ["FAKE_LOG"]).write_text(json.dumps(sys.argv[1:]))
code = int(os.environ.get("FAKE_EXIT", "0"))
findings = [] if code == 0 else [{"RuleID": "test", "File": "file.txt", "StartLine": 1,
 "Commit": "a" * 40, "Secret": "DO_NOT_PRINT_THIS", "Match": "DO_NOT_PRINT_THIS"}]
pathlib.Path(sys.argv[sys.argv.index("--report-path")+1]).write_text(json.dumps(findings))
print("DO_NOT_PRINT_THIS", file=sys.stderr)
sys.exit(code)
''')
        self.binary.chmod(0o755)

    def git(self, *args, repo=None, ok=True):
        result = subprocess.run(["git", "-C", str(repo or self.repo), *args],
                                env=self.env, capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def commit(self, name, content):
        (self.repo / name).write_text(content)
        self.git("add", "--", name)
        self.git("commit", "-m", "fixture")
        return self.git("rev-parse", "HEAD").stdout.strip()

    def manage(self, command="install", *extra, ok=True, repo=None, scripts=SCRIPTS):
        args = [sys.executable, str(scripts / "git_hooks.py"), command,
                "--repo", str(repo or self.repo), *extra]
        if command == "install":
            args += ["--gitleaks", str(self.binary)]
        result = subprocess.run(args, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def invoke(self, head=None, base=None, data=None, cwd=None):
        if data is None:
            head = head or self.git("rev-parse", "HEAD").stdout.strip()
            base = base or self.base
            data = "refs/heads/main {} refs/heads/main {}\n".format(head, base)
        return subprocess.run([str(self.hooks / "pre-push"), "origin", "unused"],
                              input=data, text=True, cwd=cwd or self.repo,
                              env=self.env, capture_output=True)


class HookTests(HookFixture):
    def test_install_idempotent_dry_run_uninstall_and_shared_worktree(self):
        self.manage("install", "--dry-run")
        self.assertFalse((self.hooks / "pre-push").exists())
        self.manage()
        installed = (self.hooks / "pre-push").read_bytes()
        self.manage()
        self.assertEqual((self.hooks / "pre-push").read_bytes(), installed)
        linked = self.root / "linked tree"
        self.git("worktree", "add", "-b", "linked", str(linked))
        self.manage(repo=linked)
        self.assertEqual(self.invoke(cwd=linked).returncode, 0)
        self.manage("uninstall", "--dry-run", repo=linked)
        self.assertTrue((self.hooks / "pre-push").exists())
        self.manage("uninstall", repo=linked)
        self.assertFalse((self.hooks / "pre-push").exists())
        self.assertFalse((self.hooks / git_hooks.PAYLOAD).exists())
        self.manage("uninstall")

    def test_copy_outside_the_checkout_installs_and_reads_its_pins(self):
        shipped = self.root / "shipped"
        shutil.copytree(SCRIPTS, shipped)
        self.manage(scripts=shipped)
        self.commit("file.txt", "changed\n")
        self.assertEqual(self.invoke().returncode, 0)
        unpinned = ("import platform, git_hooks\n"
                    "platform.machine = lambda: 'unpinned'\n"
                    "git_hooks.urllib.request.urlopen = None\n"
                    "git_hooks.download()\n")
        result = subprocess.run([sys.executable, "-c", unpinned], cwd=self.repo,
                                env={**self.env, "PYTHONPATH": str(shipped)},
                                capture_output=True, text=True)
        self.assertIn("No pinned download", result.stderr)

    def test_existing_hook_receives_all_refs_and_is_restored(self):
        old = self.hooks / "pre-push"
        old.write_text('#!/bin/sh\ncat > "$(dirname "$0")/seen"\nexit 0\n')
        old.chmod(0o751)
        before = old.read_bytes()
        self.manage()
        data = "refs/heads/main {0} refs/heads/main {0}\n".format(self.base) * 2
        self.assertEqual(self.invoke(data=data).returncode, 0)
        self.assertEqual((self.hooks / "seen").read_text(), data)
        self.manage("uninstall")
        self.assertEqual(old.read_bytes(), before)
        self.assertEqual(old.stat().st_mode & 0o777, 0o751)

    def test_existing_hook_failure_is_preserved(self):
        old = self.hooks / "pre-push"
        old.write_text("#!/bin/sh\nexit 7\n")
        old.chmod(0o755)
        self.manage()
        self.assertNotEqual(self.invoke().returncode, 0)

    def test_replace_gstack_skips_guard_chains_local_and_restores(self):
        old = self.hooks / "pre-push"
        old.write_bytes(b"#!/bin/sh\n" + git_hooks.GSTACK_MARKER + b"\nexit 9\n")
        old.chmod(0o755)
        before = old.read_bytes()
        local = self.hooks / "pre-push.local"
        local.write_text('#!/bin/sh\ncat > "$(dirname "$0")/local-seen"\n')
        local.chmod(0o755)
        self.manage(ok=False)
        self.assertEqual(old.read_bytes(), before)
        self.manage("install", "--replace-gstack")
        self.assertEqual(self.invoke().returncode, 0)
        self.assertTrue((self.hooks / "local-seen").exists())
        self.manage("uninstall")
        self.assertEqual(old.read_bytes(), before)
        self.assertTrue(local.exists())

    def test_refuses_custom_hooks_path_symlinks_and_unrelated_backups(self):
        custom = self.root / "shared-hooks"
        self.git("config", "core.hooksPath", str(custom))
        self.manage(ok=False)
        self.assertFalse(custom.exists())
        self.git("config", "--unset", "core.hooksPath")
        outside = self.root / "outside"
        outside.write_text("unchanged")
        (self.hooks / "pre-push").symlink_to(outside)
        self.manage(ok=False)
        self.assertEqual(outside.read_text(), "unchanged")
        (self.hooks / "pre-push").unlink()
        (self.hooks / git_hooks.ORIGINAL).write_text("personal backup")
        self.manage(ok=False)

    def test_uninstall_protects_modified_files_and_extra_files(self):
        self.manage()
        runner = self.hooks / git_hooks.PAYLOAD / "runner.py"
        before = runner.read_bytes()
        runner.write_bytes(before + b"# personal change\n")
        self.manage("uninstall", ok=False)
        self.assertTrue((self.hooks / "pre-push").exists())
        runner.write_bytes(before)
        note = runner.parent / "notes.txt"
        note.write_text("mine")
        self.manage("uninstall", ok=False)
        self.assertEqual(note.read_text(), "mine")
        note.unlink()
        self.manage("uninstall")

    def test_scanner_failures_block_without_echoing_content(self):
        self.commit("file.txt", "changed\n")
        self.manage()
        for code in [1, 10, 127]:
            self.env["FAKE_EXIT"] = str(code)
            result = self.invoke()
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("DO_NOT_PRINT_THIS", result.stdout + result.stderr)
        (self.hooks / git_hooks.PAYLOAD / "gitleaks").unlink()
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertEqual(self.invoke(head="0" * 40).returncode, 0)

    def test_bad_input_missing_remote_object_and_shallow_history_block(self):
        self.commit("file.txt", "changed\n")
        self.manage()
        self.assertNotEqual(self.invoke(data="bad input\n").returncode, 0)
        self.assertNotEqual(self.invoke(base="f" * 40).returncode, 0)
        (self.repo / ".git/shallow").write_text(self.base + "\n")
        self.assertNotEqual(self.invoke().returncode, 0)

    def test_new_branch_uses_its_history_without_unrelated_remote_exclusions(self):
        self.commit("file.txt", "changed\n")
        self.manage()
        log = self.root / "args.json"
        self.env["FAKE_LOG"] = str(log)
        result = self.invoke(base="0" * 40)
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(log.read_text())
        opts = args[args.index("--log-opts") + 1]
        self.assertNotIn("--remotes", opts)
        self.assertNotIn("^", opts)
        self.assertIn("--diff-merges=separate", opts)

    def test_wrong_version_preserves_original(self):
        old = self.hooks / "pre-push"
        old.write_text("#!/bin/sh\nexit 0\n")
        old.chmod(0o755)
        before = old.read_bytes()
        self.binary.write_text("#!/bin/sh\necho 0.0.0\n")
        self.manage(ok=False)
        self.assertEqual(old.read_bytes(), before)
        self.assertFalse((self.hooks / git_hooks.PAYLOAD).exists())

    def test_failed_hook_write_rolls_back_prepared_files(self):
        old = self.hooks / "pre-push"
        old.write_text("#!/bin/sh\nexit 0\n")
        old.chmod(0o751)
        before = old.read_bytes()
        with patch.object(git_hooks, "atomic_write", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                git_hooks.install(self.hooks, self.binary)
        self.assertEqual(old.read_bytes(), before)
        self.assertEqual(old.stat().st_mode & 0o777, 0o751)
        self.assertFalse((self.hooks / git_hooks.PAYLOAD).exists())
        self.assertFalse((self.hooks / git_hooks.ORIGINAL).exists())

    def test_interruption_after_hook_swap_preserves_recoverable_installation(self):
        old = self.hooks / "pre-push"
        old.write_text("#!/bin/sh\n# existing checks\nexit 0\n")
        old.chmod(0o751)
        before = old.read_bytes()
        replace = os.replace

        def interrupt_after_swap(source, destination):
            replace(source, destination)
            if Path(destination) == old:
                raise KeyboardInterrupt()

        with patch.object(git_hooks.os, "replace", side_effect=interrupt_after_swap):
            with self.assertRaises(KeyboardInterrupt):
                git_hooks.install(self.hooks, self.binary)
        self.assertEqual((self.hooks / git_hooks.ORIGINAL).read_bytes(), before)
        self.assertEqual(self.invoke().returncode, 0)
        self.manage()  # Interrupted installation can be retried safely.
        self.manage("uninstall")
        self.assertEqual(old.read_bytes(), before)
        self.assertEqual(old.stat().st_mode & 0o777, 0o751)

    def test_hook_survives_checkout_and_repository_moves(self):
        self.manage()
        moved = self.root / "moved repo"
        self.repo.rename(moved)
        self.repo = moved
        self.hooks = moved / ".git/hooks"
        self.binary.unlink()
        self.commit("file.txt", "change after move\n")
        self.assertEqual(self.invoke().returncode, 0)
        self.manage("uninstall")

    def test_verified_download_rejects_changed_archive(self):
        with patch.object(git_hooks.platform, "system", return_value="Darwin"), \
             patch.object(git_hooks.platform, "machine", return_value="arm64"), \
             patch.object(git_hooks.urllib.request, "urlopen", return_value=io.BytesIO(b"corrupt")):
            with self.assertRaisesRegex(ValueError, "checksum"):
                git_hooks.download()

    def test_lock_and_modified_original_are_preserved(self):
        lock = self.repo / ".git/astack-hooks.lock"
        lock.mkdir()
        self.manage(ok=False)
        self.assertFalse((self.hooks / "pre-push").exists())
        lock.rmdir()
        old = self.hooks / "pre-push"
        old.write_text("#!/bin/sh\nexit 0\n")
        old.chmod(0o755)
        self.manage()
        saved = self.hooks / git_hooks.ORIGINAL
        saved.write_text("personal changes")
        self.manage("uninstall", ok=False)
        self.assertEqual(saved.read_text(), "personal changes")
        self.assertIn(git_hooks.MARKER, old.read_text())


@unittest.skipUnless(os.environ.get("ASTACK_TEST_GITLEAKS"), "set ASTACK_TEST_GITLEAKS for real scanner tests")
class RealScannerTests(HookFixture):
    def setUp(self):
        super().setUp()
        self.binary = Path(os.environ["ASTACK_TEST_GITLEAKS"])
        self.remote = self.root / "remote.git"
        self.git("init", "--bare", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-u", "origin", "main")
        self.manage()

    def secret(self):
        # Synthetic fixture only, assembled to avoid a valid token in this source.
        return "ghp_" + "Ab3Cd4Ef5Gh6Ij7Kl8Mn9Op0Qr1St2Uv3Wx4"

    def test_real_replacement_commit_cannot_hide_pushed_secret(self):
        head = self.commit("credential.txt", self.secret() + "\n")
        tree = self.git("rev-parse", self.base + "^{tree}").stdout.strip()
        replacement = self.git("commit-tree", tree, "-p", self.base,
                               "-m", "clean local view").stdout.strip()
        self.git("replace", head, replacement)
        result = self.git("push", "origin", "main", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secret detected", result.stderr)
        self.assertNotIn(self.secret(), result.stderr)
        self.assertEqual(self.git("rev-parse", "main", repo=self.remote).stdout.strip(), self.base)

    def test_real_replacement_tag_cannot_change_selected_history(self):
        self.commit("credential.txt", self.secret() + "\n")
        self.git("tag", "-a", "secret-tag", "-m", "fixture")
        self.git("tag", "-a", "clean-tag", self.base, "-m", "fixture")
        original = self.git("rev-parse", "secret-tag").stdout.strip()
        replacement = self.git("rev-parse", "clean-tag").stdout.strip()
        self.git("replace", original, replacement)
        result = self.git("push", "origin", "secret-tag", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secret detected", result.stderr)
        self.assertEqual(self.git("ls-remote", "origin", "refs/tags/secret-tag").stdout, "")

    def test_real_clean_push_and_deleted_secret_in_intermediate_commit(self):
        self.commit("file.txt", "clean change\n")
        self.git("push", "origin", "main")
        token = self.secret()
        self.commit("credential.txt", token + "\n")
        self.git("rm", "credential.txt")
        self.git("commit", "-m", "remove fixture")
        result = self.git("push", "origin", "main", ok=False)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("secret detected", result.stderr)
        self.assertNotIn(token, result.stderr)

    def test_real_new_branch_with_secret_blocks_even_if_other_remote_has_it(self):
        self.git("checkout", "-b", "feature")
        head = self.commit("credential.txt", self.secret() + "\n")
        self.git("update-ref", "refs/remotes/private/feature", head)
        result = self.git("push", "origin", "feature", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secret detected", result.stderr)

    def test_real_force_push_and_merge_resolution_are_scanned(self):
        self.commit("file.txt", "published\n")
        self.git("push", "origin", "main")
        self.git("reset", "--hard", self.base)
        self.commit("credential.txt", self.secret() + "\n")
        self.assertNotEqual(self.git("push", "--force", "origin", "main", ok=False).returncode, 0)
        self.git("reset", "--hard", self.base)
        self.git("checkout", "-b", "side")
        self.commit("file.txt", "side\n")
        self.git("checkout", "main")
        self.commit("file.txt", "main\n")
        self.git("merge", "side", ok=False)
        self.commit("file.txt", self.secret() + "\n")
        result = self.git("push", "--force", "origin", "main", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secret detected", result.stderr)

    def test_real_external_diff_and_repo_allow_comments_do_not_hide_secret(self):
        self.commit("credential.txt", self.secret() + " # gitleaks:allow\n")
        driver = self.root / "empty-diff"
        driver.write_text("#!/bin/sh\nexit 0\n")
        driver.chmod(0o755)
        self.git("config", "diff.external", str(driver))
        result = self.git("push", "origin", "main", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secret detected", result.stderr)

    def test_real_worktree_push_and_annotated_tag(self):
        linked = self.root / "linked tree"
        self.git("worktree", "add", "-b", "linked", str(linked))
        (linked / "credential.txt").write_text(self.secret() + "\n")
        self.git("add", "credential.txt", repo=linked)
        self.git("commit", "-m", "fixture", repo=linked)
        result = self.git("push", "origin", "linked", repo=linked, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secret detected", result.stderr)
        self.git("tag", "-a", "fixture-tag", "-m", "fixture", repo=linked)
        result = self.git("push", "origin", "fixture-tag", repo=linked, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secret detected", result.stderr)

    def test_real_multiple_ref_push_is_atomic_when_one_contains_a_secret(self):
        clean = self.commit("file.txt", "clean ref change\n")
        self.git("branch", "clean", clean)
        self.git("checkout", "-b", "secret")
        self.commit("credential.txt", self.secret() + "\n")
        result = self.git("push", "origin", "clean", "secret", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secret detected", result.stderr)
        self.assertEqual(self.git("ls-remote", "origin", "refs/heads/clean").stdout, "")
