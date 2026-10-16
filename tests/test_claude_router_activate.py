from contextlib import redirect_stdout
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import patch

from test_claude_router_install import ComponentFixture

COMMIT = "4b1f0c2d9e8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c"
SDK = "v8.0.11"

FAKE_CLIENT = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, urllib.request
settings = json.load(open(os.environ["CLIENT_SETTINGS"]))
base = sys.argv[1] if len(sys.argv) > 1 else settings.get("env", {}).get("ANTHROPIC_BASE_URL")
key = subprocess.run(["/bin/sh", "-c", settings["apiKeyHelper"]], capture_output=True, text=True).stdout.strip()
request = urllib.request.Request(base + "/v1/messages", data=b"{}", headers={"x-api-key": key}, method="POST")
try:
    urllib.request.urlopen(request, timeout=10).read()
except OSError:
    sys.exit(1)
'''

FAKE_LAUNCHER = r'''#!/bin/sh
if [ "$1" = getenv ] && [ "$2" = "$FAKE_LAUNCHER_HAS" ]; then echo set; fi
if [ "$1" = --user ] && [ -n "$FAKE_LAUNCHER_HAS" ]; then echo "$FAKE_LAUNCHER_HAS=set"; fi
exit 0
'''


class FakeRouter:
    def __init__(self, token, events):
        self.status = {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def authorized(self):
                sent = self.headers.get("x-api-key") or (self.headers.get("Authorization") or "").removeprefix("Bearer ")
                return sent == token()

            def do_GET(self):
                if self.path == "/claude-router/status" and self.authorized():
                    return self.answer(200, fake.status)
                self.answer(401, {})

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if not self.authorized():
                    return self.answer(401, {})
                with open(events, "a") as stream:
                    stream.write(json.dumps({"kind": "request", "status": 200, "outcome": "completed"}) + "\n")
                self.answer(200, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class DirectUpstream(FakeRouter):
    def __init__(self):
        super().__init__(lambda: None, os.devnull)
        self.server.RequestHandlerClass.authorized = lambda handler: True


class ActivationFixture(ComponentFixture):
    maxDiff = None

    def setUp(self):
        super().setUp()
        self.component()
        self.enroll()
        state_path = self.home / ".local/state/astack/manifest.json"
        state = json.loads(state_path.read_text())
        state["components"]["claude-router"]["commit"] = COMMIT
        state_path.write_text(json.dumps(state))
        tools = self.root / "fake bin"
        for name, text in (("launchctl", FAKE_LAUNCHER), ("systemctl", FAKE_LAUNCHER), ("fake-client", FAKE_CLIENT),
                           ("claude", "#!/bin/sh\necho '2.1.300 (Claude Code)'\n")):
            (tools / name).write_text(text)
            (tools / name).chmod(0o755)
        self.claude = tools / "claude"
        self.sdk = self.root / "agent sdk"
        self.sdk.mkdir()
        (self.sdk / "package.json").write_text(json.dumps({"name": "@anthropic-ai/claude-agent-sdk", "version": "0.3.300"}))
        self.events = self.state_dir / "events.jsonl"
        self.router = FakeRouter(lambda: (self.state_dir / "client-token").read_text().strip(), self.events)
        self.addCleanup(self.router.close)
        config = json.loads((self.state_dir / "config.json").read_text())
        config["listen"] = "127.0.0.1:{}".format(self.router.port)
        (self.state_dir / "config.json").write_text(json.dumps(config))
        self.binary_sha256 = hashlib.sha256((self.data / "bin/claude-router").read_bytes()).hexdigest()
        self.router.status = {"journal": "ok", "binary": COMMIT[:7], "binary_sha256": self.binary_sha256, "sdk": SDK,
                              "accounts": [{"id": "work", "registered": True, "needs_login": False,
                                            "overage": {"state": "disabled"}}]}
        self.acceptance = self.root / "acceptance.json"
        self.accept()
        self.settings_path = self.home / ".claude/settings.json"
        self.settings_path.parent.mkdir()
        self.settings_path.write_text(json.dumps({
            "theme": "dark", "apiKeyHelper": "echo direct-key", "env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9", "KEEP_ME": "1"},
        }, indent=2) + "\n")
        self.before = self.settings_path.read_bytes()
        self.env["CLIENT_SETTINGS"] = str(self.settings_path)
        self.env.pop("ANTHROPIC_API_KEY", None)
        self.env.pop("ANTHROPIC_AUTH_TOKEN", None)
        self.env.pop("FAKE_LAUNCHER_HAS", None)

    def accept(self, **changes):
        record = {"result": "pass", "head": COMMIT, "binary_sha256": self.binary_sha256, "sdk": SDK,
                  "clients": {"claude_code": "2.1.300", "agent_sdk": "0.3.300"}}
        record.update(changes)
        self.acceptance.write_text(json.dumps(record))

    def activate(self, *arguments, verify=("fake-client",), **env):
        command = [sys.executable, str(self.repo / "scripts/claude_router.py"), "activate", "--home", str(self.home),
                   "--acceptance", str(self.acceptance), "--claude", str(self.claude), "--agent-sdk", str(self.sdk)]
        for check in verify:
            command += ["--verify", check]
        return subprocess.run(command + list(arguments), capture_output=True, text=True, env=dict(self.env, HOME=str(self.home.resolve()), **env))

    def direct(self, *mode):
        return subprocess.run([sys.executable, str(self.data / "lib/claude_router.py"), "direct", *mode,
                               "--state", str(self.state_dir), "--manifest", str(self.home / ".local/state/astack/manifest.json")],
                              capture_output=True, text=True, env=dict(self.env, HOME=str(self.home.resolve())))

    def journal(self):
        path = self.state_dir / "activation.json"
        return json.loads(path.read_text()) if path.exists() else None

    def assert_direct_and_state_kept(self):
        self.assertEqual(self.settings_path.read_bytes(), self.before)
        self.assertNotIn("claude-router", self.manifest().get("settings", {}))
        self.assertIsNone(self.journal())
        self.assertTrue((self.state_dir / "auths/work.json").exists())
        self.assertIn('"c":"session-2"', (self.state_dir / "assignments/assignments.jsonl").read_text())


class ActivationTests(ActivationFixture):
    def test_activation_routes_a_continuation_and_direct_restores_the_prior_settings(self):
        prepared = self.activate("--prepare")
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        listen = "http://127.0.0.1:{}".format(self.router.port)
        lines = prepared.stdout.splitlines()
        self.assertEqual(lines[:5], [
            "accept  head 4b1f0c2, binary {}, sdk v8.0.11, claude 2.1.300, agent sdk 0.3.300".format(self.binary_sha256[:12]),
            "ready   router running 127.0.0.1:{}; usable accounts work".format(self.router.port),
            "replace ~/.claude/settings.json apiKeyHelper ([redacted] with [redacted])",
            "replace ~/.claude/settings.json env.ANTHROPIC_BASE_URL (http://127.0.0.1:9 with {})".format(listen),
            "set     ~/.claude/settings.json env.CLAUDE_CODE_RETRY_WATCHDOG (1)",
        ])
        self.assertRegex(lines[5], r"^backup  ~/\.local/state/astack/backups/activate-[0-9a-f]{12}/settings\.json$")
        self.assertEqual(lines[6:], ["undo    claude-router direct", "ready   nothing changed; activate without --prepare"])
        self.assertEqual(self.settings_path.read_bytes(), self.before)
        self.assertIsNone(self.journal())

        result = self.activate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-3:], [
            "verify  apiKeyHelper prints a token the router accepts",
            "verify  fake-client: exit 0, 1 routed request",
            "done    clients connect through the router; undo with claude-router direct",
        ])
        settings = json.loads(self.settings_path.read_text())
        self.assertEqual(settings["env"], {"ANTHROPIC_BASE_URL": listen, "KEEP_ME": "1", "CLAUDE_CODE_RETRY_WATCHDOG": "1"})
        printed = subprocess.run(["/bin/sh", "-c", settings["apiKeyHelper"]], capture_output=True, text=True)
        self.assertEqual(printed.stdout.strip(), (self.state_dir / "client-token").read_text().strip())
        backup = self.home.resolve() / lines[5].split("~/", 1)[1]
        self.assertEqual(backup.read_bytes(), self.before)
        journal = self.journal()
        self.assertEqual((journal["phase"], journal["settings"], journal["backup"], journal["undo"]),
                         ("active", str(self.settings_path.resolve()), str(backup), "claude-router direct"))
        self.assertEqual(journal["acceptance"]["head"], COMMIT)

        again = self.activate()
        self.assertEqual(again.returncode, 1)
        self.assertEqual(again.stderr, "claude-router: activate: router activation already owns ~/.claude/settings.json\n"
                                       "next    claude-router direct, then run again\n")

        restored = self.direct()
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertIn("restore ~/.claude/settings.json apiKeyHelper ([redacted])", restored.stdout)
        self.assert_direct_and_state_kept()

    def test_a_missing_or_mismatched_acceptance_record_refuses_before_any_write(self):
        def refused(expected, **env):
            result = self.activate(**env)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn(expected, result.stderr)
            self.assertEqual(self.settings_path.read_bytes(), self.before)
            self.assertIsNone(self.journal())
            self.assertNotIn("settings", self.manifest())
            self.assertEqual(result.stdout, "")

        self.acceptance.unlink()
        refused("cannot read the acceptance record")
        self.accept(result="fail")
        refused("result: fail, not pass")
        self.accept(head="0" * 40)
        refused("head: accepted 000000000000, installed 4b1f0c2d9e8a")
        self.accept(clients={"claude_code": "2.1.300"})
        refused("agent_sdk: the record names no version")
        self.accept(clients={"claude_code": "2.1.299", "agent_sdk": "0.3.300"})
        refused("claude_code: accepted 2.1.299, --claude has 2.1.300")
        self.accept(clients={"claude_code": "2.1.300", "agent_sdk": "0.3.276"})
        refused("agent_sdk: accepted 0.3.276, --agent-sdk has 0.3.300")
        self.accept(sdk="v8.0.10")
        refused("sdk: accepted v8.0.10, running v8.0.11")

        self.accept()
        self.router.status["binary"] = "9f8e7d6"
        refused("head: accepted 4b1f0c2, running binary built from 9f8e7d6")
        self.router.status["binary"] = COMMIT[:7]
        self.router.status["binary_sha256"] = "a" * 64
        refused("binary: accepted {}, running aaaaaaaaaaaa".format(self.binary_sha256[:12]))
        self.router.status["binary_sha256"] = self.binary_sha256
        (self.data / "bin/claude-router").write_text("#!/bin/sh\n# rebuilt\n")
        refused("binary: accepted {}, installed ".format(self.binary_sha256[:12]))

    def test_a_router_that_is_not_ready_refuses_before_any_write(self):
        self.router.status["accounts"][0]["needs_login"] = True
        result = self.activate()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "claude-router: activate: the router is not ready\n"
                                        "        no account has a login and a reading that paid overflow is disabled\n"
                                        "next    claude-router status\n")
        self.router.close()
        result = self.activate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("claude-router: activate: the router at 127.0.0.1:{} does not answer".format(self.router.port), result.stderr)
        self.assertIn("next    claude-router start, then run again\n", result.stderr)
        self.assertEqual(self.settings_path.read_bytes(), self.before)
        self.assertIsNone(self.journal())

    def test_a_failed_or_unrouted_continuation_restores_the_direct_settings(self):
        failed = self.activate(verify=("fake-client", "exit 7"))
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(failed.stderr, "claude-router: activate: exit 7 exited 7\n"
                                        "undo    ~/.claude/settings.json restored; the router keeps its logins and assignments\n"
                                        "next    claude-router status\n")
        self.assert_direct_and_state_kept()

        upstream = DirectUpstream()
        self.addCleanup(upstream.close)
        unrouted = self.activate(verify=("fake-client http://127.0.0.1:{}".format(upstream.port),))
        self.assertEqual(unrouted.returncode, 1)
        self.assertIn("claude-router: activate: fake-client http://127.0.0.1:{} completed no request through the router\n".format(upstream.port),
                      unrouted.stderr)
        self.assert_direct_and_state_kept()

        slow = self.activate("--verify-timeout", "1", verify=("sleep 30",))
        self.assertEqual(slow.returncode, 1)
        self.assertIn("claude-router: activate: sleep 30 timed out after 1 s\n", slow.stderr)
        self.assert_direct_and_state_kept()

        self.assertEqual(self.activate(verify=()).stderr,
                         "claude-router: activate: activation needs at least one --verify continuation\n"
                         "next    pass --verify for the terminal client and for T3\n")
        self.assertEqual(self.settings_path.read_bytes(), self.before)

    def test_local_settings_and_environment_tokens_are_refused_or_named(self):
        content = json.loads(self.before)
        content["env"]["ANTHROPIC_API_KEY"] = "env-secret-value"
        self.settings_path.write_text(json.dumps(content))
        refused = self.activate()
        self.assertEqual(refused.returncode, 1)
        self.assertIn("Client settings set env.ANTHROPIC_API_KEY, which Claude Code uses instead of the router's apiKeyHelper", refused.stderr)
        self.settings_path.write_bytes(self.before)

        project = self.root / "project"
        (project / ".claude").mkdir(parents=True)
        (project / ".claude/settings.json").write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:1"}}))
        (project / ".claude/settings.local.json").write_text(json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "local-secret-value"}}))
        prepared = self.activate("--prepare", "--project", str(project), ANTHROPIC_API_KEY="shell-secret-value",
                                 FAKE_LAUNCHER_HAS="ANTHROPIC_AUTH_TOKEN")
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        manager = "launchd" if sys.platform == "darwin" else "systemd"
        warnings = [line for line in prepared.stdout.splitlines() if line.startswith("warn")]
        self.assertEqual(warnings, [
            "warn    {} sets env.ANTHROPIC_BASE_URL, which Claude Code uses instead of ~/.claude/settings.json".format(project / ".claude/settings.json"),
            "warn    {} sets env.ANTHROPIC_AUTH_TOKEN, which Claude Code uses instead of the router's apiKeyHelper".format(project / ".claude/settings.local.json"),
            "warn    {} gives the apps it starts ANTHROPIC_AUTH_TOKEN, which Claude Code uses instead of the router's apiKeyHelper".format(manager),
            "warn    this environment exports ANTHROPIC_API_KEY, which Claude Code uses instead of the router's apiKeyHelper",
        ])
        for secret in ("env-secret-value", "local-secret-value", "shell-secret-value", "direct-key",
                       (self.state_dir / "client-token").read_text().strip()):
            for text in (refused.stdout, refused.stderr, prepared.stdout, prepared.stderr):
                self.assertNotIn(secret, text)


class InterruptedActivationTests(ActivationFixture):
    def interrupt(self, during):
        spec_path = self.repo / "scripts/claude_router.py"
        sys.path.insert(0, str(spec_path.parent))
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("activation_helper", spec_path)
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
        finally:
            sys.path.remove(str(spec_path.parent))
        args = helper.argparse.Namespace(acceptance=self.acceptance, claude=self.claude, agent_sdk=self.sdk, settings=None,
                                         project=[], verify=["fake-client"], verify_timeout=60, prepare=False)
        target = {"settings write": (helper.files, "save_manifest"), "verification": (helper, "verify_wiring")}[during]
        with patch.object(*target, side_effect=KeyboardInterrupt), patch.dict(os.environ, self.env, clear=True), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                helper.activate(args, self.home.resolve() / ".local/state/astack", self.home.resolve(), False)

    def test_direct_and_uninstall_after_an_interruption_between_the_settings_write_and_its_record(self):
        self.interrupt("settings write")
        self.assertNotEqual(self.settings_path.read_bytes(), self.before)
        self.assertNotIn("settings", self.manifest())
        self.assertEqual(self.journal()["phase"], "writing")

        uninstall = self.component(command="uninstall", success=False)
        self.assertIn("An activation of claude-router was interrupted; run claude-router direct, then uninstall", uninstall.stderr)
        check = self.direct("--check")
        self.assertEqual(check.returncode, 0)
        prepared = self.direct("--prepare")
        self.assertEqual(prepared.stdout, "restore ~/.claude/settings.json (interrupted activation)\n"
                                          "keep    router logins work; 2 assignments\n"
                                          "ready   nothing changed; apply with claude-router direct\n")
        self.assertNotEqual(self.settings_path.read_bytes(), self.before)

        restored = self.direct("--stopped")

        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(restored.stdout, "restore ~/.claude/settings.json (interrupted activation)\n"
                                          "keep    router logins work; 2 assignments\n"
                                          "done    clients connect directly; the router stays installed and stopped\n")
        self.assert_direct_and_state_kept()
        self.component(command="uninstall")

    def test_the_next_activation_undoes_an_interrupted_verification_first(self):
        self.interrupt("verification")
        self.assertEqual(self.journal()["phase"], "verifying")
        self.assertIn("claude-router", self.manifest()["settings"])

        result = self.activate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], "undo    ~/.claude/settings.json restored after an interrupted activation")
        self.assertEqual(self.journal()["phase"], "active")
        self.assertEqual(self.direct().returncode, 0)
        self.assert_direct_and_state_kept()

    def test_an_edit_after_an_interruption_refuses_and_is_kept(self):
        self.interrupt("verification")
        edited = json.loads(self.settings_path.read_text())
        edited["theme"] = "light"
        self.settings_path.write_text(json.dumps(edited))
        kept = self.settings_path.read_bytes()

        for result in (self.direct(), self.activate()):
            self.assertEqual(result.returncode, 1)
            self.assertIn("~/.claude/settings.json changed after an interrupted activation\n", result.stderr)
            self.assertRegex(result.stderr, r"        backup  ~/\.local/state/astack/backups/activate-[0-9a-f]{12}/settings\.json\n")
            self.assertEqual(self.settings_path.read_bytes(), kept)
        self.assertEqual(self.journal()["phase"], "verifying")
