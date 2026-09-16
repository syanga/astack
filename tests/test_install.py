"""Exercise deployment against temporary checkouts and homes, never real settings."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parent.parent


class InstallerFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="astack test ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "checkout with spaces"
        shutil.copytree(SOURCE, self.repo, ignore=shutil.ignore_patterns(".git", ".cache", "__pycache__"))
        self.home = self.root / "test home"
        # The user's growing skill collection is not part of this test fixture.
        shutil.rmtree(self.repo / "skills")
        self.skill = self.repo / "skills/test-skill"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Exercise the installer in isolation.\n---\n\nTest instructions.\n"
        )
        helper = self.skill / "scripts/helper.sh"
        helper.parent.mkdir()
        helper.write_text("#!/bin/sh\nexit 0\n")
        helper.chmod(0o755)

    def run_installer(self, *arguments, command="install", success=True, env=None):
        result = subprocess.run(
            [str(self.repo / (command + ".sh")), "--home", str(self.home), *arguments],
            cwd=str(self.root), capture_output=True, text=True, env=env,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def manifest(self):
        return json.loads((self.home / ".local/state/astack/manifest.json").read_text())


class InstallerTests(InstallerFixture):
    def test_all_harnesses_and_supporting_files(self):
        self.run_installer("--target", "all")
        for config, filename, skills in [
            (".claude", "CLAUDE.md", ".claude/skills"),
            (".codex", "AGENTS.md", ".agents/skills"),
            (".gemini", "GEMINI.md", ".gemini/skills"),
            (".config/opencode", "AGENTS.md", ".config/opencode/skills"),
        ]:
            self.assertIn("Personal coding instructions", (self.home / config / filename).read_text())
            self.assertEqual((self.home / skills / "test-skill/SKILL.md").read_bytes(),
                             (self.skill / "SKILL.md").read_bytes())
            self.assertTrue(os.access(self.home / skills / "test-skill/scripts/helper.sh", os.X_OK))
        self.assertFalse(list(self.home.rglob("replace-with-skill-name")))
        self.assertEqual(len(self.manifest()["targets"]), 4)

    def test_dry_run_does_not_create_home(self):
        result = self.run_installer("--dry-run", "--target", "all")
        self.assertIn("WRITE", result.stdout)
        self.assertFalse(self.home.exists())

    def test_default_targets_and_repeat_install(self):
        self.run_installer()
        installed = self.home / ".codex/AGENTS.md"
        previous_mtime = installed.stat().st_mtime_ns
        result = self.run_installer()
        self.assertNotIn("WRITE", result.stdout)
        self.assertEqual(installed.stat().st_mtime_ns, previous_mtime)
        self.assertEqual(set(self.manifest()["targets"]), {"claude", "codex"})

    def test_shared_and_specific_instruction_updates(self):
        self.run_installer()
        with (self.repo / "instructions/AGENTS.md").open("a") as stream:
            stream.write("\nShared preference.\n")
        with (self.repo / "instructions/CLAUDE.md").open("a") as stream:
            stream.write("\nClaude preference.\n")
        self.run_installer()
        claude = (self.home / ".claude/CLAUDE.md").read_text()
        codex = (self.home / ".codex/AGENTS.md").read_text()
        self.assertIn("Shared preference.", claude)
        self.assertIn("Shared preference.", codex)
        self.assertIn("Claude preference.", claude)
        self.assertNotIn("Claude preference.", codex)

    def test_conflicts_preflight_all_targets(self):
        existing = self.home / ".codex/AGENTS.md"
        existing.parent.mkdir(parents=True)
        existing.write_text("Existing preferences")
        self.run_installer(success=False)
        self.assertEqual(existing.read_text(), "Existing preferences")
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".local").exists())

    def test_force_backup_and_dry_run(self):
        existing = self.home / ".codex/AGENTS.md"
        existing.parent.mkdir(parents=True)
        existing.write_text("Existing preferences")
        self.run_installer("--force", "--dry-run")
        self.assertEqual(existing.read_text(), "Existing preferences")
        self.assertFalse((self.home / ".local").exists())
        self.run_installer("--force")
        backups = list((self.home / ".local/state/astack/backups").rglob("AGENTS.md"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "Existing preferences")

    def test_local_edits_protected_on_update_and_uninstall(self):
        self.run_installer()
        installed = self.home / ".codex/AGENTS.md"
        installed.write_text("Local edits")
        self.run_installer(success=False)
        self.run_installer(command="uninstall", success=False)
        self.assertEqual(installed.read_text(), "Local edits")
        self.assertTrue((self.home / ".claude/CLAUDE.md").exists())
        self.run_installer("--force", command="uninstall")
        self.assertFalse(installed.exists())
        backups = list((self.home / ".local/state/astack/backups").rglob("AGENTS.md"))
        self.assertEqual(backups[0].read_text(), "Local edits")

    def test_uninstall_leaves_unmanaged_files_and_other_targets(self):
        self.run_installer()
        unmanaged = self.home / ".claude/skills/test-skill/personal.txt"
        unmanaged.write_text("Keep me")
        self.run_installer("--target", "claude", command="uninstall")
        self.assertEqual(unmanaged.read_text(), "Keep me")
        self.assertFalse((self.home / ".claude/CLAUDE.md").exists())
        self.assertTrue((self.home / ".codex/AGENTS.md").exists())
        self.run_installer("--target", "claude", command="uninstall")

    def test_deleted_skill_files_are_pruned(self):
        self.run_installer()
        shutil.rmtree(self.skill)
        self.run_installer()
        self.assertFalse((self.home / ".agents/skills/test-skill/SKILL.md").exists())
        self.assertFalse((self.home / ".claude/skills/test-skill/scripts/helper.sh").exists())

    def test_empty_skills_directory_installs(self):
        shutil.rmtree(self.skill)
        self.run_installer()
        self.assertTrue((self.home / ".codex/AGENTS.md").exists())
        self.assertFalse((self.home / ".agents/skills").exists())

    def test_destination_symlink_preserves_referent(self):
        referent = self.root / "personal.md"
        referent.write_text("Keep original")
        link = self.home / ".codex/AGENTS.md"
        link.parent.mkdir(parents=True)
        link.symlink_to(referent)
        self.run_installer(success=False)
        self.run_installer("--force")
        self.assertFalse(link.is_symlink())
        self.assertEqual(referent.read_text(), "Keep original")

    def test_broken_symlink_is_a_conflict(self):
        link = self.home / ".claude/CLAUDE.md"
        link.parent.mkdir(parents=True)
        link.symlink_to(self.root / "missing")
        self.run_installer(success=False)
        self.assertTrue(link.is_symlink())

    def test_invalid_skill_fails_before_writes(self):
        (self.skill / "SKILL.md").write_text("---\nname: incorrect\ndescription: Test\n---\n")
        self.run_installer(success=False)
        self.assertFalse(self.home.exists())

    def test_long_description_fails_before_writes(self):
        description = "Use when " + "x" * 200
        (self.skill / "SKILL.md").write_text("---\nname: test-skill\ndescription: {}\n---\n".format(description))
        self.run_installer(success=False)
        self.assertFalse(self.home.exists())

    def test_home_ignores_environment_overrides(self):
        outside = self.root / "outside"
        env = dict(os.environ, CODEX_HOME=str(outside), CLAUDE_CONFIG_DIR=str(outside),
                   XDG_CONFIG_HOME=str(outside), XDG_STATE_HOME=str(outside))
        self.run_installer("--target", "all", env=env)
        self.assertFalse(outside.exists())
        self.assertTrue((self.home / ".codex/AGENTS.md").exists())

    def test_destination_directory_is_not_replaced(self):
        blocker = self.home / ".codex/AGENTS.md"
        blocker.mkdir(parents=True)
        self.run_installer("--force", success=False)
        self.assertTrue(blocker.is_dir())
        self.assertFalse((self.home / ".claude").exists())

    def test_overlapping_target_destinations_are_rejected(self):
        registry = self.repo / "harnesses.json"
        harnesses = json.loads(registry.read_text())
        harnesses["gemini"]["skills"] = harnesses["codex"]["skills"]
        registry.write_text(json.dumps(harnesses))
        result = self.run_installer("--target", "all", success=False)
        self.assertIn("distinct destinations", result.stderr)
        self.assertFalse(self.home.exists())

    def test_symlinked_skill_roots_cannot_share_ownership(self):
        shared = self.home / ".agents/skills"
        shared.mkdir(parents=True)
        (self.home / ".claude").mkdir()
        (self.home / ".claude/skills").symlink_to(shared, target_is_directory=True)
        result = self.run_installer(success=False)
        self.assertIn("distinct destinations", result.stderr)
        self.assertFalse((shared / "test-skill/SKILL.md").exists())
        self.assertFalse((self.home / ".local").exists())

    def test_force_cannot_claim_another_targets_files_through_symlink(self):
        self.run_installer("--target", "codex")
        before = self.manifest()
        shared = self.home / ".agents/skills"
        installed = shared / "test-skill/SKILL.md"
        content = installed.read_bytes()
        (self.home / ".claude").mkdir()
        (self.home / ".claude/skills").symlink_to(shared, target_is_directory=True)
        result = self.run_installer("--target", "claude", "--force", success=False)
        self.assertIn("distinct destinations", result.stderr)
        self.assertEqual(installed.read_bytes(), content)
        self.assertEqual(self.manifest(), before)


if __name__ == "__main__":
    unittest.main()
