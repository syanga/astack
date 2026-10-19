from contextlib import redirect_stderr, redirect_stdout
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import pty
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from unittest.mock import patch
import warnings

from test_claude_router_install import ComponentFixture

COMMIT = "4b1f0c2d9e8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c"
SDK = "v8.0.11"
SESSION = "6f1d2c3b-0000-4000-8000-00000000000a"
CONVERSATION = hashlib.sha256(SESSION.encode()).hexdigest()[:12]
STOPS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)

FAKE_CLIENT = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, urllib.request
settings = json.load(open(os.environ["CLIENT_SETTINGS"]))
session = sys.argv[1]
base = sys.argv[2] if len(sys.argv) > 2 else settings.get("env", {}).get("ANTHROPIC_BASE_URL")
key = subprocess.run(["/bin/sh", "-c", settings["apiKeyHelper"]], capture_output=True, text=True).stdout.strip()
request = urllib.request.Request(base + "/v1/messages", data=b"{}", method="POST",
                                 headers={"x-api-key": key, "x-claude-code-session-id": session})
try:
    urllib.request.urlopen(request, timeout=10).read()
except OSError:
    sys.exit(1)
'''

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, urllib.request
arguments = sys.argv[1:]
if arguments == ["--version"]:
    print(os.environ.get("FAKE_CLAUDE_VERSION", "2.1.300") + " (Claude Code)")
    sys.exit(0)
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as log:
    log.write(json.dumps(arguments) + "\n")
if os.environ.get("FAKE_CLAUDE_EXIT"):
    sys.exit(int(os.environ["FAKE_CLAUDE_EXIT"]))
settings = json.load(open(os.environ["CLIENT_SETTINGS"]))
key = subprocess.run(["/bin/sh", "-c", settings["apiKeyHelper"]], capture_output=True, text=True).stdout.strip()
def send(session, status=None):
    headers = {"x-api-key": key, "x-claude-code-session-id": session}
    if status:
        headers["x-fake-status"] = status
    request = urllib.request.Request(settings["env"]["ANTHROPIC_BASE_URL"] + "/v1/messages", data=b"{}", method="POST", headers=headers)
    try:
        urllib.request.urlopen(request, timeout=10).read()
    except OSError:
        pass
session = arguments[arguments.index("--session-id") + 1]
send("6f1d2c3b-0000-4000-8000-0000000000ff")
send(session, "529")
send(session, os.environ.get("FAKE_CLAUDE_STATUS"))
print("a reply that check never shows")
'''

FAKE_LAUNCHER = r'''#!/bin/sh
if [ "$1" = getenv ] && [ "$2" = "$FAKE_LAUNCHER_HAS" ]; then echo set; fi
if [ "$1" = --user ] && [ -n "$FAKE_LAUNCHER_HAS" ]; then echo "$FAKE_LAUNCHER_HAS=set"; fi
exit 0
'''

HELPER_WITH_MANAGED_SETTINGS = r'''
import os, sys
from pathlib import Path
sys.path.insert(0, os.environ["ROUTER_SCRIPTS"])
import claude_router
claude_router.MANAGED_SETTINGS = {sys.platform: Path(os.environ["MANAGED_SETTINGS"])}
sys.argv = ["claude_router.py"] + sys.argv[1:]
claude_router.cli()
'''


SIGNAL_SENDER = r'''
import os, sys, time
pid, gate = int(sys.argv[1]), sys.argv[2]
deadline = time.time() + 30
while not os.path.exists(gate) and time.time() < deadline:
    pass
deadline, sent = time.time() + 2, 0
while time.time() < deadline:
    try:
        os.kill(pid, (15, 1, 2)[sent % 3])
    except ProcessLookupError:
        break
    sent += 1
'''
# CPython resets signal handlers to SIG_DFL while the interpreter exits; atexit runs in that phase.
SIGNALED_AT_EXIT = HELPER_WITH_MANAGED_SETTINGS.replace("import claude_router\n", """import claude_router
import atexit, signal
atexit.register(lambda: (signal.signal(signal.SIGTERM, signal.SIG_DFL), os.kill(os.getpid(), signal.SIGTERM)))
""")

OTHER = "6f1d2c3b-0000-4000-8000-00000000000b"
OTHER_CONVERSATION = hashlib.sha256(OTHER.encode()).hexdigest()[:12]
UNDONE = ("undo    ~/.claude/settings.json restored; the router keeps its logins and assignments\n"
          "next    claude-router status\n")
STOPPED = "claude-router: activate: verification was interrupted\n" + UNDONE


def timed(text):
    return re.sub(r" in \d+\.\ds$", " in 6.2s", text, flags=re.MULTILINE)


class Killed(BaseException):
    pass


def raise_killed(*args):
    raise Killed


class FakeRouter:
    def __init__(self, token, events):
        self.status = {}
        self.overloaded = set()
        self.account = "work"
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
                session = self.headers.get("x-claude-code-session-id", "")
                status = 529 if session in fake.overloaded else int(self.headers.get("x-fake-status") or 200)
                with open(events, "a") as stream:
                    stream.write(json.dumps({"kind": "request", "conversation": hashlib.sha256(session.encode()).hexdigest()[:12],
                                             "status": status, "outcome": "completed", "account": fake.account}) + "\n")
                self.answer(status, {})

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
        self.set_commit()
        tools = self.root / "fake bin"
        for name, text in (("launchctl", FAKE_LAUNCHER), ("systemctl", FAKE_LAUNCHER), ("fake-client", FAKE_CLIENT),
                           ("claude", FAKE_CLAUDE)):
            (tools / name).write_text(text)
            (tools / name).chmod(0o755)
        self.claude = tools / "claude"
        self.sdk = self.root / "agent sdk"
        self.sdk.mkdir()
        (self.sdk / "package.json").write_text(json.dumps({"name": "@anthropic-ai/claude-agent-sdk", "version": "0.3.300"}))
        self.managed = self.root / "managed-settings.json"
        self.events = self.state_dir / "events.jsonl"
        self.fake_router = FakeRouter(lambda: (self.state_dir / "client-token").read_text().strip(), self.events)
        self.addCleanup(self.fake_router.close)
        config = json.loads((self.state_dir / "config.json").read_text())
        config["listen"] = "127.0.0.1:{}".format(self.fake_router.port)
        config["accounts"] = [{"id": "work", "capacity": 1}]
        (self.state_dir / "config.json").write_text(json.dumps(config))
        self.listen = "http://127.0.0.1:{}".format(self.fake_router.port)
        self.binary_sha256 = hashlib.sha256((self.data / "bin/claude-router").read_bytes()).hexdigest()
        self.fake_router.status = {"journal": "ok", "binary": COMMIT[:7], "binary_sha256": self.binary_sha256, "sdk": SDK,
                                   "code_sha256": self.installed_code(),
                                   "accounts": [{"id": "work", "registered": True, "needs_login": False,
                                                 "overage": {"state": "disabled"}}]}
        self.settings_path = self.home / ".claude/settings.json"
        self.settings_path.parent.mkdir()
        self.settings_path.write_text(json.dumps({
            "theme": "dark", "apiKeyHelper": "echo direct-key", "env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9", "KEEP_ME": "1"},
        }, indent=2) + "\n")
        self.before = self.settings_path.read_bytes()
        self.env["CLIENT_SETTINGS"] = str(self.settings_path)
        self.env["FAKE_CLAUDE_LOG"] = str(self.root / "claude.log")
        for name in ("FAKE_CLAUDE_EXIT", "FAKE_CLAUDE_STATUS", "FAKE_CLAUDE_VERSION", "CLAUDE_CONFIG_DIR"):
            self.env.pop(name, None)
        self.env.pop("ANTHROPIC_API_KEY", None)
        self.env.pop("ANTHROPIC_AUTH_TOKEN", None)
        self.env.pop("FAKE_LAUNCHER_HAS", None)
        self.identity = json.loads(self.helper("identity", "--claude", str(self.claude), "--agent-sdk", str(self.sdk)).stdout)
        self.acceptance = self.root / "acceptance.json"
        self.accept()

    def installed_code(self):
        return self.manifest()["components"]["claude-router"]["code_sha256"]

    def set_commit(self):
        state_path = self.home / ".local/state/astack/manifest.json"
        state = json.loads(state_path.read_text())
        state["components"]["claude-router"]["commit"] = COMMIT
        state_path.write_text(json.dumps(state))

    def accept(self, **changes):
        record = dict(self.identity, result="pass")
        record.update(changes)
        self.acceptance.write_text(json.dumps(record))

    def helper(self, *arguments, **env):
        command = [sys.executable, "-c", HELPER_WITH_MANAGED_SETTINGS, arguments[0], "--home", str(self.home), *arguments[1:]]
        return subprocess.run(command, capture_output=True, text=True,
                              env=dict(self.env, HOME=str(self.home.resolve()), ROUTER_SCRIPTS=str(self.repo / "scripts"),
                                       MANAGED_SETTINGS=str(self.managed), **env))

    def run_activate(self, *arguments, verify=((SESSION, "fake-client " + SESSION),), **env):
        command = ["activate", "--acceptance", str(self.acceptance), "--claude", str(self.claude), "--agent-sdk", str(self.sdk)]
        for session, check in verify:
            command += ["--verify", session, check]
        return self.helper(*command, *arguments, **env)

    def activate_command(self, *verify):
        command = [sys.executable, "-c", HELPER_WITH_MANAGED_SETTINGS, "activate", "--home", str(self.home),
                   "--acceptance", str(self.acceptance), "--claude", str(self.claude), "--agent-sdk", str(self.sdk)]
        for session, check in verify:
            command += ["--verify", session, check]
        return command

    def activate_env(self):
        return dict(self.env, HOME=str(self.home.resolve()), ROUTER_SCRIPTS=str(self.repo / "scripts"), MANAGED_SETTINGS=str(self.managed))

    def start_activate(self, *verify, prefix=(), **streams):
        return subprocess.Popen([*prefix, *self.activate_command(*verify)], env=self.activate_env(), **streams)

    def wait_for_pid(self, pidfile):
        deadline = time.time() + 30
        while not (pidfile.exists() and pidfile.read_text().strip()) and time.time() < deadline:
            time.sleep(0.05)
        pid = int(pidfile.read_text())
        self.addCleanup(lambda: subprocess.run(["kill", "-9", str(pid)], capture_output=True))
        return pid

    def gone(self, pid):
        deadline = time.time() + 5
        while time.time() < deadline:
            if subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip() in ("", "Z"):
                return True
            time.sleep(0.05)
        return False

    def direct(self, *mode, **options):
        return subprocess.run([sys.executable, str(self.data / "lib/claude_router.py"), "direct", *mode,
                               "--state", str(self.state_dir), "--manifest", str(self.home / ".local/state/astack/manifest.json")],
                              capture_output=True, text=True, env=dict(self.env, HOME=str(self.home.resolve())), **options)

    def journal(self):
        path = self.state_dir / "activation.json"
        return json.loads(path.read_text()) if path.exists() else None

    def base_url(self):
        return json.loads(self.settings_path.read_text())["env"]["ANTHROPIC_BASE_URL"]

    def assert_direct_and_state_kept(self):
        self.assertEqual(self.settings_path.read_bytes(), self.before)
        self.assertNotIn("claude-router", self.manifest().get("settings", {}))
        self.assertIsNone(self.journal())
        self.assertTrue((self.state_dir / "auths/work.json").exists())
        self.assertIn('"c":"session-2"', (self.state_dir / "assignments/assignments.jsonl").read_text())

    def assert_refused_before_any_write(self, result, expected):
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(expected, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.settings_path.read_bytes(), self.before)
        self.assertIsNone(self.journal())
        self.assertNotIn("settings", self.manifest())


class ActivationTests(ActivationFixture):
    def test_activation_routes_a_continuation_and_direct_restores_the_prior_settings(self):
        prepared = self.run_activate("--prepare")
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        lines = prepared.stdout.splitlines()
        backup = "~/.local/state/astack/backups/activate-{}/settings.json".format(hashlib.sha256(self.before).hexdigest()[:12])
        self.assertEqual(lines[0], "accept  code {}, helpers {}, sdk v8.0.11".format(self.installed_code()[:12],
                                                                                     self.identity["helpers_sha256"][:12]))
        self.assertEqual(lines[1:], [
            "ready   router running 127.0.0.1:{}; usable accounts work".format(self.fake_router.port),
            "replace ~/.claude/settings.json apiKeyHelper (the old value stays in {})".format(backup),
            "replace ~/.claude/settings.json env.ANTHROPIC_BASE_URL (http://127.0.0.1:9 with {})".format(self.listen),
            "set     ~/.claude/settings.json env.CLAUDE_CODE_RETRY_WATCHDOG (1)",
            "backup  " + backup,
            "undo    claude-router direct",
            "ready   nothing changed; activate without --prepare",
        ])
        self.assertEqual(self.settings_path.read_bytes(), self.before)
        self.assertIsNone(self.journal())

        result = self.run_activate(verify=((SESSION, "CLIENT_FLAG=inline-secret-value fake-client {} # --secret-arg".format(SESSION)),))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(timed(result.stdout).splitlines()[-9:], [
            "verify  fake-client (conversation {}): exit 0, 1 routed request".format(CONVERSATION),
            "ok        router running on 127.0.0.1:{} ({})".format(self.fake_router.port, self.manager_name),
            "ok        work ready",
            "ok        paid overflow off on work",
            "ok        Claude Code settings point at the router",
            "ok        code matches the accepted record ({}/{})".format(*self.router().host_platform()),
            "ok        test message answered 200 on work in 6.2s",
            "check     passed",
            "done    clients connect through the router; undo with claude-router direct",
        ])
        for text in (result.stdout, result.stderr):
            self.assertNotIn("inline-secret-value", text)
            self.assertNotIn("--secret-arg", text)
        settings = json.loads(self.settings_path.read_text())
        self.assertEqual(settings["env"], {"ANTHROPIC_BASE_URL": self.listen, "KEEP_ME": "1", "CLAUDE_CODE_RETRY_WATCHDOG": "1"})
        printed = subprocess.run(["/bin/sh", "-c", settings["apiKeyHelper"]], capture_output=True, text=True)
        self.assertEqual(printed.stdout.strip(), (self.state_dir / "client-token").read_text().strip())
        self.assertEqual((self.home.resolve() / backup[2:]).read_bytes(), self.before)
        journal = self.journal()
        self.assertEqual((journal["phase"], journal["settings"], journal["backup"], journal["undo"], journal["acceptance"]["head"]),
                         ("active", str(self.settings_path.resolve()), str(self.home.resolve() / backup[2:]), "claude-router direct", COMMIT))

        again = self.run_activate()
        self.assertEqual(again.returncode, 1)
        self.assertEqual(again.stderr, "claude-router: activate: router activation already owns ~/.claude/settings.json\n"
                                       "next    claude-router direct, then run again\n")

        restored = self.direct()
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertIn("restore ~/.claude/settings.json apiKeyHelper ([redacted])", restored.stdout)
        self.assert_direct_and_state_kept()

    def test_a_missing_or_mismatched_acceptance_record_refuses_before_any_write(self):
        code = self.installed_code()
        self.acceptance.unlink()
        self.assert_refused_before_any_write(self.run_activate(), "cannot read the acceptance record")
        for changes, expected in (
                ({"result": "fail"}, "result: fail, not pass"),
                ({"code_sha256": "d" * 64}, "code: accepted dddddddddddd, installed {}".format(code[:12])),
                ({"code_sha256": None}, "code: the record names no digest"),
                ({"code_sha256": code[:12]}, "code: the record names no digest"),
                ({"sdk": "v8.0.10"}, "sdk: accepted v8.0.10, running v8.0.11"),
                ({"helpers_sha256": "c" * 64}, "helpers: accepted cccccccccccc, installed {}".format(self.identity["helpers_sha256"][:12])),
                ({"helpers_sha256": None}, "helpers: the record names no digest"),
                ({"helpers_sha256": self.identity["helpers_sha256"][:12]}, "helpers: the record names no digest")):
            with self.subTest(changes):
                self.accept(**changes)
                self.assert_refused_before_any_write(self.run_activate(), expected)

        self.accept()
        for status, expected in (
                ({"code_sha256": "e" * 64}, "code: accepted {}, running eeeeeeeeeeee".format(code[:12])),
                ({"binary_sha256": "a" * 64}, "binary: installed {}, running aaaaaaaaaaaa".format(self.binary_sha256[:12]))):
            with self.subTest(status):
                with patch.dict(self.fake_router.status, status):
                    self.assert_refused_before_any_write(self.run_activate(), expected)

        (self.data / "bin/claude-router").write_text("#!/bin/sh\n# rebuilt\n")
        rebuilt = hashlib.sha256(b"#!/bin/sh\n# rebuilt\n").hexdigest()
        self.assert_refused_before_any_write(self.run_activate(), "binary: installed {}, running {}".format(rebuilt[:12], self.binary_sha256[:12]))

    def test_a_record_with_another_head_build_and_client_versions_activates_when_check_passes(self):
        self.accept(head="0" * 40, binary_sha256="b" * 64, clients={"claude_code": "2.1.285", "agent_sdk": "0.3.285"})

        result = self.run_activate(verify=())

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-2:], ["check     passed",
                                                           "done    clients connect through the router; undo with claude-router direct"])
        self.assertEqual(self.base_url(), self.listen)
        self.assertEqual(self.journal()["phase"], "active")

    def test_a_failed_check_restores_the_direct_settings(self):
        for env, line in (({"FAKE_CLAUDE_EXIT": "1"}, "fail      test message got no answer through the router (claude exited 1)"),
                          ({"FAKE_CLAUDE_STATUS": "529"}, "fail      test message got no answer through the router (claude exited 0)")):
            with self.subTest(env):
                result = self.run_activate(verify=(), **env)

                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(line + "\ncheck     failed\n", result.stdout)
                self.assertEqual(result.stderr, UNDONE)
                self.assert_direct_and_state_kept()

    def test_a_check_that_cannot_read_the_events_restores_the_direct_settings(self):
        self.events.touch()
        self.events.chmod(0o200)
        self.addCleanup(self.events.chmod, 0o600)

        result = self.run_activate(verify=())

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertRegex(result.stderr, r"^claude-router: activate: the check could not finish: \[Errno 13\] Permission denied: '[^']*events\.jsonl'\n")
        self.assertTrue(result.stderr.endswith(UNDONE), result.stderr)
        self.assert_direct_and_state_kept()

    def test_activation_on_another_platform_than_the_record_s_names_both(self):
        host = "{}/{}".format(*self.router().host_platform())
        other = "linux/amd64" if host.startswith("darwin") else "darwin/arm64"
        self.accept(platform=other)

        result = self.run_activate(verify=())

        self.assertEqual(result.returncode, 0, result.stderr)
        untested = ", not yet tested on real Linux" if host.startswith("linux") else ""
        self.assertIn("note    accepted on {}, this is {}{}\n".format(other, host, untested), result.stdout)
        self.assertIn("ok        code matches the accepted record ({})\n".format(other), result.stdout)

    def test_the_helpers_digest_covers_each_installed_file_by_path_and_content(self):
        lib = self.data / "lib"
        listed = sorted((path.relative_to(lib).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
                        for path in lib.rglob("*") if path.is_file())
        self.assertIn("_vendor/tomllib/_parser.py", dict(listed))
        expected = hashlib.sha256("".join("{}\0{}\n".format(name, digest) for name, digest in listed).encode()).hexdigest()
        self.assertEqual(self.identity["helpers_sha256"], expected)

        for cache in (lib / "__pycache__/claude_router.cpython-312.pyc", lib / "_vendor/tomllib/__pycache__/_parser.cpython-312.pyc"):
            cache.parent.mkdir(exist_ok=True)
            cache.write_bytes(b"compiled")
        identity = json.loads(self.helper("identity", "--claude", str(self.claude), "--agent-sdk", str(self.sdk)).stdout)
        self.assertEqual(identity["helpers_sha256"], self.identity["helpers_sha256"])
        self.assertEqual(self.run_activate("--prepare").returncode, 0)

    def test_an_install_from_an_edited_checkout_refuses_before_any_write(self):
        for name in ("claude_router.py", "_vendor/tomllib/_parser.py"):
            with self.subTest(name):
                checkout = self.repo / "scripts" / name
                original = checkout.read_bytes()
                checkout.write_bytes(original + b"\n# a local edit\n")
                self.addCleanup(checkout.write_bytes, original)
                self.component()
                self.set_commit()
                identity = json.loads(self.helper("identity", "--claude", str(self.claude), "--agent-sdk", str(self.sdk)).stdout)
                self.assertNotEqual(identity["helpers_sha256"], self.identity["helpers_sha256"])
                self.assertEqual({k: v for k, v in identity.items() if k != "helpers_sha256"},
                                 {k: v for k, v in self.identity.items() if k != "helpers_sha256"})

                self.assert_refused_before_any_write(self.run_activate(), "        helpers: accepted {}, installed {}\n".format(
                    self.identity["helpers_sha256"][:12], identity["helpers_sha256"][:12]))
                checkout.write_bytes(original)
                self.component()
                self.set_commit()

    def test_activation_refuses_when_the_running_helpers_differ_from_the_installed_ones(self):
        lib = self.data / "lib"
        for edited in (self.repo / "scripts/astack_settings.py", lib / "astack_settings.py",
                       self.repo / "scripts/_vendor/tomllib/_re.py", lib / "_vendor/tomllib/_re.py"):
            with self.subTest(edited):
                original = edited.read_bytes()
                edited.write_bytes(original + b"\n# a local edit\n")
                result = self.run_activate("--prepare")
                edited.write_bytes(original)
                self.assert_refused_before_any_write(result, "")
                self.assertRegex(result.stderr,
                                 r"^claude-router: activate: the helpers running this command differ from the installed ones\n"
                                 r"        helpers: running [0-9a-f]{12} from .+/scripts, installed [0-9a-f]{12}\n"
                                 r"next    run ~/\.local/share/astack/claude-router/lib/claude_router\.py instead\n$")

        (lib / "notes.txt").write_text("stray\n")
        from_lib = subprocess.run([sys.executable, str(lib / "claude_router.py"), "activate", "--prepare", "--home", str(self.home),
                                   "--acceptance", str(self.acceptance), "--claude", str(self.claude), "--agent-sdk", str(self.sdk)],
                                  capture_output=True, text=True, env=dict(self.env, HOME=str(self.home.resolve())))
        self.assertEqual(from_lib.returncode, 1)
        self.assertTrue(from_lib.stderr.endswith("next    reinstall from a checkout of the accepted head: "
                                                 "./install.sh --component claude-router\n"), from_lib.stderr)
        shutil.rmtree(lib)
        missing = self.run_activate()
        self.assertRegex(missing.stderr, r"        helpers: running [0-9a-f]{12} from .+/scripts, installed none\n"
                                         r"next    reinstall from a checkout of the accepted head: \./install\.sh --component claude-router\n$")
        self.assertEqual(self.settings_path.read_bytes(), self.before)

    def test_uninstall_refuses_to_restore_settings_with_helpers_other_than_the_installed_ones(self):
        checkout = self.repo / "scripts/astack_settings.py"
        original = checkout.read_bytes()
        for wiring in ("activation", "settings record without activation.json"):
            with self.subTest(wiring):
                if wiring == "activation":
                    self.assertEqual(self.run_activate().returncode, 0)
                else:
                    self.activate(self.settings_path)
                    self.assertIsNone(self.journal())
                wired = self.settings_path.read_bytes()
                checkout.write_bytes(original + b"\n# a local edit\n")

                refused = self.component(command="uninstall", success=False)
                checkout.write_bytes(original)

                self.assertEqual(refused.returncode, 1)
                self.assertRegex(refused.stderr, r"Uninstall would restore client settings, but the helpers running this command differ from "
                                                 r"the installed ones \(helpers: running [0-9a-f]{12} from .+/scripts, installed [0-9a-f]{12}\); "
                                                 r"run claude-router direct, then uninstall")
                self.assertEqual(self.settings_path.read_bytes(), wired)
                self.assertEqual(self.direct().returncode, 0)
                self.assertEqual(self.settings_path.read_bytes(), self.before)
        checkout.write_bytes(original + b"\n# a local edit\n")
        self.component(command="uninstall")
        self.assertFalse((self.data / "lib").exists())

    def test_a_router_that_is_not_ready_refuses_before_any_write(self):
        no_account = ("claude-router: activate: the router is not ready\n"
                      "        no account has a login and a reading that paid overflow is disabled\n"
                      "next    claude-router status\n")
        for account, expected in (
                ({"needs_login": True}, no_account),
                ({"registered": False}, no_account),
                ({"overage": {"state": "enabled"}}, no_account),
                ({"overage": {"state": "unknown"}}, no_account),
                ({"overage": None}, no_account)):
            with self.subTest(account):
                with patch.dict(self.fake_router.status["accounts"][0], account):
                    result = self.run_activate()
                self.assert_refused_before_any_write(result, expected)
                self.assertEqual(result.stderr, expected)
        with patch.dict(self.fake_router.status, {"journal": "assignments.jsonl line 3: unexpected end of JSON input"}):
            result = self.run_activate()
        self.assertEqual(result.stderr, "claude-router: activate: the router is not ready\n"
                                        "        assignment journal: assignments.jsonl line 3: unexpected end of JSON input\n"
                                        "next    claude-router status\n")

        self.fake_router.close()
        result = self.run_activate()
        self.assert_refused_before_any_write(result, "claude-router: activate: the router at 127.0.0.1:{} does not answer".format(
            self.fake_router.port))
        self.assertIn("next    claude-router start, then run again\n", result.stderr)

    def test_a_failed_or_unrouted_continuation_restores_the_direct_settings(self):
        failed = self.run_activate(verify=((SESSION, "fake-client " + SESSION), ("other", "exit 7")))
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(failed.stderr, "claude-router: activate: exit (conversation {}) exited 7\n"
                                        "undo    ~/.claude/settings.json restored; the router keeps its logins and assignments\n"
                                        "next    claude-router status\n".format(hashlib.sha256(b"other").hexdigest()[:12]))
        self.assert_direct_and_state_kept()

        upstream = DirectUpstream()
        self.addCleanup(upstream.close)
        unrouted = self.run_activate(verify=((SESSION, "fake-client {} http://127.0.0.1:{}".format(SESSION, upstream.port)),))
        self.assertEqual(unrouted.returncode, 1)
        self.assertIn("claude-router: activate: fake-client (conversation {}) completed no request through the router\n".format(CONVERSATION),
                      unrouted.stderr)
        self.assert_direct_and_state_kept()

        routed_then_failed = self.run_activate(verify=((SESSION, "fake-client {}; exit 3".format(SESSION)),))
        self.assertEqual(routed_then_failed.returncode, 1)
        self.assertIn("claude-router: activate: fake-client (conversation {}) exited 3\n".format(CONVERSATION), routed_then_failed.stderr)
        self.assertIn('"conversation": "{}", "status": 200'.format(CONVERSATION), self.events.read_text())
        self.assert_direct_and_state_kept()

        self.fake_router.overloaded.add(SESSION)
        refused = self.run_activate(verify=((SESSION, "fake-client {} || true".format(SESSION)),))
        self.assertEqual(refused.returncode, 1)
        self.assertIn("claude-router: activate: fake-client (conversation {}) completed no request through the router\n".format(CONVERSATION),
                      refused.stderr)
        self.assert_direct_and_state_kept()


    def test_a_continuation_counts_only_requests_of_its_own_conversation(self):
        result = self.run_activate(verify=((SESSION, "fake-client another-conversation"),))

        self.assertEqual(result.returncode, 1)
        self.assertIn("claude-router: activate: fake-client (conversation {}) completed no request through the router\n".format(CONVERSATION),
                      result.stderr)
        self.assertIn('"conversation": "{}", "status": 200'.format(hashlib.sha256(b"another-conversation").hexdigest()[:12]),
                      self.events.read_text())
        self.assert_direct_and_state_kept()

    def test_a_timed_out_continuation_is_killed_with_its_children(self):
        pidfile = self.root / "grandchild.pid"
        slow = self.run_activate("--verify-timeout", "1",
                                 verify=((SESSION, "exec > /dev/null 2>&1; sh -c 'echo $$ > \"{}\"; exec sleep 30' & wait".format(pidfile)),))
        self.assertEqual(slow.returncode, 1)
        self.assertIn("claude-router: activate: exec (conversation {}) timed out after 1 s\n".format(CONVERSATION), slow.stderr)
        self.assert_direct_and_state_kept()
        pid = int(pidfile.read_text())
        self.addCleanup(lambda: subprocess.run(["kill", "-9", str(pid)], capture_output=True))
        self.assertTrue(self.gone(pid), "the continuation's child {} still runs".format(pid))

    def test_a_continuation_s_children_are_killed_when_it_ends(self):
        for ending, expected_exit in (("exit 5", 1), ("fake-client {}".format(SESSION), 0)):
            with self.subTest(ending):
                pidfile = self.root / "child-{}.pid".format(expected_exit)
                command = ("exec > /dev/null 2>&1; sh -c 'echo $$ > \"{0}\"; exec sleep 30' & "
                           "while [ ! -s \"{0}\" ]; do sleep 0.05; done; {1}".format(pidfile, ending))
                result = self.run_activate(verify=((SESSION, command),))
                pid = int(pidfile.read_text())
                self.addCleanup(lambda pid=pid: subprocess.run(["kill", "-9", str(pid)], capture_output=True))

                self.assertEqual(result.returncode, expected_exit, result.stderr)
                self.assertTrue(self.gone(pid), "the continuation's child {} still runs".format(pid))
                if expected_exit == 0:
                    self.assertEqual(self.direct().returncode, 0)
                self.assert_direct_and_state_kept()

    def sleeper(self, name):
        pidfile = self.root / "{}.pid".format(name)
        return pidfile, "echo $$ > \"{}\"; exec sleep 30 > /dev/null 2>&1".format(pidfile)

    def test_an_interrupted_verification_restores_the_direct_settings(self):
        for sent in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(sent):
                pidfile, command = self.sleeper("verify-{}".format(int(sent)))
                process = self.start_activate((SESSION, command), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                pid = self.wait_for_pid(pidfile)
                process.send_signal(sent)
                _, stderr = process.communicate(timeout=30)

                self.assertEqual(process.returncode, 1)
                self.assertEqual(stderr, STOPPED)
                self.assert_direct_and_state_kept()
                self.assertTrue(self.gone(pid), "the continuation {} still runs".format(pid))

    def test_repeated_and_mixed_signals_do_not_stop_the_undo_or_change_the_exit_status(self):
        for name, signals in (("repeated", (signal.SIGTERM,)), ("mixed", (signal.SIGHUP, signal.SIGINT, signal.SIGTERM))):
            with self.subTest(name):
                pidfile, command = self.sleeper("verify-" + name)
                process = self.start_activate((SESSION, command), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                pid = self.wait_for_pid(pidfile)
                deadline = time.time() + 10
                sent = 0
                while process.poll() is None and time.time() < deadline:
                    os.kill(process.pid, signals[sent % len(signals)])
                    sent += 1
                _, stderr = process.communicate(timeout=30)

                self.assertEqual(process.returncode, 1)
                self.assertEqual(stderr, STOPPED)
                self.assert_direct_and_state_kept()
                self.assertTrue(self.gone(pid), "the continuation {} still runs".format(pid))

    def test_signals_from_many_parallel_senders_do_not_stop_the_undo_or_change_the_exit_status(self):
        pidfile, command = self.sleeper("verify-parallel")
        gate = self.root / "parallel.go"
        process = self.start_activate((SESSION, command), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        senders = [subprocess.Popen([sys.executable, "-c", SIGNAL_SENDER, str(process.pid), str(gate)]) for _ in range(8)]
        for sender in senders:
            self.addCleanup(sender.kill)
        pid = self.wait_for_pid(pidfile)
        gate.touch()
        for sender in senders:
            sender.wait(timeout=30)
        _, stderr = process.communicate(timeout=30)

        self.assertEqual(process.returncode, 1)
        self.assertEqual(stderr, STOPPED)
        self.assert_direct_and_state_kept()
        self.assertTrue(self.gone(pid), "the continuation {} still runs".format(pid))

    def test_a_signal_while_activation_exits_changes_neither_the_result_nor_the_exit_status(self):
        command = self.activate_command((SESSION, "fake-client " + SESSION))
        command[2] = SIGNALED_AT_EXIT
        result = subprocess.run(command, env=self.activate_env(), capture_output=True, text=True, timeout=60)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], "done    clients connect through the router; undo with claude-router direct")
        self.assertEqual(self.journal()["phase"], "active")

    def test_signals_during_the_undo_after_a_failed_continuation_do_not_stop_it(self):
        process = self.start_activate((SESSION, "exit 5"), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        watchdog = threading.Timer(30, process.kill)
        watchdog.start()
        self.addCleanup(watchdog.cancel)
        failure = process.stderr.readline()
        deadline, sent = time.time() + 10, 0
        while True:
            os.kill(process.pid, STOPS[sent % len(STOPS)])
            sent += 1
            if process.poll() is not None or time.time() > deadline:
                break
        _, rest = process.communicate(timeout=30)

        self.assertEqual(failure, "claude-router: activate: exit (conversation {}) exited 5\n".format(CONVERSATION))
        self.assertEqual(rest, UNDONE)
        self.assertEqual(process.returncode, 1)
        self.assert_direct_and_state_kept()

    def test_signals_ignored_when_activation_starts_stay_ignored(self):
        pidfile, go = self.root / "ignored.pid", self.root / "ignored.go"
        command = "echo $$ > \"{}\"; while [ ! -e \"{}\" ]; do sleep 0.05; done; fake-client {}".format(pidfile, go, SESSION)
        process = self.start_activate((SESSION, command), prefix=("/bin/sh", "-c", 'trap "" HUP INT TERM; exec "$@"', "sh"),
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        pid = self.wait_for_pid(pidfile)
        for sent in STOPS:
            process.send_signal(sent)
        time.sleep(0.3)
        go.touch()
        stdout, stderr = process.communicate(timeout=30)

        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(stdout.splitlines()[-1], "done    clients connect through the router; undo with claude-router direct")
        self.assertEqual(self.journal()["phase"], "active")
        self.assertTrue(self.gone(pid))
        self.assertEqual(self.direct().returncode, 0)
        self.assert_direct_and_state_kept()

    def activate_while_stdout_breaks(self, second):
        go = self.root / "stdout-gone.go"
        go.unlink(missing_ok=True)
        first = "while [ ! -e \"{}\" ]; do sleep 0.05; done; fake-client {}".format(go, SESSION)
        process = self.start_activate((SESSION, first), (OTHER, second), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        late = threading.Event()
        deadline = threading.Timer(30, lambda: (late.set(), go.touch()))
        deadline.start()
        self.addCleanup(deadline.cancel)
        while process.stdout.readline() not in ("undo    claude-router direct\n", ""):
            pass
        process.stdout.close()
        go.touch()
        _, stderr = process.communicate(timeout=30)
        self.assertFalse(late.is_set(), "activation did not print its plan before the first command ran")
        return process.returncode, stderr

    def activate_without_a_stdout_reader(self, continuation):
        reader, writer = os.pipe()
        os.close(reader)
        process = self.start_activate((SESSION, continuation), stdout=writer, stderr=subprocess.PIPE, text=True)
        os.close(writer)
        _, stderr = process.communicate(timeout=30)
        return process.returncode, stderr

    def test_a_stdout_that_breaks_during_verification_keeps_the_failure_lines_on_stderr(self):
        code, stderr = self.activate_while_stdout_breaks("exit 5")

        self.assertEqual(code, 1)
        self.assertEqual(stderr, "claude-router: activate: exit (conversation {}) exited 5\n".format(OTHER_CONVERSATION) + UNDONE)
        self.assert_direct_and_state_kept()

    def test_a_stdout_that_breaks_during_verification_keeps_the_edit_refusal_on_stderr(self):
        edit = "'{}' -c 'import json, sys; p = sys.argv[1]; d = json.load(open(p)); d[\"theme\"] = \"light\"; " \
               "json.dump(d, open(p, \"w\"))' \"{}\"; exit 7".format(sys.executable, self.settings_path)

        code, stderr = self.activate_while_stdout_breaks(edit)

        self.assertEqual(code, 1)
        self.assertRegex(stderr,
                         r"^claude-router: activate: {} \(conversation {}\) exited 7\n"
                         r"claude-router: activate: ~/\.claude/settings\.json changed after an interrupted activation\n"
                         r"        backup  ~/\.local/state/astack/backups/activate-[0-9a-f]{{12}}/settings\.json\n"
                         r"next    restore the file or remove the router keys, then remove ~/\.local/state/astack/claude-router/"
                         r"activation\.json \(local edits are kept\)\n$".format(re.escape(Path(sys.executable).name), OTHER_CONVERSATION))
        self.assertEqual(json.loads(self.settings_path.read_text())["theme"], "light")
        self.assertEqual(self.journal()["phase"], "verifying")

    def test_a_stdout_without_a_reader_changes_neither_stderr_nor_the_exit_status(self):
        failed = self.activate_without_a_stdout_reader("exit 5")
        self.assertEqual(failed, (1, "claude-router: activate: exit (conversation {}) exited 5\n".format(CONVERSATION) + UNDONE))
        self.assert_direct_and_state_kept()

        passed = self.activate_without_a_stdout_reader("fake-client " + SESSION)
        self.assertEqual(passed, (0, ""))
        self.assertEqual(self.journal()["phase"], "active")

    def test_closing_the_terminal_during_verification_restores_the_direct_settings(self):
        pidfile, check = self.sleeper("verify-pty")
        command = self.activate_command((SESSION, check))
        env = self.activate_env()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", r"This process .* is multi-threaded", DeprecationWarning)
            process, terminal = pty.fork()
        if process == 0:
            try:
                os.execve(command[0], command, env)
            finally:
                os._exit(127)
        pid = self.wait_for_pid(pidfile)

        os.close(terminal)
        _, status = os.waitpid(process, 0)

        self.assertEqual(os.waitstatus_to_exitcode(status), 1)
        self.assert_direct_and_state_kept()
        self.assertTrue(self.gone(pid), "the continuation {} still runs".format(pid))

    def test_a_settings_file_that_did_not_exist_is_removed_again_after_a_failed_continuation(self):
        self.settings_path.unlink()

        prepared = self.run_activate("--prepare")
        failed = self.run_activate(verify=((SESSION, "exit 7"),))

        self.assertNotIn("backup", prepared.stdout)
        self.assertIn("set     ~/.claude/settings.json apiKeyHelper ([redacted])\n", prepared.stdout)
        self.assertEqual(failed.returncode, 1)
        self.assertFalse(self.settings_path.exists())
        self.assertIsNone(self.journal())
        self.assertNotIn("claude-router", self.manifest().get("settings", {}))

    def test_a_settings_edit_during_a_failed_continuation_is_kept(self):
        edit = "'{}' -c 'import json, sys; p = sys.argv[1]; d = json.load(open(p)); d[\"theme\"] = \"light\"; " \
               "json.dump(d, open(p, \"w\"))' \"{}\"; exit 7".format(sys.executable, self.settings_path)

        result = self.run_activate(verify=((SESSION, edit),))

        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stderr,
                         r"^claude-router: activate: {} \(conversation {}\) exited 7\n"
                         r"claude-router: activate: ~/\.claude/settings\.json changed after an interrupted activation\n"
                         r"        backup  ~/\.local/state/astack/backups/activate-[0-9a-f]{{12}}/settings\.json\n"
                         r"next    restore the file or remove the router keys, then remove ~/\.local/state/astack/claude-router/"
                         r"activation\.json \(local edits are kept\)\n$".format(re.escape(Path(sys.executable).name), CONVERSATION))
        settings = json.loads(self.settings_path.read_text())
        self.assertEqual((settings["theme"], settings["env"]["ANTHROPIC_BASE_URL"]), ("light", self.listen))
        self.assertEqual(self.journal()["phase"], "verifying")

        original = json.loads(self.before)
        settings.update(apiKeyHelper=original["apiKeyHelper"], env=original["env"])
        self.settings_path.write_text(json.dumps(settings, indent=2) + "\n")
        for still in (self.direct(), self.run_activate()):
            self.assertEqual(still.returncode, 1)
            self.assertIn("~/.claude/settings.json changed after an interrupted activation\n", still.stderr)
        (self.state_dir / "activation.json").unlink()
        recovered = self.direct()
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(self.settings_path.read_text()), dict(original, theme="light"))
        self.assertNotIn("claude-router", self.manifest().get("settings", {}))

    def test_local_settings_and_environment_tokens_are_refused_or_named(self):
        content = json.loads(self.before)
        content["env"]["ANTHROPIC_API_KEY"] = "env-secret-value"
        self.settings_path.write_text(json.dumps(content))
        refused = self.run_activate()
        self.assertEqual(refused.returncode, 1)
        self.assertIn("Client settings set env.ANTHROPIC_API_KEY, which Claude Code uses instead of the router's apiKeyHelper", refused.stderr)
        self.settings_path.write_bytes(self.before)

        project = self.root / "project"
        (project / ".claude").mkdir(parents=True)
        (project / ".claude/settings.json").write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:1"}}))
        (project / ".claude/settings.local.json").write_text(json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "local-secret-value"}}))
        self.managed.write_text(json.dumps({"env": {"ANTHROPIC_API_KEY": "managed-secret-value"}}))
        prepared = self.run_activate("--prepare", "--project", str(project), ANTHROPIC_API_KEY="shell-secret-value",
                                     FAKE_LAUNCHER_HAS="ANTHROPIC_AUTH_TOKEN")
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        manager = "launchd" if sys.platform == "darwin" else "systemd"
        warnings = [line for line in prepared.stdout.splitlines() if line.startswith("warn")]
        self.assertEqual(warnings, [
            "warn    {} sets env.ANTHROPIC_BASE_URL, which Claude Code uses instead of ~/.claude/settings.json".format(project / ".claude/settings.json"),
            "warn    {} sets env.ANTHROPIC_AUTH_TOKEN, which Claude Code uses instead of the router's apiKeyHelper".format(project / ".claude/settings.local.json"),
            "warn    {} sets env.ANTHROPIC_API_KEY, which Claude Code uses instead of the router's apiKeyHelper".format(self.managed),
            "warn    {} gives the apps it starts ANTHROPIC_AUTH_TOKEN, which Claude Code uses instead of the router's apiKeyHelper".format(manager),
            "warn    this environment exports ANTHROPIC_API_KEY, which Claude Code uses instead of the router's apiKeyHelper",
        ])
        for secret in ("env-secret-value", "local-secret-value", "managed-secret-value", "shell-secret-value", "direct-key",
                       (self.state_dir / "client-token").read_text().strip()):
            for text in (refused.stdout, refused.stderr, prepared.stdout, prepared.stderr):
                self.assertNotIn(secret, text)

    def test_uninstall_after_activation_restores_the_settings_and_removes_the_record(self):
        self.assertEqual(self.run_activate().returncode, 0)

        self.component(command="uninstall")

        self.assertEqual(self.settings_path.read_bytes(), self.before)
        self.assertIsNone(self.journal())

    def activate_then_lose_the_settings_record(self):
        self.assertEqual(self.run_activate().returncode, 0)
        state_path = self.home / ".local/state/astack/manifest.json"
        state = json.loads(state_path.read_text())
        del state["settings"]
        state_path.write_text(json.dumps(state))

    def test_direct_without_a_settings_record_restores_the_activation_backup(self):
        self.activate_then_lose_the_settings_record()
        self.assertEqual(self.direct("--check").returncode, 0)
        prepared = self.direct("--prepare")
        self.assertEqual(prepared.stdout, "restore ~/.claude/settings.json (activation with no settings record)\n"
                                          "keep    router logins work; 2 assignments\n"
                                          "ready   nothing changed; apply with claude-router direct\n")
        self.assertEqual(self.base_url(), self.listen)

        result = self.direct("--stopped")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "restore ~/.claude/settings.json (activation with no settings record)\n"
                                        "keep    router logins work; 2 assignments\n"
                                        "done    clients connect directly; the router stays installed and stopped\n")
        self.assert_direct_and_state_kept()

    def test_direct_without_a_settings_record_refuses_an_edited_file_and_keeps_the_activation_record(self):
        self.activate_then_lose_the_settings_record()
        edited = json.loads(self.settings_path.read_text())
        edited["theme"] = "light"
        self.settings_path.write_text(json.dumps(edited))
        kept = self.settings_path.read_bytes()

        for result in (self.direct("--check"), self.direct("--prepare"), self.direct()):
            self.assertEqual(result.returncode, 1)
            self.assertIn("claude-router: direct: ~/.claude/settings.json changed after activation\n", result.stderr)
            self.assertRegex(result.stderr, r"        backup  ~/\.local/state/astack/backups/activate-[0-9a-f]{12}/settings\.json\n")
            self.assertEqual(result.stdout, "")
            self.assertEqual(self.settings_path.read_bytes(), kept)
        self.assertEqual(self.journal()["phase"], "active")

    def test_direct_without_a_settings_record_says_it_restores_nothing_when_the_file_is_already_the_original(self):
        self.activate_then_lose_the_settings_record()
        self.settings_path.write_bytes(self.before)
        skip = "skip    ~/.claude/settings.json (already the original; activation with no settings record)\n"

        prepared = self.direct("--prepare")
        result = self.direct("--stopped")

        self.assertEqual(prepared.stdout, skip + "keep    router logins work; 2 assignments\n"
                                                 "ready   nothing changed; apply with claude-router direct\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, skip + "keep    router logins work; 2 assignments\n"
                                               "done    clients connect directly; the router stays installed and stopped\n")
        self.assert_direct_and_state_kept()

    def test_uninstall_without_a_settings_record_refuses_and_keeps_the_activation_until_direct(self):
        self.activate_then_lose_the_settings_record()

        refused = self.component(command="uninstall", success=False)

        self.assertIn("An activation of claude-router has no settings record; run claude-router direct, then uninstall",
                      refused.stderr)
        self.assertEqual(self.base_url(), self.listen)
        self.assertEqual(self.journal()["phase"], "active")
        self.assertTrue((self.data / "lib/claude_router.py").exists())
        self.assertEqual(self.direct("--stopped").returncode, 0)
        self.component(command="uninstall")
        self.assertEqual(self.settings_path.read_bytes(), self.before)

    def test_direct_with_a_missing_state_directory_refuses(self):
        missing = self.root / "no-such-state"

        result = subprocess.run([sys.executable, str(self.data / "lib/claude_router.py"), "direct", "--check",
                                 "--state", str(missing), "--manifest", str(self.home / ".local/state/astack/manifest.json")],
                                capture_output=True, text=True, env=dict(self.env, HOME=str(self.home.resolve())))

        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (1, "", "claude-router: direct: no state directory at {}\n".format(missing)))


LOCKED = "another claude-router setup, account, activate, direct, or uninstall is running\nnext    wait for it to finish, then run again\n"


class OverlapTests(ActivationFixture):
    def wait_for(self, path, process=None):
        deadline = time.time() + 30
        while not path.exists() and (process is None or process.poll() is None) and time.time() < deadline:
            time.sleep(0.05)

    def start_held_activation(self):
        ready, gate = self.root / "verifying", self.root / "gate"
        command = "fake-client {}; touch \"{}\"; while [ ! -e \"{}\" ]; do sleep 0.05; done".format(SESSION, ready, gate)
        process = self.start_activate((SESSION, command), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        self.wait_for(ready)
        self.assertTrue(ready.exists(), "the first activation did not reach verification")
        return process, gate

    def finish(self, process, gate):
        gate.touch()
        out, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 0, err)
        self.assertEqual(out.splitlines()[-1], "done    clients connect through the router; undo with claude-router direct")

    def assert_routed_and_owned(self):
        self.assertEqual(self.base_url(), self.listen)
        self.assertIn("claude-router", self.manifest().get("settings", {}))
        self.assertEqual(self.journal()["phase"], "active")
        self.assertEqual(self.direct().returncode, 0)
        self.assert_direct_and_state_kept()

    def test_an_activation_during_another_s_verification_refuses_without_changes(self):
        first, gate = self.start_held_activation()
        self.acceptance.unlink()

        second = self.run_activate()

        self.assertEqual((second.returncode, second.stdout, second.stderr), (1, "", "claude-router: activate: " + LOCKED))
        self.finish(first, gate)
        self.assert_routed_and_owned()

    def test_direct_during_an_activation_s_verification_refuses_without_changes(self):
        first, gate = self.start_held_activation()

        results = [self.direct(*mode) for mode in (("--check",), ("--prepare",), ())]

        for result in results:
            self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", "claude-router: direct: " + LOCKED))
        self.finish(first, gate)
        self.assert_routed_and_owned()

    def test_a_second_activation_whose_verification_fails_refuses_before_it_touches_the_first(self):
        first, gate = self.start_held_activation()
        ready, gate2 = self.root / "verifying 2", self.root / "gate 2"
        failing = "touch \"{}\"; while [ ! -e \"{}\" ]; do sleep 0.05; done; exit 1".format(ready, gate2)
        second = self.start_activate((OTHER, failing), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: second.poll() is None and second.kill())
        self.wait_for(ready, second)

        self.finish(first, gate)
        gate2.touch()
        out, err = second.communicate(timeout=60)

        self.assertEqual((second.returncode, out, err), (1, "", "claude-router: activate: " + LOCKED))
        self.assert_routed_and_owned()

    def test_uninstall_during_an_activation_s_verification_refuses_without_changes(self):
        first, gate = self.start_held_activation()

        refused = self.component(command="uninstall", success=False)

        self.assertIn("astack: another claude-router setup, account, activate, direct, or uninstall is running; wait for it to finish, then run again",
                      refused.stderr)
        self.finish(first, gate)
        self.assert_routed_and_owned()

    def test_uninstall_while_the_lock_is_held_before_any_activation_record_removes_nothing(self):
        fd = os.open(str(self.state_dir / "activation.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        calls = self.router_log.read_text() if self.router_log.exists() else ""
        files, manifest = self.inventory(), self.manifest()

        results = [self.component(command="uninstall", success=False), self.helper("uninstall")]

        for result in results:
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("astack: another claude-router setup, account, activate, direct, or uninstall is running; wait for it to finish, then run again\n",
                          result.stderr)
        self.assertEqual(self.inventory(), files)
        self.assertEqual(self.manifest(), manifest)
        self.assertEqual(self.router_log.read_text() if self.router_log.exists() else "", calls)

    def test_uninstall_that_cannot_open_the_lock_names_no_next_step(self):
        lock = self.state_dir / "activation.lock"
        lock.unlink(missing_ok=True)
        lock.symlink_to(self.root / "removed state" / "activation.lock")

        results = [self.component(command="uninstall", success=False), self.helper("uninstall")]

        for result, shown in zip(results, (self.state_dir, "~/.local/state/astack/claude-router")):
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertEqual(result.stderr.splitlines()[-1], "astack: no state directory at {}".format(shown))

    def test_direct_runs_under_the_lock_its_caller_holds_and_hands_down(self):
        self.assertEqual(self.run_activate().returncode, 0)
        fd = os.open(str(self.state_dir / "activation.lock"), os.O_RDWR)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        refused = self.direct()
        restored = self.direct("--lock-fd", str(fd), pass_fds=(fd,))

        self.assertEqual((refused.returncode, refused.stderr), (1, "claude-router: direct: " + LOCKED))
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assert_direct_and_state_kept()

    def test_activation_runs_under_the_lock_its_caller_holds_and_hands_down(self):
        fd = os.open(str(self.state_dir / "activation.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        refused = self.run_activate()
        command = self.activate_command((SESSION, "fake-client " + SESSION)) + ["--lock-fd", str(fd)]
        activated = subprocess.run(command, env=self.activate_env(), capture_output=True, text=True, pass_fds=(fd,))

        self.assertEqual((refused.returncode, refused.stderr), (1, "claude-router: activate: " + LOCKED))
        self.assertEqual(activated.returncode, 0, activated.stderr)
        self.assertEqual(self.base_url(), self.listen)


class InterruptedActivationTests(ActivationFixture):
    def load_helper(self):
        spec_path = self.repo / "scripts/claude_router.py"
        sys.path.insert(0, str(spec_path.parent))
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("activation_helper", spec_path)
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
        finally:
            sys.path.remove(str(spec_path.parent))
        for signum in STOPS:
            self.addCleanup(signal.signal, signum, signal.getsignal(signum))
        self.addCleanup(signal.pthread_sigmask, signal.SIG_SETMASK, signal.pthread_sigmask(signal.SIG_BLOCK, []))
        return helper

    def activate_in_process(self, helper, command="fake-client " + SESSION, timeout=60):
        args = helper.argparse.Namespace(acceptance=self.acceptance, claude=self.claude, agent_sdk=self.sdk, settings=None,
                                         project=[], verify=[(SESSION, command)], verify_timeout=timeout, prepare=False,
                                         brief=False)
        stderr = io.StringIO()
        with patch.dict(os.environ, dict(self.env, HOME=str(self.home.resolve())), clear=True), \
                patch.object(helper, "MANAGED_SETTINGS", {sys.platform: self.managed}), \
                redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            code = helper.activate(args, self.home.resolve() / ".local/state/astack", self.home.resolve(), False)
        return code, stderr.getvalue()

    def interrupt(self, during):
        helper = self.load_helper()
        target = {"before the settings write": (helper.astack_settings.SettingsChange, "apply"),
                  "settings write": (helper.files, "save_manifest"),
                  "verification": (helper, "verify_wiring")}[during]
        with patch.object(*target, side_effect=Killed), self.assertRaises(Killed):
            self.activate_in_process(helper)

    def signal_after(self, helper, name, when=lambda *args: True):
        original = getattr(helper, name)

        def call_then_signal(*args, **kwargs):
            result = original(*args, **kwargs)
            if when(*args):
                signal.raise_signal(signal.SIGTERM)
            return result
        return patch.object(helper, name, call_then_signal)

    def test_a_signal_right_after_the_backup_write_starts_no_command_and_rolls_back(self):
        helper = self.load_helper()
        signal.signal(signal.SIGTERM, raise_killed)
        marker = self.root / "command-ran"
        with self.signal_after(helper.files, "atomic_write", lambda path, *rest: path.parent.name.startswith("activate-")):
            result = self.activate_in_process(helper, "touch \"{}\"; fake-client {}".format(marker, SESSION))

        self.assertEqual(result, (1, STOPPED))
        self.assertFalse(marker.exists())
        self.assert_direct_and_state_kept()

    def test_a_signal_before_the_first_command_starts_no_command_and_rolls_back(self):
        helper = self.load_helper()
        marker = self.root / "command-ran"
        with self.signal_after(helper, "write_journal", lambda state, journal: journal["phase"] == "verifying"):
            result = self.activate_in_process(helper, "touch \"{}\"; fake-client {}".format(marker, SESSION))

        self.assertEqual(result, (1, STOPPED))
        self.assertFalse(marker.exists())
        self.assert_direct_and_state_kept()

    def test_a_signal_as_a_command_starts_kills_it_and_rolls_back(self):
        helper = self.load_helper()
        marker = self.root / "command-finished"
        with self.signal_after(helper.subprocess, "Popen", lambda argv, *rest: str(marker) in argv[-1]):
            result = self.activate_in_process(helper, "sleep 1; touch \"{}\"".format(marker), timeout=3)

        self.assertEqual(result, (1, STOPPED))
        self.assertFalse(marker.exists())
        self.assert_direct_and_state_kept()

    def test_a_signal_after_the_last_command_passes_rolls_back_without_signaling_its_group_again(self):
        helper = self.load_helper()
        pidfile = self.root / "last.pid"
        groups, killpg = [], os.killpg

        def record(group, signum):
            groups.append((group, signum))
            return killpg(group, signum)
        with patch.object(helper.os, "killpg", record), self.signal_after(helper, "verify_wiring"):
            result = self.activate_in_process(helper, "echo $$ > \"{}\"; fake-client {}".format(pidfile, SESSION))

        self.assertEqual(result, (1, STOPPED))
        self.assertEqual(groups, [(int(pidfile.read_text()), signal.SIGKILL)])
        self.assert_direct_and_state_kept()

    def test_direct_and_uninstall_after_an_interruption_between_the_settings_write_and_its_record(self):
        self.settings_path.chmod(0o640)
        self.before = self.settings_path.read_bytes()
        self.interrupt("settings write")
        self.assertEqual(self.base_url(), self.listen)
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
        self.assertEqual(self.base_url(), self.listen)

        restored = self.direct("--stopped")

        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(restored.stdout, "restore ~/.claude/settings.json (interrupted activation)\n"
                                          "keep    router logins work; 2 assignments\n"
                                          "done    clients connect directly; the router stays installed and stopped\n")
        self.assert_direct_and_state_kept()
        self.assertEqual(self.settings_path.stat().st_mode & 0o777, 0o640)
        self.component(command="uninstall")

    def test_direct_after_an_interruption_before_the_settings_write_drops_the_record(self):
        self.interrupt("before the settings write")
        self.assertEqual(self.journal()["phase"], "writing")
        self.assertEqual(self.settings_path.read_bytes(), self.before)

        result = self.direct()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_direct_and_state_kept()

    def test_the_next_activation_undoes_an_interrupted_verification_first(self):
        self.interrupt("verification")
        self.assertEqual(self.journal()["phase"], "verifying")
        self.assertIn("claude-router", self.manifest()["settings"])
        prepared = self.run_activate("--prepare")
        self.assertEqual(prepared.stdout, "undo    an interrupted activation of ~/.claude/settings.json; activate restores it first\n"
                                          "ready   nothing changed; activate without --prepare\n")
        self.assertEqual(self.journal()["phase"], "verifying")

        result = self.run_activate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], "undo    ~/.claude/settings.json restored after an interrupted activation")
        self.assertEqual(self.journal()["phase"], "active")
        self.assertEqual(self.direct().returncode, 0)
        self.assert_direct_and_state_kept()

    def test_the_next_activation_says_it_skips_a_settings_file_the_interruption_left_unchanged(self):
        self.interrupt("before the settings write")
        self.assertEqual(self.settings_path.read_bytes(), self.before)
        skip = "skip    ~/.claude/settings.json (already the original; interrupted activation)\n"

        prepared = self.run_activate("--prepare")
        result = self.run_activate()

        self.assertEqual((prepared.returncode, prepared.stdout), (0, skip + "ready   nothing changed; activate without --prepare\n"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0] + "\n", skip)
        self.assertEqual(self.journal()["phase"], "active")

    def test_an_edited_checkout_refuses_before_it_undoes_an_interrupted_activation(self):
        self.interrupt("verification")
        activated = self.settings_path.read_bytes()
        checkout = self.repo / "scripts/astack_settings.py"
        original = checkout.read_bytes()
        checkout.write_bytes(original + b"\n# a local edit\n")

        for result in (self.run_activate("--prepare"), self.run_activate()):
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertIn("the helpers running this command differ from the installed ones\n", result.stderr)
            self.assertEqual(self.settings_path.read_bytes(), activated)
            self.assertEqual(self.journal()["phase"], "verifying")
        checkout.write_bytes(original)
        self.assertEqual(self.direct().returncode, 0)
        self.assert_direct_and_state_kept()

    def test_an_edit_after_an_interruption_refuses_and_is_kept(self):
        self.interrupt("verification")
        edited = json.loads(self.settings_path.read_text())
        edited["theme"] = "light"
        self.settings_path.write_text(json.dumps(edited))
        kept = self.settings_path.read_bytes()

        for result in (self.direct("--check"), self.direct("--prepare"), self.direct(), self.run_activate("--prepare"), self.run_activate()):
            self.assertEqual(result.returncode, 1)
            self.assertIn("~/.claude/settings.json changed after an interrupted activation\n", result.stderr)
            self.assertRegex(result.stderr, r"        backup  ~/\.local/state/astack/backups/activate-[0-9a-f]{12}/settings\.json\n")
            self.assertEqual(result.stdout, "")
            self.assertEqual(self.settings_path.read_bytes(), kept)
        self.assertEqual(self.journal()["phase"], "verifying")

    def test_a_changed_backup_refuses_the_undo_and_keeps_the_settings(self):
        self.interrupt("verification")
        activated = self.settings_path.read_bytes()
        backup = Path(self.journal()["backup"])
        backup.write_text('{"theme": "tampered"}\n')

        for result in (self.direct("--check"), self.direct()):
            self.assertEqual(result.returncode, 1)
            self.assertIn("claude-router: direct: the backup of ~/.claude/settings.json changed: ", result.stderr)
            self.assertEqual(self.settings_path.read_bytes(), activated)
        self.assertEqual(self.journal()["phase"], "verifying")


class CodeDigestTests(ActivationFixture):
    def setUp(self):
        super().setUp()
        self.commit_checkout()
        self.component()
        self.fake_router.status["code_sha256"] = self.installed_code()
        self.component_dir = self.repo / "components/claude-router"

    def test_the_code_digest_covers_tracked_router_code_and_skips_docs_tests_testdata_and_the_record(self):
        (self.component_dir / "accepted.json").write_text("{}\n")
        self.commit_checkout("record")
        listed = subprocess.run(["git", "-C", str(self.component_dir), "ls-files"], capture_output=True, text=True, check=True).stdout.split()
        code = sorted(name for name in listed if not name.endswith((".md", "_test.go")) and "testdata" not in name.split("/")
                      and name != "accepted.json")
        self.assertIn("cmd/claude-router/main.go", code)
        self.assertIn("go.sum", code)
        expected = hashlib.sha256("".join("{}\0{}\n".format(name, hashlib.sha256((self.component_dir / name).read_bytes()).hexdigest())
                                          for name in code).encode()).hexdigest()

        self.assertEqual(self.code_only(), {"code_sha256": expected, "helpers_sha256": self.identity["helpers_sha256"]})

    def test_a_record_matches_by_code_digest_at_another_commit(self):
        code = self.code_only()
        self.accept(head="0" * 40, binary_sha256="b" * 64, **code)
        readme = self.component_dir / "README.md"
        readme.write_text(readme.read_text() + "\nAnother paragraph.\n")
        self.commit_checkout("docs")
        self.component()

        self.assertEqual(self.installed_code(), code["code_sha256"])
        result = self.run_activate(verify=())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.journal()["phase"], "active")

    def test_an_edit_inside_the_code_set_refuses_install_and_an_edit_outside_does_not(self):
        code = self.code_only()["code_sha256"]
        for name in ("README.md", "cmd/claude-router/main_test.go", "cmd/claude-router/testdata/status/healthy.txt"):
            with self.subTest(name):
                path = self.component_dir / name
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                self.addCleanup(path.write_bytes, original)
                self.component()
                self.assertEqual(self.installed_code(), code)
                path.write_bytes(original)

        main = self.component_dir / "cmd/claude-router/main.go"
        original = main.read_bytes()
        extra = self.component_dir / "cmd/claude-router/extra.go"
        for path, content, restore in ((main, original + b"\n// a local edit\n", lambda: main.write_bytes(original)),
                                       (extra, b"package main\n", extra.unlink)):
            with self.subTest(path.name):
                path.write_bytes(content)
                try:
                    before = self.inventory()
                    result = self.component(success=False)
                    self.assertEqual(result.stderr, "astack: claude-router code differs from the checkout's commit: cmd/claude-router/{}; "
                                                    "commit or revert it, then install again\n".format(path.name))
                    self.assertEqual(self.inventory(), before)
                finally:
                    restore()


class UpdateOverActivationTests(ActivationFixture):
    def test_an_update_restarts_an_activated_router_and_keeps_it_active_when_check_passes(self):
        self.assertEqual(self.run_activate().returncode, 0)
        self.router_log.write_text("")
        (self.repo / "components/claude-router/accepted.json").write_text(self.acceptance.read_text())
        self.fake_router.status["binary_sha256"] = hashlib.sha256(b"#!/bin/sh\n# build 2\nprintf '%s\\n' \"$*\" >> \"$FAKE_ROUTER_LOG\"\n").hexdigest()

        result = self.component(FAKE_BUILD="2")

        self.assertEqual(self.router_log.read_text(), "stop -state {0}\nstart -state {0}\n".format(self.state_dir))
        self.assertIn("check     passed\n", result.stdout)
        self.assertEqual(self.base_url(), self.listen)
        self.assertEqual(self.journal()["phase"], "active")

    def test_an_update_whose_check_fails_connects_clients_directly(self):
        self.assertEqual(self.run_activate().returncode, 0)
        self.router_log.write_text("")
        (self.repo / "components/claude-router/accepted.json").write_text(self.acceptance.read_text())
        built = hashlib.sha256(b"#!/bin/sh\n# build 2\nprintf '%s\\n' \"$*\" >> \"$FAKE_ROUTER_LOG\"\n").hexdigest()

        result = self.component(FAKE_BUILD="2", success=False)

        self.assertIn("fail      code differs from the accepted record ({}/{})\n".format(*self.router().host_platform()) +
                      "          binary: installed {}, running {}\n".format(built[:12], self.binary_sha256[:12]), result.stdout)
        self.assertIn("check     failed\n", result.stdout)
        self.assertIn("next      claude-router check\n", result.stdout)
        self.assertIn("astack: the updated router failed its check; Claude Code connects directly\n", result.stderr)
        self.assertEqual(self.router_log.read_text(), "stop -state {0}\nstart -state {0}\nstop -state {0}\n".format(self.state_dir))
        self.assert_direct_and_state_kept()

    def prepare_update(self, check_passes=True):
        self.fake_router.status["binary_sha256"] = self.binary_sha256
        self.assertEqual(self.run_activate().returncode, 0)
        self.router_log.write_text("")
        (self.repo / "components/claude-router/accepted.json").write_text(self.acceptance.read_text())
        if check_passes:
            self.fake_router.status["binary_sha256"] = hashlib.sha256(
                b"#!/bin/sh\n# build 2\nprintf '%s\\n' \"$*\" >> \"$FAKE_ROUTER_LOG\"\n").hexdigest()

    def test_an_update_that_fails_to_stop_start_or_check_the_router_connects_clients_directly(self):
        for step in ("stop", "start", "check", "stop after a failed check"):
            with self.subTest(step):
                self.prepare_update(check_passes=step != "stop after a failed check")
                module = self.module()
                helper = module.claude_router
                service_command, stops = helper.service_command, []

                def failing_command(lay, *arguments):
                    stops.append(arguments[0])
                    if (step, arguments[0]) in (("stop", "stop"), ("start", "start")) or (
                            step == "stop after a failed check" and stops.count("stop") == 2):
                        raise ValueError("claude-router {} failed: injected".format(arguments[0]))
                    service_command(lay, *arguments)

                def failing_check(*arguments):
                    raise OSError("events.jsonl unreadable")

                with patch.object(helper, "service_command", failing_command), \
                        patch.object(helper, "run_check", failing_check if step == "check" else helper.run_check), \
                        self.assertRaisesRegex(ValueError, "Claude Code connects directly$"):
                    self.run_module(module, {"FAKE_BUILD": "2", "HOME": str(self.home.resolve())},
                                    "install", "--home", str(self.home), "--component", "claude-router")

                self.assert_direct_and_state_kept()
                self.component()

    def test_a_dry_run_over_an_activated_router_prints_what_the_install_then_does(self):
        self.prepare_update()

        preview = self.component("--dry-run", FAKE_BUILD="2")
        self.assertEqual(self.router_log.read_text(), "")
        result = self.component(FAKE_BUILD="2")

        lines = preview.stdout.splitlines()
        self.assertEqual(lines[-3:], ["RESTART {} dev.astack.claude-router".format(self.manager_name),
                                      "CHECK claude-router check; claude-router direct if it fails",
                                      "Preview complete for claude-router."])
        self.assertEqual(result.stdout.splitlines()[:len(lines) - 1], lines[:-1])
        self.assertIn("check     passed\n", result.stdout)
