"""Deployment lifecycle and failure tests; all destinations are temporary."""

from contextlib import redirect_stdout
import errno
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
from unittest.mock import patch

from test_install import InstallerFixture


class InstallerLifecycleTests(InstallerFixture):
    def inventory(self):
        return {
            str(path.relative_to(self.home)): (
                path.read_bytes(), stat.S_IMODE(path.stat().st_mode), path.stat().st_mtime_ns
            )
            for path in self.home.rglob("*") if path.is_file()
        }

    def manager(self):
        spec = importlib.util.spec_from_file_location("astack_manager", self.repo / "scripts/manage.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def run_with_config(self, module, env, *arguments):
        # Exercise production environment handling without changing the real HOME.
        with patch.object(module.Path, "home", return_value=self.home), \
                patch.dict(module.os.environ, env, clear=True), \
                patch.object(module.sys, "argv", ["manage.py", *arguments]), \
                redirect_stdout(io.StringIO()):
            module.main()

    def test_full_lifecycle_all_targets_with_portable_assets(self):
        self.home = self.root / "home with spaces café"
        asset = self.skill / "references/café example.bin"
        asset.parent.mkdir()
        asset.write_bytes(bytes(range(256)))
        metadata = self.skill / "agents/openai.yaml"
        metadata.parent.mkdir()
        metadata.write_text('interface:\n  display_name: "Test skill"\n')
        (self.skill / "LICENSE").write_text("Fixture license\n")
        for relative in (".env", "__pycache__/cached.pyc", "scripts/helper.pyc"):
            excluded = self.skill / relative
            excluded.parent.mkdir(exist_ok=True)
            excluded.write_text("Do not install")
        roots = [self.home / name / "test-skill" for name in (
            ".claude/skills", ".agents/skills", ".gemini/skills", ".config/opencode/skills"
        )]

        self.run_installer("--target", "all")
        self.assertNotIn("WRITE", self.run_installer("--target", "all").stdout)
        for root in roots:
            for relative in ("references/café example.bin", "agents/openai.yaml", "LICENSE"):
                self.assertEqual((root / relative).read_bytes(), (self.skill / relative).read_bytes())
            for relative in (".env", "__pycache__", "scripts/helper.pyc"):
                self.assertFalse((root / relative).exists())
            subprocess.run([str(root / "scripts/helper.sh")], check=True)

        asset.write_bytes(b"Updated binary\x00\xff")
        helper = self.skill / "scripts/helper.sh"
        helper.write_text("#!/bin/sh\nprintf 'updated\\n'\n")
        helper.chmod(0o700)
        metadata.unlink()
        self.run_installer("--target", "all")
        for root in roots:
            self.assertEqual((root / "references/café example.bin").read_bytes(), asset.read_bytes())
            self.assertFalse((root / "agents/openai.yaml").exists())
            installed_helper = root / "scripts/helper.sh"
            self.assertEqual(stat.S_IMODE(installed_helper.stat().st_mode), 0o700)
            self.assertEqual(subprocess.check_output([str(installed_helper)], text=True), "updated\n")

        managed = [Path(name) for records in self.manifest()["targets"].values() for name in records]
        self.run_installer("--target", "all", command="uninstall")
        self.assertTrue(all(not path.exists() for path in managed))
        self.assertEqual(self.manifest()["targets"], {})
        self.run_installer("--target", "all", command="uninstall")

    def test_uninstall_without_install_creates_nothing(self):
        self.run_installer("--target", "all", command="uninstall")
        self.assertFalse(self.home.exists())

    def test_uninstall_preview_leaves_every_file_unchanged(self):
        self.run_installer("--target", "all")
        before = self.inventory()
        self.run_installer("--target", "all", "--dry-run", command="uninstall")
        self.assertEqual(self.inventory(), before)

    def test_moved_checkout_can_uninstall_and_installed_helper_still_runs(self):
        self.run_installer("--target", "all")
        moved = self.root / "moved checkout"
        self.repo.rename(moved)
        self.repo = moved
        subprocess.run([str(self.home / ".agents/skills/test-skill/scripts/helper.sh")], check=True)
        self.run_installer("--target", "all", command="uninstall")
        self.assertEqual(self.manifest()["targets"], {})

    def test_uninstall_tolerates_manually_deleted_files(self):
        self.run_installer("--target", "all")
        (self.home / ".claude/CLAUDE.md").unlink()
        (self.home / ".agents/skills/test-skill/SKILL.md").unlink()
        self.run_installer("--target", "all", command="uninstall")
        self.assertEqual(self.manifest()["targets"], {})

    def test_renamed_skill_protects_local_edits_and_unmanaged_files(self):
        self.run_installer("--target", "codex")
        old = self.home / ".agents/skills/test-skill"
        (old / "SKILL.md").write_text("Local edits")
        (old / "personal.txt").write_text("Personal notes")
        renamed = self.skill.with_name("renamed-skill")
        self.skill.rename(renamed)
        entry = renamed / "SKILL.md"
        entry.write_text(entry.read_text().replace("name: test-skill", "name: renamed-skill"))
        before = self.inventory()
        self.run_installer("--target", "codex", success=False)
        self.assertEqual(self.inventory(), before)
        self.run_installer("--target", "codex", "--force")
        self.assertFalse((old / "SKILL.md").exists())
        self.assertEqual((old / "personal.txt").read_text(), "Personal notes")
        self.assertTrue((old.with_name("renamed-skill") / "SKILL.md").exists())
        backups = list((self.home / ".local/state/astack/backups").rglob("SKILL.md"))
        self.assertEqual([path.read_text() for path in backups], ["Local edits"])
        self.run_installer("--target", "codex", command="uninstall")
        self.assertEqual((old / "personal.txt").read_text(), "Personal notes")
        self.assertTrue(backups[0].exists())

    def test_custom_environment_locations_install_and_uninstall(self):
        module = self.manager()
        custom = self.root / "custom config"
        env = {
            "CLAUDE_CONFIG_DIR": str(custom / "claude"),
            "CODEX_HOME": str(custom / "codex"),
            "XDG_CONFIG_HOME": str(custom / "xdg"),
            "XDG_STATE_HOME": str(custom / "state"),
        }
        self.run_with_config(module, env, "install", "--target", "all")
        expected = [
            custom / "claude/CLAUDE.md", custom / "claude/skills/test-skill/SKILL.md",
            custom / "codex/AGENTS.md", self.home / ".agents/skills/test-skill/SKILL.md",
            custom / "xdg/opencode/AGENTS.md", custom / "xdg/opencode/skills/test-skill/SKILL.md",
            self.home / ".gemini/GEMINI.md", self.home / ".gemini/skills/test-skill/SKILL.md",
        ]
        self.assertTrue(all(path.is_file() for path in expected))
        state = custom / "state/astack/manifest.json"
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)
        self.assertFalse((self.home / ".local/state").exists())
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".codex").exists())
        self.run_with_config(module, env, "uninstall", "--target", "all")
        self.assertTrue(all(not path.exists() for path in expected))
        self.assertEqual(json.loads(state.read_text())["targets"], {})

    def test_changed_config_requires_uninstall_then_reinstall(self):
        module = self.manager()
        original = self.root / "original config"
        replacement = self.root / "replacement config"
        env = {"CLAUDE_CONFIG_DIR": str(original)}
        self.run_with_config(module, env, "install", "--target", "claude")
        settings = original / "settings.json"
        content = json.loads(settings.read_text())
        content["personal"] = True
        settings.write_text(json.dumps(content) + "\n")
        env["CLAUDE_CONFIG_DIR"] = str(replacement)
        with self.assertRaisesRegex(ValueError, "destination changed"):
            self.run_with_config(module, env, "install", "--target", "claude")
        self.assertFalse(replacement.exists())
        self.run_with_config(module, env, "uninstall", "--target", "claude")
        self.assertFalse((original / "CLAUDE.md").exists())
        self.assertTrue(settings.exists())
        self.run_with_config(module, env, "install", "--target", "claude")
        self.assertTrue((replacement / "CLAUDE.md").exists())

    def test_invalid_state_does_not_change_installed_files(self):
        self.run_installer()
        state = self.home / ".local/state/astack/manifest.json"
        for content in ("{invalid", '{"version": 999, "targets": {}}'):
            with self.subTest(content=content):
                state.write_text(content)
                before = self.inventory()
                for command in ("install", "uninstall"):
                    self.run_installer(command=command, success=False)
                    self.assertEqual(self.inventory(), before)

    def test_blocked_state_directory_prevents_any_installation(self):
        blocker = self.home / ".local/state/astack"
        blocker.parent.mkdir(parents=True)
        blocker.write_text("Unrelated file")
        before = self.inventory()
        self.run_installer("--target", "all", success=False)
        self.assertEqual(self.inventory(), before)

    def test_failed_file_replace_preserves_old_file_and_retry_succeeds(self):
        self.run_installer("--target", "claude")
        target = (self.home / ".claude/skills/test-skill/SKILL.md").resolve()
        original = target.read_bytes()
        (self.skill / "SKILL.md").write_bytes(original + b"\nUpdated instructions.\n")
        module = self.manager()
        real_replace = module.os.replace

        def fail_one_file(source, destination):
            if Path(destination) == target:
                raise OSError(errno.ENOSPC, "Injected disk-full failure")
            return real_replace(source, destination)

        with patch.object(module.os, "replace", side_effect=fail_one_file):
            with self.assertRaises(OSError):
                self.run_with_config(module, {}, "install", "--target", "claude")
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(self.manifest()["targets"]["claude"][str(target)],
                         module.fingerprint(original, 0o644))
        self.assertFalse(list(self.home.rglob(".astack-*")))
        self.run_installer("--target", "claude")
        self.assertEqual(target.read_bytes(), (self.skill / "SKILL.md").read_bytes())
        self.run_installer("--target", "claude", command="uninstall")
        self.assertFalse(target.exists())

    def test_force_backup_can_be_restored_after_uninstall(self):
        original = self.home / ".claude/CLAUDE.md"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"Personal instructions\n")
        original.chmod(0o600)
        self.run_installer("--target", "claude", "--force")
        backup, = (self.home / ".local/state/astack/backups").rglob("CLAUDE.md")
        self.run_installer("--target", "claude", command="uninstall")
        self.assertFalse(original.exists())
        backup.rename(original)
        self.assertEqual(original.read_bytes(), b"Personal instructions\n")
        self.assertEqual(stat.S_IMODE(original.stat().st_mode), 0o600)
