"""Run author-skill's run_case.py against a fake `claude` that records what each session received."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parent.parent
SCRIPT_PATH = Path(".agents/skills/author-skill/scripts/run_case.py")
UNCOPIED = shutil.ignore_patterns(".git", ".claude", "__pycache__", "node_modules")

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, time
from pathlib import Path

config = Path(os.environ["CLAUDE_CONFIG_DIR"])
argv = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({
        "argv": argv,
        "cwd": os.getcwd(),
        "parent_session": os.environ.get("CLAUDECODE"),
        "settings": json.loads((config / "settings.json").read_text()),
        "credentials": (config / ".credentials.json").exists(),
        "tdd": (config / "skills/tdd/SKILL.md").read_text(),
        "instructions": (config / "CLAUDE.md").read_text(),
    }) + "\n")

def emit(event):
    print(json.dumps(event), flush=True)

prompt = argv[argv.index("-p") + 1]
mode = os.environ.get("FAKE_MODE", "")
if mode == "signed-out":
    emit({"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in", "total_cost_usd": 0})
    sys.exit(1)
if mode == "crash":
    print("fatal: config unreadable", file=sys.stderr)
    sys.exit(2)
if mode == "api-error":
    emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "",
          "errors": ["API Error: 529 overloaded"], "total_cost_usd": 0})
    sys.exit(0)
emit({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "t0", "name": "Skill", "input": {}},
    {"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": "tdd"}},
    {"type": "tool_use", "id": "t7", "name": "Skill", "input": {"skill": "missing-skill"}},
    {"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": os.getcwd() + "/README.md"}},
    {"type": "tool_use", "id": "t3", "name": "Edit", "input": {"file_path": os.getcwd() + "/skills/tdd/SKILL.md"}},
    {"type": "tool_use", "id": "t4", "name": "Write", "input": {"file_path": os.getcwd() + "/notes.md"}},
    {"type": "tool_use", "id": "t5", "name": "Bash", "input": {"command": "git log --oneline"}},
    {"type": "tool_use", "id": "t6", "name": "Bash", "input": {"command": "ls missing"}},
]}})
emit({"type": "user", "message": {"content": [
    {"type": "tool_result", "tool_use_id": "t7", "is_error": True, "content": "Unknown skill: missing-skill"},
    {"type": "tool_result", "tool_use_id": "t5", "is_error": False, "content": "abc123 subject"},
    {"type": "tool_result", "tool_use_id": "t6", "is_error": True, "content": "Exit code 2\nls: missing: No such file"},
]}})
for call, tool in (("t3", "Edit"), ("t4", "Write")):
    denial = "Permission to use %s has been denied." % tool
    emit({"type": "system", "subtype": "permission_denied", "tool_name": tool, "tool_use_id": call, "message": denial})
    emit({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": call, "is_error": True, "content": denial}]}})
if mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(45)"])
    record = Path(os.environ["FAKE_LOG"] + ".child")
    Path(str(record) + ".tmp").write_text(str(child.pid))
    os.replace(str(record) + ".tmp", record)
    time.sleep(60)
session = argv[argv.index("--resume") + 1] if "--resume" in argv else "session-%d" % os.getpid()
emit({"type": "result", "subtype": "success", "is_error": False, "session_id": session,
      "result": "Answered %r in %s" % (prompt, session), "total_cost_usd": 0.25})
'''


class RunCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        checkouts = tempfile.TemporaryDirectory()
        cls.addClassCleanup(checkouts.cleanup)
        cls.checkout = Path(checkouts.name) / "checkout"
        shutil.copytree(REPO, cls.checkout, ignore=UNCOPIED)

    def copy_checkout(self):
        checkout = self.temp / "checkout"
        shutil.copytree(self.checkout, checkout)
        return checkout

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        bin_dir = self.temp / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "claude"
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o755)
        self.user_config = self.temp / "user-config"
        self.user_config.mkdir()
        (self.user_config / ".credentials.json").write_text("{}")
        (self.user_config / "settings.json").write_text(json.dumps({
            "apiKeyHelper": "cat token",
            "env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:1"},
            "model": "opus",
            "autoMemoryEnabled": False,
            "permissions": {"allow": ["Bash", "Edit"]},
            "hooks": {"PreToolUse": []},
        }))
        self.log = self.temp / "log.jsonl"
        self.env = dict(os.environ, PATH="{}{}{}".format(bin_dir, os.pathsep, os.environ["PATH"]),
                        CLAUDE_CONFIG_DIR=str(self.user_config), FAKE_LOG=str(self.log), CLAUDECODE="1")

    def recorded_child_exits(self):
        record = Path(str(self.log) + ".child")
        pid = int(record.read_text())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.05)
        return False

    def kill_recorded_child(self):
        record = Path(str(self.log) + ".child")
        if record.exists():
            try:
                os.kill(int(record.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass

    def run_case(self, *arguments, mode="", checkout=None):
        script = (checkout or self.checkout) / SCRIPT_PATH
        result = subprocess.run(
            [sys.executable, str(script), "--out", str(self.temp / "out"), *arguments],
            env=dict(self.env, FAKE_MODE=mode), capture_output=True, text=True, timeout=60,
        )
        sessions = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return result, sessions

    def test_each_run_loads_this_checkout_with_sign_in_and_memory_settings_and_read_only_tools(self):
        result, sessions = self.run_case("--runs", "2", "Add a feature")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(sessions), 2)
        for session in sessions:
            self.assertEqual(session["tdd"], (self.checkout / "skills/tdd/SKILL.md").read_text())
            shared = (self.checkout / "instructions/AGENTS.md").read_text().strip()
            self.assertIn(shared, session["instructions"])
            self.assertEqual(session["settings"], {"apiKeyHelper": "cat token",
                                                   "env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:1"},
                                                   "model": "opus", "autoMemoryEnabled": False})
            self.assertFalse(session["credentials"])
            self.assertIsNone(session["parent_session"])
            self.assertEqual(Path(session["cwd"]).resolve(), self.checkout.resolve())
            argv = session["argv"]
            self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
            self.assertEqual(argv[argv.index("--setting-sources") + 1], "user,project")
            self.assertCountEqual(argv[argv.index("--allowedTools") + 1].split(","), ["Read", "Grep", "Glob", "Skill"])

    def test_report_lists_skills_actions_and_answers_per_run_and_in_total(self):
        result, _ = self.run_case("--runs", "2", "Add a feature")

        report = result.stdout
        self.assertIn("Skills: tdd\n", report)
        edit_position = report.index("Edit skills/tdd/SKILL.md (denied)")
        self.assertLess(edit_position, report.index("Write notes.md (denied)"))
        self.assertIn("Bash git log --oneline\n", report)
        self.assertIn("Bash ls missing (failed: Exit code 2)", report)
        self.assertNotIn("Read README.md", report)
        self.assertIn("Answered 'Add a feature' in session-", report)
        self.assertIn("Skills: tdd 2/2\n", report)
        self.assertIn("Skill missing-skill (failed: Unknown skill: missing-skill)", report)
        self.assertIn("Denied: Edit 2/2, Write 2/2\n", report)
        self.assertIn("$0.50", report)
        self.assertIn(str(self.temp / "out"), report)
        self.assertEqual(len(list((self.temp / "out").glob("*.jsonl"))), 2)

    def test_refuses_to_run_when_the_checkout_could_approve_more_than_reading(self):
        tdd = (self.checkout / "skills/tdd/SKILL.md").read_text()
        loosening = {
            "an allow rule": ({".claude/settings.json": json.dumps({"permissions": {"allow": ["Write"]}})},
                              "permissions.allow"),
            "a hook": ({".claude/settings.json": json.dumps({"hooks": {"PreToolUse": []}})}, "hooks"),
            "a sandbox": ({".claude/settings.json": json.dumps({"sandbox": {"enabled": True}})}, "sandbox"),
            "malformed settings": ({".claude/settings.json": "{not json"}, "not a JSON object"),
            "a project command": ({".claude/commands/ship.md": "ship it"}, ".claude/commands"),
            "a skill's allowed-tools": (
                {"skills/tdd/SKILL.md": tdd.replace("description:", "allowed-tools: Write\ndescription:", 1)},
                "skills/tdd/SKILL.md"),
        }
        for case, (files, named) in loosening.items():
            with self.subTest(case):
                checkout = self.copy_checkout()
                for path, contents in files.items():
                    (checkout / path).parent.mkdir(parents=True, exist_ok=True)
                    (checkout / path).write_text(contents)
                refused, sessions = self.run_case("--runs", "1", "Add a feature", checkout=checkout)
                shutil.rmtree(checkout)

                self.assertNotEqual(refused.returncode, 0)
                self.assertIn(named, refused.stderr)
                self.assertNotIn("Traceback", refused.stderr)
                self.assertEqual(sessions, [])

        checkout = self.copy_checkout()
        narrowing = {"permissions": {"deny": ["WebFetch"], "ask": ["Bash(rm:*)"]}}
        (checkout / ".claude/worktrees/probe").mkdir(parents=True)
        (checkout / ".claude/settings.json").write_text(json.dumps(narrowing))
        (checkout / ".claude/settings.local.json").write_text(json.dumps({"permissions": {"allow": ["Write"]}}))
        allowed, sessions = self.run_case("--runs", "1", "Add a feature", checkout=checkout)

        self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
        self.assertEqual(len(sessions), 1)

    def test_followup_turn_resumes_the_same_session(self):
        result, sessions = self.run_case("--runs", "1", "--then", "Yes, do it", "Is the guidance clear?")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        first, second = sessions
        self.assertNotIn("--resume", first["argv"])
        session = second["argv"][second["argv"].index("--resume") + 1]
        self.assertEqual(second["argv"][second["argv"].index("-p") + 1], "Yes, do it")
        self.assertIn("Answered 'Is the guidance clear?' in {}".format(session), result.stdout)
        self.assertIn("Answered 'Yes, do it' in {}".format(session), result.stdout)

    def test_run_that_times_out_keeps_its_actions_and_fails_the_command(self):
        self.addCleanup(self.kill_recorded_child)
        started = time.monotonic()
        result, _ = self.run_case("--runs", "1", "--timeout", "1", "Add a feature", mode="hang")

        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(result.returncode, 1)
        self.assertIn("timed out after 1 s", result.stdout)
        self.assertIn("Edit skills/tdd/SKILL.md (denied)", result.stdout)
        self.assertTrue(self.recorded_child_exits())

    def test_terminating_the_script_stops_every_session(self):
        self.addCleanup(self.kill_recorded_child)
        script = subprocess.Popen(
            [sys.executable, str(self.checkout / SCRIPT_PATH), "--out", str(self.temp / "out"), "--runs", "1",
             "Add a feature"],
            env=dict(self.env, FAKE_MODE="hang"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        record = Path(str(self.log) + ".child")
        deadline = time.monotonic() + 30
        while not record.exists() and time.monotonic() < deadline:
            time.sleep(0.05)

        script.send_signal(signal.SIGTERM)
        script.communicate(timeout=30)

        self.assertNotEqual(script.returncode, 0)
        self.assertTrue(self.recorded_child_exits())

    def test_signed_out_session_is_reported_and_fails_the_command(self):
        result, _ = self.run_case("--runs", "1", "Add a feature", mode="signed-out")

        self.assertEqual(result.returncode, 1)
        self.assertIn("Not logged in", result.stdout)

    def test_crash_without_a_result_reports_stderr_and_fails_the_command(self):
        result, _ = self.run_case("--runs", "1", "Add a feature", mode="crash")

        self.assertEqual(result.returncode, 1)
        self.assertIn("fatal: config unreadable", result.stdout)

    def test_error_result_fails_the_command_even_when_claude_exits_0(self):
        result, _ = self.run_case("--runs", "1", "Add a feature", mode="api-error")

        self.assertEqual(result.returncode, 1)
        self.assertIn("API Error: 529 overloaded", result.stdout)


if __name__ == "__main__":
    unittest.main()
