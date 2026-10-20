"""Lifecycle of the optional claude-router component in temporary homes.

A fake `go` on PATH stands in for the toolchain: it reports a version and
platform, and its `build` writes a shell script that logs its arguments. No
test starts a service manager or the real router.
"""

from contextlib import redirect_stdout
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch

from test_install import InstallerFixture

FAKE_GO = r'''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
if args[0] == "env":
    machine = {"x86_64": "amd64", "aarch64": "arm64"}.get(os.uname().machine.lower(), os.uname().machine.lower())
    host = (sys.platform, machine)
    print(os.environ.get("FAKE_GOVERSION", "go1.26.8"))
    print(os.environ.get("FAKE_GOOS", host[0]))
    print(os.environ.get("FAKE_GOARCH", host[1]))
    print(host[0])
    print(host[1])
elif args[0] == "build":
    if os.environ.get("FAKE_BUILD_FAIL"):
        sys.stderr.write("cmd/claude-router/main.go:1:1: injected build error\n")
        sys.exit(1)
    out = args[args.index("-o") + 1]
    workspace = os.environ.get("GOWORK")
    if workspace is None:
        here = os.getcwd()
        while workspace is None and here != os.path.dirname(here):
            if os.path.exists(os.path.join(here, "go.work")):
                workspace = os.path.join(here, "go.work")
            here = os.path.dirname(here)
    with open(out, "w") as stream:
        stream.write("#!/bin/sh\n# build {}\n".format(os.environ.get("FAKE_BUILD", "1")))
        if workspace not in (None, "", "off"):
            stream.write("# workspace {}\n".format(workspace))
        stream.write("printf '%s\\n' \"$*\" >> \"$FAKE_ROUTER_LOG\"\n")
    os.chmod(out, 0o755)
'''

KILLED_AT_TOKEN_RENAME = r'''
import os, runpy, sys
rename = os.replace
def replace(source, destination, **options):
    if os.path.basename(str(destination)) == "client-token":
        os._exit(137)
    rename(source, destination, **options)
os.replace = replace
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
'''

class ComponentFixture(InstallerFixture):
    def setUp(self):
        super().setUp()
        bin_dir = self.root / "fake bin"
        bin_dir.mkdir()
        go = bin_dir / "go"
        go.write_text(FAKE_GO)
        go.chmod(0o755)
        self.router_log = self.root / "router.log"
        self.env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
                        FAKE_ROUTER_LOG=str(self.router_log))
        for name in ("FAKE_GOVERSION", "FAKE_GOOS", "FAKE_GOARCH", "FAKE_BUILD", "FAKE_BUILD_FAIL"):
            self.env.pop(name, None)
        self.manager_name = "launchd" if sys.platform == "darwin" else "systemd"

    def component(self, *arguments, command="install", success=True, **env):
        return self.run_installer("--component", "claude-router", *arguments, command=command,
                                  success=success, env=dict(self.env, **env))

    @property
    def data(self):
        return self.home.resolve() / ".local/share/astack/claude-router"

    @property
    def state_dir(self):
        return self.home.resolve() / ".local/state/astack/claude-router"

    @property
    def descriptor(self):
        if self.manager_name == "launchd":
            return self.home.resolve() / "Library/LaunchAgents/dev.astack.claude-router.plist"
        return self.home.resolve() / ".config/systemd/user/dev.astack.claude-router.service"

    def inventory(self):
        return {str(p.relative_to(self.home)): (p.read_bytes(), stat.S_IMODE(p.stat().st_mode), p.stat().st_mtime_ns)
                for p in self.home.rglob("*") if p.is_file() and p.name not in ("manifest.json", "activation.lock")}

    def module(self):
        sys.modules.pop("claude_router", None)
        spec = importlib.util.spec_from_file_location("astack_manager", self.repo / "scripts/manage.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def run_module(self, module, env, *arguments):
        with patch.object(module.Path, "home", return_value=self.home), \
                patch.dict(module.os.environ, dict(self.env, **env), clear=True), \
                patch.object(module.sys, "argv", ["manage.py", *arguments]), \
                redirect_stdout(io.StringIO()):
            module.main()

    def router(self):
        spec = importlib.util.spec_from_file_location("router_helper", self.repo / "scripts/claude_router.py")
        router = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(router)
        return router

    def activate(self, settings_path):
        router = self.router()
        current = json.loads(settings_path.read_text()) if settings_path.exists() else {}
        values, replaced = router.client_settings(self.state_dir, current)
        state_path = self.home / ".local/state/astack/manifest.json"
        state = json.loads(state_path.read_text())
        change, = router.astack_settings.prepare("claude-router", {}, {str(settings_path): ("json", values)})
        with redirect_stdout(io.StringIO()):
            change.apply(state, state_path, self.root / "unused backups")
        return replaced

    def commit_checkout(self, message="checkout"):
        if not (self.repo / ".git").exists():
            subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        git = ["git", "-C", str(self.repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
               "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", message], check=True)

    def code_only(self):
        result = subprocess.run([sys.executable, str(self.repo / "scripts/claude_router.py"), "identity", "--code-only"],
                                capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def enroll(self):
        """Give the router a login and an assignment, as login and a request would."""
        auths = self.state_dir / "auths"
        auths.mkdir(parents=True)
        (auths / "work.json").write_text('{"type": "claude", "access_token": "credential-secret-value"}\n')
        journal = self.state_dir / "assignments/assignments.jsonl"
        journal.parent.mkdir()
        journal.write_text('{"op":"assign","c":"session-1","a":"work","why":"capacity","t":"2026-10-15T00:00:00Z"}\n'
                           '{"op":"served","c":"session-1","a":"work","why":"capacity","t":"2026-10-15T00:00:01Z"}\n'
                           '{"op":"assign","c":"session-2","a":"work","why":"capacity","t":"2026-10-15T00:00:02Z"}\n')


class ComponentSelectionTests(ComponentFixture):
    def test_default_and_all_targets_never_install_the_component(self):
        self.run_installer(env=self.env)
        self.run_installer("--target", "all", env=self.env)
        self.assertFalse(self.data.exists())
        self.assertFalse(self.state_dir.exists())
        self.assertFalse(self.descriptor.exists())
        self.assertNotIn("components", self.manifest())

    def test_component_alone_installs_service_files_and_no_client_settings(self):
        result = self.component()
        self.assertIn("Install complete for claude-router.", result.stdout)
        binary = self.data / "bin/claude-router"
        subprocess.run([str(binary), "status"], check=True, env=self.env)
        self.assertEqual(self.router_log.read_text(), "status\n")
        descriptor = self.descriptor.read_text()
        self.assertIn(str(binary), descriptor)
        self.assertIn("-supervised", descriptor)
        self.assertIn(str(self.state_dir / "config.json"), descriptor)
        config = json.loads((self.state_dir / "config.json").read_text())
        self.assertEqual(config, {"listen": "127.0.0.1:18787", "state_dir": str(self.state_dir),
                                  "client_token_file": str(self.state_dir / "client-token"), "accounts": []})
        token = self.state_dir / "client-token"
        self.assertRegex(token.read_text(), r"^[0-9a-f]{64}\n$")
        self.assertEqual(stat.S_IMODE(token.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.state_dir.stat().st_mode), 0o700)
        record = json.loads((self.state_dir / "service.json").read_text())
        self.assertEqual(record["manager"], self.manager_name)
        self.assertEqual(record["name"], "dev.astack.claude-router")
        self.assertEqual(record["helper"], str(self.data / "lib/claude_router.py"))
        helper = subprocess.run([record["python"], record["helper"], "direct", "--prepare", "--state", str(self.state_dir),
                                 "--manifest", record["manifest"]], capture_output=True, text=True, env=self.env)
        self.assertEqual((helper.returncode, helper.stdout), (0, "keep    no client settings name the router; router logins none; 0 assignments\n"
                                                                "ready   nothing to change\n"), helper.stderr)
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".codex").exists())
        self.assertNotIn("settings", self.manifest())

    def test_component_and_targets_install_together(self):
        result = self.component("--target", "claude")
        self.assertIn("Install complete for claude, claude-router.", result.stdout)
        self.assertTrue((self.home / ".claude/CLAUDE.md").exists())
        self.assertTrue((self.data / "bin/claude-router").exists())
        settings = json.loads((self.home / ".claude/settings.json").read_text())
        self.assertNotIn("env", settings)

    def test_preview_writes_nothing(self):
        result = self.component("--dry-run")
        self.assertIn("WRITE {}".format(self.data / "bin/claude-router"), result.stdout)
        self.assertIn("CREATE {}".format(self.state_dir / "config.json"), result.stdout)
        self.assertFalse(self.home.exists())

    def test_a_preview_over_an_install_neither_creates_nor_takes_the_activation_lock(self):
        self.component()
        lock = self.state_dir / "activation.lock"
        commands = {"install.sh": [str(self.repo / "install.sh"), "--component", "claude-router"],
                    "claude_router.py": [sys.executable, str(self.repo / "scripts/claude_router.py"), "install"]}
        previews = {name: lambda command=command: subprocess.run(
            command + ["--home", str(self.home), "--dry-run"], capture_output=True, text=True, env=self.env)
            for name, command in commands.items()}

        exits = [preview().returncode for preview in previews.values()]
        self.assertEqual((exits, lock.exists()), ([0, 0], False))
        fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        for name, preview in previews.items():
            with self.subTest(name):
                result = preview()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("KEEP {}".format(self.data / "bin/claude-router"), result.stdout)

    def test_repeat_install_changes_nothing(self):
        self.component()
        before = self.inventory()
        result = self.component()
        self.assertNotIn("WRITE", result.stdout)
        self.assertNotIn("CREATE", result.stdout)
        self.assertEqual(self.inventory(), before)

    def test_update_replaces_the_binary_and_keeps_user_config_and_state(self):
        self.component()
        config = self.state_dir / "config.json"
        edited = json.loads(config.read_text())
        edited["accounts"] = [{"id": "work", "capacity": 5}]
        config.write_text(json.dumps(edited))
        self.enroll()
        token = (self.state_dir / "client-token").read_text()
        result = self.component(FAKE_BUILD="2")
        self.assertIn("WRITE {}".format(self.data / "bin/claude-router"), result.stdout)
        self.assertIn("# build 2", (self.data / "bin/claude-router").read_text())
        self.assertEqual(json.loads(config.read_text())["accounts"], [{"id": "work", "capacity": 5}])
        self.assertEqual((self.state_dir / "client-token").read_text(), token)
        self.assertTrue((self.state_dir / "auths/work.json").exists())


class ShellPathTests(ComponentFixture):
    def bash_rc(self):
        return self.home / (".bash_profile" if sys.platform == "darwin" else ".bashrc")

    def bin_dir(self):
        return self.data / "bin"

    def block(self):
        return '# astack: claude-router on PATH\nexport PATH="{}:$PATH"\n'.format(self.bin_dir())

    def on_path(self, rc):
        result = subprocess.run(["sh", "-c", '. "$1"; command -v claude-router', "sh", str(rc)],
                                capture_output=True, text=True, env=dict(self.env, HOME=str(self.home)))
        return result.stdout.strip()

    def test_install_puts_the_router_on_path_in_a_new_shell_file_and_uninstall_removes_it(self):
        rc = self.bash_rc()

        result = self.component(SHELL="/bin/bash")

        self.assertIn("APPEND {}".format(rc), result.stdout)
        self.assertEqual(rc.read_text(), self.block())
        self.assertEqual(self.on_path(rc), str(self.bin_dir() / "claude-router"))
        self.component(command="uninstall", SHELL="/bin/bash")
        self.assertFalse(rc.exists())

    def test_an_existing_shell_file_keeps_its_lines_and_a_repeat_install_adds_nothing(self):
        rc = self.bash_rc()
        rc.parent.mkdir(parents=True)
        rc.write_text("alias ll='ls -l'")
        rc.chmod(0o600)

        self.component(SHELL="/bin/bash")
        once = rc.read_text()
        repeat = self.component(SHELL="/bin/bash")

        self.assertEqual(once, "alias ll='ls -l'\n" + self.block())
        self.assertEqual(rc.read_text(), once)
        self.assertNotIn("APPEND", repeat.stdout)
        self.assertEqual(stat.S_IMODE(rc.stat().st_mode), 0o600)
        self.component(command="uninstall", SHELL="/bin/bash")
        self.assertEqual(rc.read_text(), "alias ll='ls -l'")

    def test_a_shell_file_that_already_lists_the_router_stays_as_it_is_through_uninstall(self):
        rc = self.home / ".zshrc"
        rc.parent.mkdir(parents=True)
        line = 'export PATH="${XDG_DATA_HOME:-$HOME/.local/share}/astack/claude-router/bin:$PATH"\n'
        rc.write_text(line)

        result = self.component(SHELL="/usr/bin/zsh")
        self.component(command="uninstall", SHELL="/usr/bin/zsh")

        self.assertIn("KEEP {} (already puts claude-router on PATH)".format(rc), result.stdout)
        self.assertEqual(rc.read_text(), line)

    def test_another_shell_gets_no_file_and_the_line_to_add(self):
        result = self.component(SHELL="/usr/bin/fish")

        self.assertIn("SKIP PATH (no shell file known for SHELL=/usr/bin/fish", result.stdout)
        self.assertEqual([p.name for p in self.home.iterdir() if p.name.startswith(".") and p.is_file()], [])

    def test_the_shell_file_keeps_its_exact_bytes_through_install_and_uninstall(self):
        rc = self.bash_rc()
        rc.parent.mkdir(parents=True)
        original = b"export CONTROL='\r'\nexport LANG=C"
        rc.write_bytes(original)

        self.component(SHELL="/bin/bash")
        installed = rc.read_bytes()
        self.component(command="uninstall", SHELL="/bin/bash")

        self.assertEqual(installed, original + b"\n" + self.block().encode())
        self.assertEqual(rc.read_bytes(), original)

    def test_lines_added_below_the_path_lines_stay_separate_after_uninstall(self):
        rc = self.bash_rc()
        rc.parent.mkdir(parents=True)
        rc.write_bytes(b"alias a=b")
        self.component(SHELL="/bin/bash")
        with open(rc, "ab") as stream:
            stream.write(b"export Z=1\n")

        self.component(command="uninstall", SHELL="/bin/bash")

        self.assertEqual(rc.read_bytes(), b"alias a=b\nexport Z=1\n")

    def test_an_edit_saved_while_uninstall_removes_the_lines_is_kept(self):
        rc = self.bash_rc()
        self.component(SHELL="/bin/bash")
        router = self.module().claude_router
        read = router.Path.read_bytes
        edited = []

        def read_then_edit(path):
            data = read(path)
            if path == rc and not edited:
                edited.append(True)
                with open(rc, "ab") as stream:
                    stream.write(b"export EDITOR=vi\n")
            return data

        change = router.ShellChange(router.ShellAction.REMOVE, rc, tuple(self.block().splitlines()))
        with patch.object(router.Path, "read_bytes", read_then_edit), redirect_stdout(io.StringIO()):
            router.apply_shell(change, {"shell_path": {"created": True}}, dry_run=False)

        self.assertEqual(rc.read_bytes(), b"export EDITOR=vi\n")

    def test_uninstall_after_the_shell_file_was_deleted_says_nothing_about_it(self):
        self.component(SHELL="/bin/bash")
        self.bash_rc().unlink()

        result = self.component(command="uninstall", SHELL="/bin/bash")

        self.assertNotIn(str(self.bash_rc()), result.stdout)
        self.assertFalse(self.bash_rc().exists())

    def test_a_shell_file_that_is_not_utf8_gets_the_line(self):
        rc = self.bash_rc()
        rc.parent.mkdir(parents=True)
        rc.write_bytes(b"export NAME='\xff'\n")

        self.component(SHELL="/bin/bash")

        self.assertEqual(rc.read_bytes(), b"export NAME='\xff'\n" + self.block().encode())

    @unittest.skipIf(os.geteuid() == 0, "root writes files whatever their mode")
    def test_uninstall_restores_client_settings_when_the_shell_file_cannot_change(self):
        self.run_installer("--target", "claude", env=self.env)
        target = self.home / "dotfiles/bashrc"
        target.parent.mkdir(parents=True)
        target.write_text("set -o vi\n")
        self.bash_rc().symlink_to(target)
        self.component(SHELL="/bin/bash")
        settings_path = self.home / ".claude/settings.json"
        before = settings_path.read_bytes()
        self.activate(settings_path)
        target.parent.chmod(0o500)
        target.chmod(0o400)
        self.addCleanup(target.parent.chmod, 0o700)

        result = self.component(command="uninstall", SHELL="/bin/bash")

        self.assertEqual(settings_path.read_bytes(), before)
        self.assertIn("WARN {}".format(self.bash_rc()), result.stdout)
        self.assertIn(" / ".join(self.block().splitlines()), result.stdout)

    def test_a_zsh_user_gets_the_line_in_zshrc(self):
        self.component(SHELL="/bin/zsh")

        self.assertEqual((self.home / ".zshrc").read_text(), self.block())
        self.assertFalse((self.bash_rc()).exists())

    def test_a_commented_out_line_does_not_count_as_on_path(self):
        rc = self.bash_rc()
        rc.parent.mkdir(parents=True)
        rc.write_text('# export PATH="$HOME/.local/share/astack/claude-router/bin:$PATH"\n')

        self.component(SHELL="/bin/bash")

        self.assertTrue(rc.read_text().endswith(self.block()))

    def test_zdotdir_names_the_zsh_file_unless_home_is_given(self):
        router = self.router()
        zdotdir = self.root / "zsh config"
        with patch.dict(router.os.environ, {"SHELL": "/bin/zsh", "ZDOTDIR": str(zdotdir)}):
            self.assertEqual(router.shell_file_for(self.home, True), zdotdir / ".zshrc")
            self.assertEqual(router.shell_file_for(self.home, False), self.home / ".zshrc")

        self.component(SHELL="/bin/zsh", ZDOTDIR=str(zdotdir))

        self.assertEqual((self.home / ".zshrc").read_text(), self.block())
        self.assertFalse(zdotdir.exists())

    def test_bash_on_macos_writes_the_login_file_bash_reads(self):
        router = self.router()
        self.home.mkdir(parents=True)
        (self.home / ".profile").write_text("export EDITOR=vi\n")
        with patch.object(router.sys, "platform", "darwin"), patch.dict(router.os.environ, {"SHELL": "/bin/bash"}):
            self.assertEqual(router.shell_file_for(self.home, False), self.home / ".profile")
            (self.home / ".bash_profile").write_text("")
            self.assertEqual(router.shell_file_for(self.home, False), self.home / ".bash_profile")

    def test_ctrl_c_at_the_question_writes_nothing_and_ctrl_d_takes_the_default(self):
        module = self.module()
        with patch.object(module.claude_router, "on_terminal", return_value=True), \
                patch("builtins.input", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.run_module(module, {"SHELL": "/bin/bash"}, "install", "--home", str(self.home), "--component", "claude-router")
        self.assertFalse((self.data / "bin/claude-router").exists())
        self.assertFalse(self.bash_rc().exists())

        module = self.module()
        with patch.object(module.claude_router, "on_terminal", return_value=True), patch("builtins.input", side_effect=EOFError):
            self.run_module(module, {"SHELL": "/bin/bash"}, "install", "--home", str(self.home), "--component", "claude-router")
        self.assertEqual(self.bash_rc().read_text(), self.block())

    def test_a_symlinked_shell_file_stays_a_link_to_the_file_that_gets_the_line(self):
        target = self.home / "dotfiles/bashrc"
        target.parent.mkdir(parents=True)
        target.write_text("set -o vi\n")
        rc = self.bash_rc()
        rc.symlink_to(target)

        self.component(SHELL="/bin/bash")

        self.assertTrue(rc.is_symlink())
        self.assertEqual(target.read_text(), "set -o vi\n" + self.block())

    def test_a_preview_asks_nothing_and_writes_no_shell_file(self):
        result = self.component("--dry-run", SHELL="/bin/bash")

        self.assertIn("APPEND {}".format(self.bash_rc()), result.stdout)
        self.assertFalse(self.home.exists())

    def test_on_a_terminal_the_installer_asks_and_takes_yes_by_default(self):
        for answer, added in (("", True), ("y", True), ("n", False), ("no", False)):
            with self.subTest(answer=answer):
                rc = self.bash_rc()
                if rc.exists():
                    rc.unlink()
                module = self.module()
                questions = []
                with patch.object(module.claude_router, "on_terminal", return_value=True), \
                        patch("builtins.input", side_effect=lambda q: questions.append(q) or answer):
                    self.run_module(module, {"SHELL": "/bin/bash"}, "install", "--home", str(self.home), "--component", "claude-router")
                self.assertEqual(questions, ["Add claude-router to PATH in {}? [Y/n] ".format(rc)])
                self.assertEqual(rc.exists(), added)
                if not added:
                    state = json.loads((self.home / ".local/state/astack/manifest.json").read_text())
                    self.assertNotIn("shell_path", state["components"]["claude-router"])
                self.component(command="uninstall", SHELL="/bin/bash")

    def test_a_declined_line_stays_out_at_later_installs_without_a_terminal(self):
        rc = self.bash_rc()
        module = self.module()
        with patch.object(module.claude_router, "on_terminal", return_value=True), patch("builtins.input", return_value="n"):
            self.run_module(module, {"SHELL": "/bin/bash"}, "install", "--home", str(self.home), "--component", "claude-router")

        later = self.component(SHELL="/bin/bash")

        self.assertFalse(rc.exists())
        self.assertIn("SKIP {} (declined at an earlier install".format(rc), later.stdout)


class TokenCreationTests(ComponentFixture):
    def test_an_install_killed_before_the_token_rename_leaves_none_and_the_next_one_cleans_up(self):
        token = self.state_dir / "client-token"
        killed = subprocess.run(
            [sys.executable, "-c", KILLED_AT_TOKEN_RENAME, str(self.repo / "scripts/manage.py"),
             "install", "--home", str(self.home), "--component", "claude-router"],
            cwd=str(self.root), capture_output=True, text=True, env=self.env)
        left = token.exists()
        strays = sorted(path.name for path in self.state_dir.glob(".client-token-*"))
        result = self.component()

        self.assertFalse(left)
        self.assertEqual(killed.returncode, 137, killed.stderr)
        self.assertEqual(len(strays), 1)
        self.assertIn("REMOVE {}".format(self.state_dir / strays[0]), result.stdout)
        self.assertIn("CREATE {}".format(token), result.stdout)
        self.assertRegex(token.read_text(), r"^[0-9a-f]{64}\n$")
        self.assertEqual(stat.S_IMODE(token.stat().st_mode), 0o600)
        self.assertEqual(list(self.state_dir.glob(".client-token-*")), [])

    def test_install_keeps_exactly_the_tokens_the_service_accepts(self):
        config_go = (self.repo / "components/claude-router/internal/claude/config.go").read_text()
        service_minimum = int(re.search(r"minClientTokenLength\s*=\s*(\d+)", config_go).group(1))
        token = self.state_dir / "client-token"
        self.component()

        token.write_text("x" * (service_minimum - 1) + "\n")
        refused = self.component()
        token.write_text("x" * service_minimum + "\n")
        accepted = self.component()

        self.assertIn("REPLACE {}".format(token), refused.stdout)
        self.assertNotIn("REPLACE", accepted.stdout)
        self.assertEqual(token.read_text(), "x" * service_minimum + "\n")

    def test_install_makes_a_usable_token_private(self):
        token = self.state_dir / "client-token"
        self.component()
        token.write_text("x" * 32 + "\n")
        token.chmod(0o644)

        result = self.component()

        self.assertIn("CHMOD {} 0600 (was 0644)".format(token), result.stdout)
        self.assertEqual(stat.S_IMODE(token.stat().st_mode), 0o600)
        self.assertEqual(token.read_text(), "x" * 32 + "\n")

    def test_install_refuses_a_token_path_that_is_a_directory_before_writing(self):
        token = self.state_dir / "client-token"
        token.mkdir(parents=True)

        result = self.component(success=False)

        self.assertIn("astack: {} is not a file; move it aside, then install again".format(token), result.stderr)
        self.assertTrue(token.is_dir())
        self.assertFalse((self.data / "bin/claude-router").exists())

    def test_install_replaces_a_token_the_router_would_refuse(self):
        token = self.state_dir / "client-token"
        self.component()
        for unusable in ("", "  \n", "x" * 31 + "\n"):
            with self.subTest(unusable=unusable):
                token.write_text(unusable)
                token.chmod(0o644)
                result = self.component()
                self.assertIn("REPLACE {} (fewer than 32 characters)".format(token), result.stdout)
                self.assertRegex(token.read_text(), r"^[0-9a-f]{64}\n$")
                self.assertEqual(stat.S_IMODE(token.stat().st_mode), 0o600)

    def test_install_keeps_a_usable_token_the_user_chose(self):
        token = self.state_dir / "client-token"
        self.component()
        token.write_text("x" * 32 + "\n")
        result = self.component()
        self.assertNotIn("REPLACE", result.stdout)
        self.assertEqual(token.read_text(), "x" * 32 + "\n")


class ComponentRemovalTests(ComponentFixture):
    def test_uninstall_stops_the_service_removes_payloads_and_keeps_state(self):
        self.component()
        self.enroll()
        config = self.state_dir / "config.json"
        config.write_text(config.read_text().replace('"accounts": []', '"accounts": [{"id": "work", "capacity": 1}]'))
        result = self.component(command="uninstall")
        self.assertEqual(self.router_log.read_text(), "stop -uninstall -state {}\n".format(self.state_dir))
        self.assertFalse(self.data.exists())
        self.assertFalse(self.descriptor.exists())
        self.assertFalse((self.state_dir / "service.json").exists())
        for kept in ("config.json", "client-token", "auths/work.json", "assignments/assignments.jsonl"):
            self.assertTrue((self.state_dir / kept).exists(), kept)
        self.assertIn("KEEP {}".format(config), result.stdout)
        self.assertNotIn("claude-router", self.manifest().get("components", {}))

    def test_uninstall_removes_an_unedited_config(self):
        self.component()
        self.component(command="uninstall")
        self.assertFalse((self.state_dir / "config.json").exists())
        self.assertTrue((self.state_dir / "client-token").exists())

    def test_uninstall_preview_and_uninstall_without_install_change_nothing(self):
        self.component(command="uninstall")
        self.assertFalse(self.home.exists())
        self.component()
        before = self.inventory()
        self.component("--dry-run", command="uninstall")
        self.assertEqual(self.inventory(), before)
        self.assertFalse(self.router_log.exists())

    def test_moved_checkout_keeps_the_payload_runnable_and_can_uninstall(self):
        self.component()
        moved = self.root / "moved checkout"
        self.repo.rename(moved)
        self.repo = moved
        subprocess.run([str(self.data / "bin/claude-router"), "status"], check=True, env=self.env)
        helper = subprocess.run([sys.executable, str(self.data / "lib/claude_router.py"), "direct", "--prepare",
                                 "--state", str(self.state_dir), "--manifest", str(self.home / ".local/state/astack/manifest.json")],
                                capture_output=True, text=True, env=self.env)
        self.assertEqual(helper.returncode, 0, helper.stderr)
        self.component(command="uninstall")
        self.assertFalse(self.data.exists())

    def test_uninstall_restores_router_client_settings_and_keeps_credentials(self):
        self.run_installer("--target", "claude", env=self.env)
        self.component()
        settings_path = self.home / ".claude/settings.json"
        content = json.loads(settings_path.read_text())
        content["theme"] = "dark"
        settings_path.write_text(json.dumps(content, indent=2) + "\n")
        before = settings_path.read_bytes()
        self.activate(settings_path)
        self.enroll()
        self.component(command="uninstall")
        self.assertEqual(settings_path.read_bytes(), before)
        self.assertTrue((self.state_dir / "auths/work.json").exists())
        self.assertNotIn("claude-router", self.manifest().get("settings", {}))
        self.run_installer("--target", "claude", command="uninstall", env=self.env)
        self.assertEqual(json.loads(settings_path.read_text()), {"theme": "dark"})


class ServiceLabelTests(ComponentFixture):
    def helper(self, command, *arguments, success=True):
        result = subprocess.run([sys.executable, str(self.repo / "scripts/claude_router.py"), command, "--home", str(self.home), *arguments],
                                capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        return result

    def test_uninstall_stops_the_label_this_home_recorded_and_refuses_another(self):
        lane = "dev.astack.claude-router.lane1"
        self.helper("install", "--service", lane)
        descriptor = self.descriptor.with_name(lane + self.descriptor.suffix)
        self.assertTrue(descriptor.exists())

        refused = self.helper("uninstall", "--service", "dev.astack.claude-router", success=False)
        self.assertEqual(refused.stderr, "astack: --service dev.astack.claude-router differs from {}, the service this home's "
                                         "install recorded; omit --service\n".format(lane))
        self.assertTrue(descriptor.exists())
        self.assertFalse(self.router_log.exists())

        removed = self.helper("uninstall")
        self.assertIn("STOP {} {}\n".format(self.manager_name, lane), removed.stdout)
        self.assertEqual(self.router_log.read_text(), "stop -uninstall -state {}\n".format(self.state_dir))
        self.assertFalse(descriptor.exists())

    def test_systemd_install_writes_a_user_unit_and_uninstall_stops_it(self):
        unit = self.home.resolve() / ".config/systemd/user/dev.astack.claude-router.lane2.service"
        self.helper("install", "--manager", "systemd", "--service", "dev.astack.claude-router.lane2")

        text = unit.read_text()
        self.assertIn('ExecStart="{}" serve -supervised -config "{}"'.format(self.data / "bin/claude-router", self.state_dir / "config.json"),
                      text)
        self.assertEqual(json.loads((self.state_dir / "service.json").read_text())["descriptor"], str(unit))
        removed = self.helper("uninstall")
        self.assertIn("STOP systemd dev.astack.claude-router.lane2\n", removed.stdout)
        self.assertFalse(unit.exists())

    def test_on_linux_activation_reads_the_systemd_user_environment(self):
        systemctl = self.root / "fake bin/systemctl"
        systemctl.write_text("#!/bin/sh\n[ \"$1 $2\" = \"--user show-environment\" ] && echo ANTHROPIC_API_KEY=set\nexit 0\n")
        systemctl.chmod(0o755)
        router = self.router()
        with patch.object(router.sys, "platform", "linux"), patch.dict(router.os.environ, {"PATH": self.env["PATH"]}):
            self.assertEqual((router.launcher_has("ANTHROPIC_API_KEY"), router.launcher_has("ANTHROPIC_AUTH_TOKEN")), ("systemd", None))


class ComponentFailureTests(ComponentFixture):
    def test_an_ambient_go_workspace_does_not_reach_the_build(self):
        (self.repo / "go.work").write_text("go 1.26\n\nuse ./components/claude-router\n")
        self.component()
        self.component(GOWORK=str(self.root / "elsewhere.work"))

        self.assertNotIn("# workspace", (self.data / "bin/claude-router").read_text())

    def test_architecture_and_version_errors_fail_before_writes(self):
        cross = self.component(success=False, FAKE_GOARCH="riscv64")
        self.assertIn("Go would build for", cross.stderr)
        old = self.component(success=False, FAKE_GOVERSION="go1.25.3")
        self.assertIn("needs Go 1.26.0 or later; found go1.25.3", old.stderr)
        broken = self.component(success=False, FAKE_BUILD_FAIL="1")
        self.assertIn("injected build error", broken.stderr)
        self.assertFalse(self.home.exists())

    def test_unsupported_host_fails_before_writes(self):
        module = self.module()
        with patch.object(module.claude_router.sys, "platform", "win32"):
            with self.assertRaisesRegex(ValueError, "supports macOS and Linux; this host is win32"):
                self.run_module(module, {}, "install", "--home", str(self.home), "--component", "claude-router")
        self.assertFalse(self.home.exists())

    def test_changed_roots_require_uninstall_first(self):
        module = self.module()
        first, second = self.root / "data one", self.root / "data two"
        self.run_module(module, {"XDG_DATA_HOME": str(first)}, "install", "--component", "claude-router")
        with self.assertRaisesRegex(ValueError, "claude-router destination changed"):
            self.run_module(module, {"XDG_DATA_HOME": str(second)}, "install", "--component", "claude-router")
        self.assertFalse(second.exists())
        self.run_module(module, {"XDG_DATA_HOME": str(second)}, "uninstall", "--component", "claude-router")
        self.assertFalse((first / "astack/claude-router/bin/claude-router").exists())
        self.run_module(module, {"XDG_DATA_HOME": str(second)}, "install", "--component", "claude-router")
        self.assertTrue((second / "astack/claude-router/bin/claude-router").exists())

    def test_unmanaged_and_locally_modified_files_are_protected(self):
        self.descriptor.parent.mkdir(parents=True)
        self.descriptor.write_text("someone else's job\n")
        refused = self.component(success=False)
        self.assertIn(str(self.descriptor), refused.stderr)
        self.assertEqual(self.descriptor.read_text(), "someone else's job\n")
        self.assertFalse(self.data.exists())
        self.component("--force")
        backup, = (self.home / ".local/state/astack/backups").rglob(self.descriptor.name)
        self.assertEqual(backup.read_text(), "someone else's job\n")
        binary = self.data / "bin/claude-router"
        binary.write_text("#!/bin/sh\nexit 0\n")
        self.component(success=False, FAKE_BUILD="2")
        self.component(command="uninstall", success=False)
        self.assertEqual(binary.read_text(), "#!/bin/sh\nexit 0\n")

    def test_component_cannot_take_a_harness_destination(self):
        registry = self.repo / "harnesses.json"
        harnesses = json.loads(registry.read_text())
        harnesses["claude"]["instructions"] = {"default": ".local/state/astack/claude-router", "suffix": "config.json"}
        registry.write_text(json.dumps(harnesses))
        result = self.component("--target", "claude", success=False)
        self.assertIn("distinct destinations", result.stderr)
        self.assertFalse((self.data / "bin/claude-router").exists())

    def test_interrupted_install_is_reconciled_on_retry(self):
        module = self.module()
        real_save = module.files.save_manifest
        calls = []

        def crash_after_binary_write(path, state):
            record = state.get("components", {}).get("claude-router", {})
            if any(name.endswith("bin/claude-router") for name in record.get("files", {})) and not calls:
                calls.append(path)
                raise OSError("injected crash after the binary was written")
            return real_save(path, state)

        with patch.object(module.files, "save_manifest", side_effect=crash_after_binary_write):
            with self.assertRaises(OSError) as raised:
                self.run_module(module, {}, "install", "--home", str(self.home), "--component", "claude-router")
        self.assertEqual(str(raised.exception), "injected crash after the binary was written")
        binary = self.data / "bin/claude-router"
        self.assertTrue(binary.exists())
        self.assertNotIn(str(binary), self.manifest()["components"]["claude-router"]["files"])
        self.component()
        self.assertIn(str(binary), self.manifest()["components"]["claude-router"]["files"])
        self.component(command="uninstall")
        self.assertFalse(binary.exists())


class DirectRecoveryTests(ComponentFixture):
    def setUp(self):
        super().setUp()
        self.component()
        self.enroll()
        self.settings_path = self.home / ".claude/settings.json"
        self.settings_path.parent.mkdir()
        self.settings_path.write_text(json.dumps({"theme": "dark", "env": {"KEEP_ME": "1"}}, indent=2) + "\n")
        self.before = self.settings_path.read_bytes()
        self.activate(self.settings_path)
        self.activated = self.settings_path.read_bytes()

    def direct(self, *mode):
        return subprocess.run([sys.executable, str(self.data / "lib/claude_router.py"), "direct", *mode,
                               "--state", str(self.state_dir), "--manifest", str(self.home / ".local/state/astack/manifest.json")],
                              capture_output=True, text=True, env=dict(self.env, HOME=str(self.home)))

    def test_prepare_lists_the_changes_and_changes_nothing(self):
        result = self.direct("--prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[:4], [
            "remove  ~/.claude/settings.json apiKeyHelper ([redacted])",
            "remove  ~/.claude/settings.json env.ANTHROPIC_BASE_URL (http://127.0.0.1:18787)",
            "remove  ~/.claude/settings.json env.CLAUDE_CODE_RETRY_WATCHDOG (1)",
            "keep    2 other settings keys; router logins work; 2 assignments",
        ])
        self.assertRegex(lines[4], r"^backup  ~/\.local/state/astack/backups/direct-[0-9a-f]{12}/settings\.json$")
        self.assertEqual(lines[5:], ["after   claude needs its own /login", "ready   nothing changed; apply with claude-router direct"])
        self.assertEqual(self.settings_path.read_bytes(), self.activated)

    def test_apply_restores_the_settings_backs_them_up_and_keeps_router_state(self):
        prepared = self.direct("--prepare").stdout.splitlines()
        result = self.direct("--stopped")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), prepared[:-1] + ["done    clients connect directly; the router stays installed and stopped"])
        self.assertEqual(self.settings_path.read_bytes(), self.before)
        backup = self.home / prepared[4].split("~/", 1)[1]
        self.assertEqual(backup.read_bytes(), self.activated)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertNotIn("claude-router", self.manifest().get("settings", {}))
        self.assertTrue((self.state_dir / "auths/work.json").exists())
        self.assertTrue((self.state_dir / "assignments/assignments.jsonl").exists())
        self.assertTrue((self.data / "bin/claude-router").exists())
        again = self.direct("--prepare")
        self.assertEqual(again.stdout, "keep    no client settings name the router; router logins work; 2 assignments\n"
                                       "ready   nothing to change\n")
        check = self.direct("--check")
        self.assertEqual((check.returncode, check.stdout, check.stderr), (3, "", ""))

    def test_without_a_stopped_service_apply_does_not_claim_one(self):
        result = self.direct()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], "done    clients connect directly; the router stays installed")
        self.assertEqual(self.settings_path.read_bytes(), self.before)

    def test_a_crash_after_the_settings_write_is_recovered_by_the_next_run(self):
        sys.path.insert(0, str(self.data / "lib"))
        try:
            spec = importlib.util.spec_from_file_location("installed_helper", self.data / "lib/claude_router.py")
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
        finally:
            sys.path.remove(str(self.data / "lib"))
        manifest = self.home / ".local/state/astack/manifest.json"
        with patch.object(helper.files, "save_manifest", side_effect=KeyboardInterrupt), redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                helper.direct(self.state_dir, manifest, "apply")
        self.assertEqual(self.settings_path.read_bytes(), self.before)
        self.assertIn("claude-router", self.manifest()["settings"])
        prepared = self.direct("--prepare")
        self.assertEqual(prepared.stdout.splitlines()[:3], [
            "skip    ~/.claude/settings.json apiKeyHelper (already restored)",
            "skip    ~/.claude/settings.json env.ANTHROPIC_BASE_URL (already restored)",
            "skip    ~/.claude/settings.json env.CLAUDE_CODE_RETRY_WATCHDOG (already restored)",
        ])
        result = self.direct()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.settings_path.read_bytes(), self.before)
        self.assertNotIn("claude-router", self.manifest().get("settings", {}))

    def test_a_local_edit_refuses_and_is_kept(self):
        content = json.loads(self.activated)
        content["env"]["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:9000"
        self.settings_path.write_text(json.dumps(content, indent=2) + "\n")
        edited = self.settings_path.read_bytes()
        for mode in (("--prepare",), ("--check",), ()):
            result = self.direct(*mode)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, "claude-router: direct: env.ANTHROPIC_BASE_URL changed after activation\n"
                                            "        expected http://127.0.0.1:18787, found http://127.0.0.1:9000\n"
                                            "next    restore or remove the key, then run again (local edits are kept)\n")
            self.assertEqual(self.settings_path.read_bytes(), edited)


class SettingsOwnershipTests(ComponentFixture):
    def test_harness_settings_cannot_claim_router_keys(self):
        source = self.repo / "settings/claude.json"
        content = json.loads(source.read_text())
        for key, value in (("apiKeyHelper", "cat /x"), ("env", {"ANTHROPIC_BASE_URL": "http://127.0.0.1:1"}),
                           ("env", {"ANTHROPIC_API_KEY": "x"}), ("env", {"ANTHROPIC_AUTH_TOKEN": "x"})):
            source.write_text(json.dumps(dict(content, **{key: value})))
            result = self.run_installer("--target", "claude", success=False, env=self.env)
            name = key if isinstance(value, str) else "env." + next(iter(value))
            self.assertIn("claude-router activation owns or overrides: " + name, result.stderr)
            self.assertFalse(self.home.exists())

    def test_harness_reinstall_and_removal_keep_router_keys(self):
        self.run_installer("--target", "claude", env=self.env)
        self.component()
        settings_path = self.home / ".claude/settings.json"
        self.activate(settings_path)
        activated = json.loads(settings_path.read_text())
        router_keys = {"apiKeyHelper": activated["apiKeyHelper"], "env": activated["env"]}
        source = self.repo / "settings/claude.json"
        content = json.loads(source.read_text())
        content["autoMemoryEnabled"] = True
        source.write_text(json.dumps(content))
        self.run_installer("--target", "claude", env=self.env)
        reinstalled = json.loads(settings_path.read_text())
        self.assertEqual({key: reinstalled[key] for key in router_keys}, router_keys)
        self.run_installer("--target", "claude", command="uninstall", env=self.env)
        self.assertEqual(json.loads(settings_path.read_text()), router_keys)

    def test_a_key_owned_twice_is_refused(self):
        self.run_installer("--target", "claude", env=self.env)
        state_path = self.home / ".local/state/astack/manifest.json"
        state = json.loads(state_path.read_text())
        settings_path = str(self.home.resolve() / ".claude/settings.json")
        state["settings"]["claude-router"] = {settings_path: dict(state["settings"]["claude"][settings_path])}
        state_path.write_text(json.dumps(state))
        result = self.run_installer("--target", "claude", success=False, env=self.env)
        self.assertIn("Targets must own distinct settings keys", result.stderr)


class ClientTokenTests(ComponentFixture):
    def test_no_output_settings_file_manifest_or_backup_holds_the_client_token(self):
        outputs = [self.component().stdout]
        self.enroll()
        settings_path = self.home / ".claude/settings.json"
        self.activate(settings_path)
        token = (self.state_dir / "client-token").read_text().strip()
        manifest = self.home / ".local/state/astack/manifest.json"

        def direct(*mode):
            return subprocess.run([sys.executable, str(self.data / "lib/claude_router.py"), "direct", *mode,
                                   "--state", str(self.state_dir), "--manifest", str(manifest)],
                                  capture_output=True, text=True, env=dict(self.env, HOME=str(self.home)))
        prepared = direct("--prepare")
        self.assertIn("remove  ~/.claude/settings.json apiKeyHelper ([redacted])\n", prepared.stdout)
        activated = settings_path.read_text()
        content = json.loads(activated)
        content["apiKeyHelper"] = "echo edited-secret-value"
        settings_path.write_text(json.dumps(content))
        edited = direct("--prepare")
        self.assertIn("        expected [redacted], found [redacted]\n", edited.stderr)
        settings_path.write_text(activated)
        stored = [activated, manifest.read_text()]
        applied = direct()
        self.assertEqual(applied.returncode, 0, applied.stderr)
        backup, = (manifest.parent / "backups").rglob("direct-*/settings.json")
        self.assertEqual(backup.read_text(), activated)
        stored += [path.read_text() for path in (settings_path, manifest) if path.exists()]
        stored += [path.read_text() for path in (manifest.parent / "backups").rglob("*") if path.is_file()]
        outputs += [prepared.stdout, prepared.stderr, edited.stdout, edited.stderr, applied.stdout, applied.stderr,
                    self.component("--dry-run", command="uninstall").stdout]
        for secret in (token, "credential-secret-value", "edited-secret-value"):
            for text in outputs + stored:
                self.assertNotIn(secret, text)

    def test_the_helper_command_prints_the_token_from_a_path_with_a_space_and_a_quote(self):
        state = self.root / "it's a state"
        state.mkdir()
        (state / "client-token").write_text("0123456789abcdef\n")
        (state / "config.json").write_text(json.dumps({"listen": "127.0.0.1:28787", "client_token_file": str(state / "client-token")}))

        values, replaced = self.router().client_settings(state, {"theme": "dark"})

        printed = subprocess.run(["/bin/sh", "-c", values["apiKeyHelper"]], capture_output=True, text=True)
        self.assertEqual((printed.returncode, printed.stdout), (0, "0123456789abcdef\n"))
        self.assertEqual(values["env"], {"ANTHROPIC_BASE_URL": "http://127.0.0.1:28787", "CLAUDE_CODE_RETRY_WATCHDOG": "1"})
        self.assertEqual(replaced, [])

    def test_a_relative_token_path_is_read_from_the_state_directory(self):
        state = self.root / "state"
        state.mkdir()
        (state / "client-token").write_text("0123456789abcdef\n")
        (state / "config.json").write_text(json.dumps({"listen": "127.0.0.1:28787", "client_token_file": "client-token"}))

        values, _ = self.router().client_settings(state, {})

        printed = subprocess.run(["/bin/sh", "-c", values["apiKeyHelper"]], capture_output=True, text=True, cwd=self.root)
        self.assertEqual((printed.returncode, printed.stdout), (0, "0123456789abcdef\n"))

    def test_activation_refuses_an_env_token_that_claude_code_would_use_instead(self):
        self.component()
        router = self.router()
        for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
            with self.assertRaisesRegex(ValueError, "Client settings set env.{}, which Claude Code uses instead".format(name)):
                router.client_settings(self.state_dir, {"env": {name: "x"}})

    def test_a_prior_api_key_helper_is_restored_exactly_by_direct_and_by_uninstall(self):
        self.component()
        settings_path = self.home / ".claude/settings.json"
        settings_path.parent.mkdir()
        settings_path.write_text(json.dumps({"apiKeyHelper": "/opt/vault/print-key --profile 'work'", "theme": "dark"}, indent=2) + "\n")
        before = settings_path.read_bytes()
        manifest = self.home / ".local/state/astack/manifest.json"

        self.assertEqual(self.activate(settings_path), ["apiKeyHelper"])
        prepared = subprocess.run([sys.executable, str(self.data / "lib/claude_router.py"), "direct", "--prepare",
                                   "--state", str(self.state_dir), "--manifest", str(manifest)],
                                  capture_output=True, text=True, env=dict(self.env, HOME=str(self.home)))
        applied = subprocess.run([sys.executable, str(self.data / "lib/claude_router.py"), "direct",
                                  "--state", str(self.state_dir), "--manifest", str(manifest)],
                                 capture_output=True, text=True, env=dict(self.env, HOME=str(self.home)))
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(settings_path.read_bytes(), before)
        self.assertIn("restore ~/.claude/settings.json apiKeyHelper ([redacted])\n", prepared.stdout)
        self.assertNotIn("after   claude needs its own /login", prepared.stdout)

        self.activate(settings_path)
        self.component(command="uninstall")
        self.assertEqual(settings_path.read_bytes(), before)
