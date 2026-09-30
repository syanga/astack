import os
import subprocess
import sys

from test_install import InstallerFixture, SOURCE

CHECK = SOURCE / "skills/astack-help/scripts/check_update.py"


class UpdateCheckTests(InstallerFixture):
    def setUp(self):
        super().setUp()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.com",
                        GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.com")
        self.remote = self.root / "remote.git"

    def git(self, *arguments):
        return subprocess.run(["git", "-C", str(self.repo), *arguments], env=self.env,
                              capture_output=True, text=True, check=True).stdout.strip()

    def check(self):
        result = subprocess.run([sys.executable, str(CHECK), str(self.home / ".local/state/astack/manifest.json")],
                                env=self.env, capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def publish_checkout(self, branch="main"):
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "initial")
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], env=self.env, check=True)
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "origin", "main:" + branch)
        return self.git("rev-parse", "HEAD")

    def publish_change(self):
        (self.skill / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: A newer revision.\n---\n\nUpdated.\n")
        self.git("commit", "-q", "-am", "update")
        self.git("push", "-q", "origin", "main")
        return self.git("rev-parse", "HEAD")

    def test_reports_update_after_main_moves(self):
        installed = self.publish_checkout()
        self.run_installer(env=self.env)
        self.assertEqual(self.check(), "up to date: " + installed[:12])

        latest = self.publish_change()
        self.assertEqual(self.check(), "update available: claude at {0}, codex at {0}, main {1}".format(
            installed[:12], latest[:12]))

        self.run_installer(env=self.env)
        self.assertEqual(self.check(), "up to date: " + latest[:12])

    def test_reports_each_target_installed_behind_main(self):
        older = self.publish_checkout()
        self.run_installer("--target", "claude", env=self.env)
        latest = self.publish_change()
        self.run_installer("--target", "codex", env=self.env)
        self.assertEqual(self.check(), "update available: claude at {}, main {}".format(older[:12], latest[:12]))

        self.run_installer("--target", "claude", command="uninstall", env=self.env)
        self.assertEqual(self.check(), "up to date: " + latest[:12])

    def test_skips_install_without_git_checkout(self):
        self.run_installer(env=self.env)
        self.assertEqual(self.check(), "check skipped: the manifest records no installed commit for claude, codex. "
                                       "Rerun ./install.sh from a Git checkout with an origin remote.")

    def test_skips_remote_without_main(self):
        self.publish_checkout(branch="trunk")
        self.run_installer(env=self.env)
        self.assertEqual(self.check(), "check skipped: {} has no main branch".format(self.remote))

    def test_skips_unreachable_remote(self):
        self.publish_checkout()
        self.run_installer(env=self.env)
        self.remote.rename(self.root / "moved.git")
        self.assertTrue(self.check().startswith("check skipped: cannot read main from " + str(self.remote)))
