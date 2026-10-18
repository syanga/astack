"""Lifecycle of the optional claude-router component in temporary homes.

A fake `go` on PATH stands in for the toolchain: it reports a version and
platform, and its `build` writes a shell script that logs its arguments. No
test starts a service manager or the real router.
"""

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
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
    with open(out, "w") as stream:
        stream.write("#!/bin/sh\n# build {}\nprintf '%s\\n' \"$*\" >> \"$FAKE_ROUTER_LOG\"\n".format(os.environ.get("FAKE_BUILD", "1")))
    os.chmod(out, 0o755)
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
        """Every file under the home except the astack manifest, which each run saves."""
        return {str(p.relative_to(self.home)): (p.read_bytes(), stat.S_IMODE(p.stat().st_mode), p.stat().st_mtime_ns)
                for p in self.home.rglob("*") if p.is_file() and p.name != "manifest.json"}

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


class TokenCreationTests(ComponentFixture):
    def test_an_install_interrupted_while_making_the_token_leaves_none_and_the_next_one_makes_it(self):
        token = self.state_dir / "client-token"
        module = self.module()

        def interrupted(_nbytes=None):
            raise KeyboardInterrupt("interrupted while making the token")

        with patch("secrets.token_hex", side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            self.run_module(module, {}, "install", "--home", str(self.home), "--component", "claude-router")
        left = token.exists()
        result = self.component()

        self.assertFalse(left)
        self.assertIn("CREATE {}".format(token), result.stdout)
        self.assertRegex(token.read_text(), r"^[0-9a-f]{64}\n$")
        self.assertEqual(stat.S_IMODE(token.stat().st_mode), 0o600)

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


class ComponentFailureTests(ComponentFixture):
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
