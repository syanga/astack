"""Exercise deployment against temporary checkouts and homes, never real settings."""

import json
import os
from pathlib import Path
import pty
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parent.parent
JOBGATE_PATH = os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"])
BOAT_INSTALL = "curl -fsSL https://boat.dev/install | sh"
FAKE_CURL = r"""#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_CURL_LOG"
[ -z "$FAKE_CURL_FAIL" ] || exit 22
cat <<'EOF'
mkdir -p "$HOME/.local/bin"
printf '#!/bin/sh\n' > "$HOME/.local/bin/boat"
chmod +x "$HOME/.local/bin/boat"
if [ -n "$FAKE_BOAT_INTERRUPT" ]; then kill -INT 0; sleep 1; fi
exit "${FAKE_BOAT_STATUS:-0}"
EOF
"""


class InstallerFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="astack test ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "checkout with spaces"
        shutil.copytree(SOURCE, self.repo, ignore=shutil.ignore_patterns(".git", ".cache", "__pycache__", "node_modules"))
        self.home = self.root / "test home"
        # The user's growing skill collection is not part of this test fixture.
        shutil.rmtree(self.repo / "skills")
        (self.repo / "settings/plugins.json").unlink()
        self.skill = self.repo / "skills/test-skill"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Exercise the installer in isolation.\n---\n\nTest instructions.\n"
        )
        helper = self.skill / "scripts/helper.sh"
        helper.parent.mkdir()
        helper.write_text("#!/bin/sh\nexit 0\n")
        helper.chmod(0o755)

    def run_installer(self, *arguments, command="install", success=True, env=None, input=None):
        result = subprocess.run(
            [str(self.repo / (command + ".sh")), "--home", str(self.home), *arguments],
            cwd=str(self.root), capture_output=True, text=True, env=env, input=input,
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
            (".gemini/config", "GEMINI.md", ".gemini/config/skills"),
        ]:
            self.assertIn("Personal coding instructions", (self.home / config / filename).read_text())
            self.assertEqual((self.home / skills / "test-skill/SKILL.md").read_bytes(),
                             (self.skill / "SKILL.md").read_bytes())
            self.assertTrue(os.access(self.home / skills / "test-skill/scripts/helper.sh", os.X_OK))
        self.assertFalse(list(self.home.rglob("replace-with-skill-name")))
        self.assertEqual(set(self.manifest()["targets"]), {"claude", "codex", "antigravity", "jobgate"})

    def test_dependency_cache_is_not_installed_or_validated_as_skill_source(self):
        cache = self.skill / "scripts/node_modules"
        cache.mkdir()
        (cache / "README.md").write_text("[dependency link](missing.md)\n")
        (cache / "bin").symlink_to("missing-executable")
        (self.skill / "scripts/package.json").write_text('{"private": true}\n')
        self.run_installer()
        installed = self.home / ".agents/skills/test-skill/scripts"
        self.assertTrue((installed / "package.json").exists())
        self.assertFalse((installed / "node_modules").exists())
        self.assertFalse(any("node_modules" in path for path in self.manifest()["targets"]["codex"]))

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
        self.assertEqual(set(self.manifest()["targets"]), {"claude", "codex", "antigravity", "jobgate"})

    def test_shared_and_specific_instruction_updates(self):
        self.run_installer()
        with (self.repo / "instructions/AGENTS.md").open("a") as stream:
            stream.write("\nShared preference.\n")
        with (self.repo / "instructions/CLAUDE.md").open("a") as stream:
            stream.write("\nClaude preference.\n")
        with (self.repo / "instructions/ANTIGRAVITY.md").open("a") as stream:
            stream.write("\nAntigravity preference.\n")
        self.run_installer()
        claude = (self.home / ".claude/CLAUDE.md").read_text()
        codex = (self.home / ".codex/AGENTS.md").read_text()
        antigravity = (self.home / ".gemini/config/GEMINI.md").read_text()
        self.assertIn("Shared preference.", claude)
        self.assertIn("Shared preference.", codex)
        self.assertIn("Shared preference.", antigravity)
        self.assertIn("Claude preference.", claude)
        self.assertNotIn("Claude preference.", codex)
        self.assertNotIn("Claude preference.", antigravity)
        self.assertIn("Antigravity preference.", antigravity)
        self.assertNotIn("Antigravity preference.", claude)
        self.assertNotIn("Antigravity preference.", codex)

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
        self.assertFalse((self.home / ".gemini/config/skills/test-skill/SKILL.md").exists())

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

    def test_user_invoked_skill_gets_codex_policy(self):
        (self.skill / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Manual only.\ndisable-model-invocation: true\n---\n"
        )
        self.run_installer("--target", "codex")
        policy = self.home / ".agents/skills/test-skill/agents/openai.yaml"
        self.assertIn("allow_implicit_invocation: false", policy.read_text())
        self.run_installer(command="uninstall", *("--target", "codex"))
        self.assertFalse(policy.exists())

    def test_codex_policy_must_match_frontmatter(self):
        policy = self.skill / "agents/openai.yaml"
        policy.parent.mkdir()
        policy.write_text("policy:\n  allow_implicit_invocation: false\n")
        self.run_installer(success=False)
        self.assertFalse(self.home.exists())

    def test_broken_skill_link_fails_before_writes(self):
        (self.skill / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Links.\n---\n\nSee [steps](steps.md).\n"
        )
        self.run_installer(success=False)
        self.assertFalse(self.home.exists())

    def test_home_ignores_environment_overrides(self):
        outside = self.root / "outside"
        env = dict(os.environ, CODEX_HOME=str(outside), CLAUDE_CONFIG_DIR=str(outside),
                   XDG_STATE_HOME=str(outside))
        self.run_installer("--target", "all", env=env)
        self.assertFalse(outside.exists())
        self.assertTrue((self.home / ".codex/AGENTS.md").exists())

    def jobgate_status_env(self):
        fake_bin = self.root / "fake bin"
        fake_bin.mkdir()
        (fake_bin / "systemctl").write_text("#!/bin/sh\nexit 1\n")
        (fake_bin / "systemctl").chmod(0o755)
        machine = self.root / "machine.json"
        machine.write_text('{"memory": 8589934592, "cores": 4, "psi": "full avg10=0.00"}')
        return dict(os.environ, SHELL="/bin/zsh", PATH=os.pathsep.join([str(fake_bin), JOBGATE_PATH]),
                    JOBGATE_MACHINE_FILE=str(machine), JOBGATE_HOME=str(self.home))

    def test_jobgate_goes_on_path_and_uninstall_removes_what_it_added(self):
        env = self.jobgate_status_env()
        self.run_installer("--target", "jobgate", env=env)
        jobgate = self.home / ".local/bin/jobgate"
        startup = self.home / ".zshrc"
        shell_env = dict(env, HOME=str(self.home))
        result = subprocess.run(["sh", "-c", '. "$1"; command -v jobgate; jobgate status', "sh", str(startup)],
                                capture_output=True, text=True, cwd=str(self.root), env=shell_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], str(jobgate.resolve()))
        self.assertIn("4.0G", result.stdout)
        login = self.home / ".zprofile"
        self.assertIn(str(jobgate.parent), login.read_text())

        self.run_installer("--target", "jobgate", command="uninstall", env=env)
        self.assertFalse(jobgate.exists())
        self.assertFalse((self.home / ".local/share/astack").exists())
        self.assertFalse(startup.exists())
        self.assertFalse(login.exists())

    def test_jobgate_adds_a_path_line_only_when_the_directory_is_missing_from_path(self):
        jobgate = self.home / ".local/bin/jobgate"
        env = dict(os.environ, SHELL="/bin/zsh", PATH=os.pathsep.join([str(jobgate.parent), JOBGATE_PATH]))
        self.run_installer("--target", "jobgate", env=env)
        self.assertTrue(jobgate.exists())
        self.assertFalse((self.home / ".zshrc").exists())
        self.run_installer("--target", "jobgate", command="uninstall", env=env)
        self.run_installer("--target", "jobgate", env=dict(env, PATH=JOBGATE_PATH))
        self.assertIn(str(jobgate.parent), (self.home / ".zshrc").read_text())

    def test_installed_jobgate_reads_toml_with_its_vendored_copy(self):
        env = self.jobgate_status_env()
        self.run_installer("--target", "jobgate", env=env)
        without_tomllib = self.root / "no stdlib tomllib"
        without_tomllib.mkdir()
        (without_tomllib / "tomllib.py").write_text("raise ModuleNotFoundError(\"No module named 'tomllib'\")\n")
        (self.home / ".config").mkdir(parents=True)
        (self.home / ".config/jobgate.toml").write_text("memory_share = 0.25\n")
        result = subprocess.run([str(self.home / ".local/bin/jobgate"), "status"], capture_output=True, text=True,
                                cwd=str(self.root), env=dict(env, HOME=str(self.home), PYTHONPATH=str(without_tomllib)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2.0G (25% of 8.0G", result.stdout)

    def test_bash_login_shell_that_skips_bashrc_finds_jobgate(self):
        env = dict(os.environ, SHELL="/bin/bash", PATH=JOBGATE_PATH)
        self.home.mkdir(parents=True)
        profile = self.home / ".bash_profile"
        profile.write_text("export EDITOR=vi\n")
        self.run_installer("--target", "jobgate", env=env)
        jobgate = str((self.home / ".local/bin/jobgate").resolve())
        for flags in (["--login"], ["-i"]):
            with self.subTest(flags=flags):
                found = subprocess.run(["bash", *flags, "-c", "command -v jobgate"], capture_output=True, text=True,
                                       stdin=subprocess.DEVNULL, env=dict(env, HOME=str(self.home)))
                self.assertEqual(found.stdout.strip().splitlines()[-1:], [jobgate], found.stderr)
        self.run_installer("--target", "jobgate", command="uninstall", env=env)
        self.assertEqual(profile.read_text(), "export EDITOR=vi\n")
        self.assertFalse((self.home / ".bashrc").exists())

    def test_jobgate_uninstall_keeps_a_marked_block_it_did_not_write(self):
        env = dict(os.environ, SHELL="/bin/zsh", PATH=JOBGATE_PATH)
        self.home.mkdir(parents=True)
        own = '# astack: jobgate on PATH\nexport PATH="{}:$PATH"\nexport EDITOR=vi\n'.format(self.home / ".local/bin")
        startup = self.home / ".zshrc"
        startup.write_text(own)
        self.run_installer("--target", "jobgate", env=env)
        self.assertEqual(startup.read_text().count("# astack: jobgate on PATH"), 2)
        self.run_installer("--target", "jobgate", command="uninstall", env=env)
        self.assertEqual(startup.read_text(), own)

    def test_jobgate_uninstall_after_an_interruption_keeps_a_marked_block_it_did_not_write(self):
        env = dict(os.environ, SHELL="/bin/zsh", PATH=JOBGATE_PATH)
        self.home.mkdir(parents=True)
        own = '# astack: jobgate on PATH\nexport PATH="{}:$PATH"\nexport EDITOR=vi\n'.format(self.home / ".local/bin")
        startup = self.home / ".zshrc"
        startup.write_text(own)
        self.run_installer("--target", "jobgate", env=env)
        startup.write_text(own)
        self.run_installer("--target", "jobgate", command="uninstall", env=env)
        self.assertEqual(startup.read_text(), own)

    def test_jobgate_uninstall_removes_the_path_line_after_a_failed_removal(self):
        if os.geteuid() == 0:
            self.skipTest("root can rewrite a read-only home")
        env = dict(os.environ, SHELL="/bin/zsh", PATH=JOBGATE_PATH)
        startup = self.home / ".zshrc"
        self.home.mkdir(parents=True)
        startup.write_text("alias ll='ls -l'\n")
        self.run_installer("--target", "jobgate", env=env)
        self.home.chmod(0o555)
        try:
            self.run_installer("--target", "jobgate", command="uninstall", env=env)
        finally:
            self.home.chmod(0o755)
        self.assertIn(".local/bin:$PATH", startup.read_text())
        self.run_installer("--target", "jobgate", command="uninstall", env=env)
        self.assertEqual(startup.read_text(), "alias ll='ls -l'\n")

    def test_destination_directory_is_not_replaced(self):
        blocker = self.home / ".codex/AGENTS.md"
        blocker.mkdir(parents=True)
        self.run_installer("--force", success=False)
        self.assertTrue(blocker.is_dir())
        self.assertFalse((self.home / ".claude").exists())

    def test_overlapping_target_destinations_are_rejected(self):
        registry = self.repo / "harnesses.json"
        harnesses = json.loads(registry.read_text())
        harnesses["claude"]["skills"] = harnesses["codex"]["skills"]
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


class BoatOfferTests(InstallerFixture):
    def setUp(self):
        super().setUp()
        self.fake_bin = self.root / "fake bin"
        self.fake_bin.mkdir()
        (self.fake_bin / "curl").write_text(FAKE_CURL)
        (self.fake_bin / "curl").chmod(0o755)
        python_bin = self.root / "python bin"
        python_bin.mkdir()
        (python_bin / "python3").write_text('#!/bin/sh\nexec {} "$@"\n'.format(shlex.quote(sys.executable)))
        (python_bin / "python3").chmod(0o755)
        self.curl_log = self.root / "curl.log"
        self.process_home = self.root / "process home"
        path = os.pathsep.join([str(self.fake_bin), str(python_bin), "/usr/bin", "/bin"])
        self.env = dict(os.environ, SHELL="/bin/zsh", PATH=path, FAKE_CURL_LOG=str(self.curl_log),
                        HOME=str(self.process_home))
        for name in ("FAKE_BOAT_STATUS", "FAKE_CURL_FAIL", "FAKE_BOAT_INTERRUPT"):
            self.env.pop(name, None)

    def on_terminal(self, answer, *arguments, at_question=lambda process: None, **env):
        primary, secondary = pty.openpty()
        process = subprocess.Popen([str(self.repo / "install.sh"), "--home", str(self.home), *arguments],
                                   stdin=secondary, stdout=secondary, stderr=secondary, cwd=str(self.root),
                                   env=dict(self.env, **env), start_new_session=True)
        os.close(secondary)
        output, asked = b"", 0
        try:
            while True:
                ready, _, _ = select.select([primary], [], [], 60)
                self.assertTrue(ready, "the installer printed nothing for 60 seconds:\n" + output.decode())
                try:
                    chunk = os.read(primary, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
                if output.count(b"[y/N] ") > asked:
                    at_question(process)
                    os.write(primary, answer.encode() + b"\n")
                    asked += 1
            process.wait(timeout=60)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(primary)
        text = output.decode().replace("\r\n", "\n")
        self.assertEqual(process.returncode, 0, text)
        return text, asked

    def boat_lines(self, output):
        lines = output.splitlines()
        start = next((i for i, line in enumerate(lines) if line.startswith("OPTIONAL boat")), len(lines))
        return lines[start:start + 2]

    def test_on_a_terminal_only_yes_runs_boats_installer_in_the_installed_home(self):
        for answer in ("", "n", "no", "maybe"):
            with self.subTest(answer=answer):
                output, asked = self.on_terminal(answer, "--target", "jobgate")
                self.assertEqual(asked, 1, output)
                self.assertFalse(self.curl_log.exists())

        output, asked = self.on_terminal("y", "--target", "jobgate")
        self.assertEqual(asked, 1, output)
        self.assertNotRegex(output, r"(?m)^WARN ")
        self.assertEqual(self.curl_log.read_text(), "-fsSL https://boat.dev/install\n")
        self.assertTrue(os.access(self.home / ".local/bin/boat", os.X_OK))
        self.assertFalse(self.process_home.exists(), "boat's installer ran in the installer's HOME, not the installed home")

        output, asked = self.on_terminal("y", "--target", "jobgate")
        self.assertEqual(asked, 0, output)
        self.assertEqual(self.boat_lines(output), [])

    def test_a_preview_or_an_install_without_a_terminal_prints_boats_command_and_asks_nothing(self):
        output, asked = self.on_terminal("y", "--target", "jobgate", "--dry-run")
        self.assertEqual(asked, 0, output)
        lines = self.boat_lines(output)
        self.assertEqual(lines[1].strip(), BOAT_INSTALL)

        output = self.run_installer("--target", "jobgate", env=self.env, input="y\n").stdout
        lines = self.boat_lines(output)
        self.assertEqual(lines[1].strip(), BOAT_INSTALL)
        self.assertNotIn("[y/N]", output)
        self.assertFalse(self.curl_log.exists())
        (doc, anchor), = re.findall(r"\((docs/[\w.-]+\.md)#([\w-]+)\)", lines[0])
        headings = re.findall(r"^#+ (.+)$", (self.repo / doc).read_text(), re.MULTILINE)
        self.assertIn(anchor, {heading.lower().replace(" ", "-") for heading in headings})

    def test_boat_on_path_or_an_install_without_jobgate_gets_no_boat_line(self):
        self.assertNotEqual(self.boat_lines(self.run_installer("--target", "jobgate", env=self.env).stdout), [])
        self.assertEqual(self.boat_lines(self.run_installer("--target", "claude", env=self.env).stdout), [])

        (self.fake_bin / "boat").write_text("#!/bin/sh\n")
        (self.fake_bin / "boat").chmod(0o755)
        self.assertEqual(self.boat_lines(self.run_installer("--target", "jobgate", env=self.env).stdout), [])

    def test_the_question_waits_without_holding_the_claude_router_lock(self):
        sys.path.insert(0, str(SOURCE / "scripts"))
        import claude_router
        state_dir = self.root / "router state"
        state_dir.mkdir()
        manifest = self.home / ".local/state/astack/manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"version": 3, "targets": {},
                                        "components": {"claude-router": {"layout": {"state": str(state_dir)}}}}))
        held = []

        def take_lock(process=None):
            try:
                claude_router.ActivationLock(state_dir).close()
            except claude_router.Refusal:
                held.append("held")

        with claude_router.ActivationLock(state_dir):
            take_lock()
        self.assertEqual(held, ["held"])

        output, asked = self.on_terminal("n", "--target", "jobgate", at_question=take_lock)
        self.assertEqual(asked, 1, output)
        self.assertEqual(held, ["held"])

    def warning(self, output):
        lines = [line for line in output.splitlines() if line.startswith("WARN boat")]
        self.assertEqual(len(lines), 1, output)
        return lines[0]

    def test_a_failed_boat_install_warns_and_the_install_still_succeeds(self):
        output, asked = self.on_terminal("yes", "--target", "jobgate", FAKE_CURL_FAIL="1")
        self.assertEqual(asked, 1, output)
        self.assertTrue(self.warning(output).endswith(BOAT_INSTALL), output)
        self.assertFalse((self.home / ".local/bin/boat").exists())
        self.assertTrue((self.home / ".local/bin/jobgate").exists())

    def test_a_failed_or_interrupted_sign_in_after_boat_installs_names_boat_login(self):
        for env in ({"FAKE_BOAT_STATUS": "3"}, {"FAKE_BOAT_INTERRUPT": "1"}):
            with self.subTest(env=env):
                shutil.rmtree(self.home, ignore_errors=True)
                output, asked = self.on_terminal("y", "--target", "jobgate", **env)
                self.assertEqual(asked, 1, output)
                self.assertTrue(self.warning(output).endswith("boat login"), output)
                self.assertNotIn("Traceback", output)

    def test_ctrl_c_at_the_question_declines_it(self):
        output, asked = self.on_terminal("y", "--target", "jobgate",
                                         at_question=lambda process: os.killpg(process.pid, signal.SIGINT))
        self.assertEqual(asked, 1, output)
        self.assertNotIn("Traceback", output)
        self.assertFalse(self.curl_log.exists())
        self.assertTrue((self.home / ".local/bin/jobgate").exists())


if __name__ == "__main__":
    unittest.main()
