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
if mode == "garbled-stderr":
    sys.stderr.buffer.write(b"warning: \xff\xfe\n")
    sys.stderr.buffer.flush()
emit({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "t0", "name": "Skill", "input": {}},
    {"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": "tdd"}},
    {"type": "tool_use", "id": "t7", "name": "Skill", "input": {"skill": "missing-skill"}},
    {"type": "tool_use", "id": "t8", "name": "Skill", "input": {"skill": "denied-skill"}},
    {"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": os.getcwd() + "/README.md"}},
    {"type": "tool_use", "id": "t3", "name": "Edit", "input": {"file_path": os.getcwd() + "/skills/tdd/SKILL.md"}},
    {"type": "tool_use", "id": "t4", "name": "Write", "input": {"file_path": os.getcwd() + "/notes.md"}},
    {"type": "tool_use", "id": "t5", "name": "Bash", "input": {"command": "git log --oneline"}},
    {"type": "tool_use", "id": "t6", "name": "Bash", "input": {"command": "ls missing"}},
]}})
emit({"type": "user", "message": {"content": [
    {"type": "tool_result", "tool_use_id": "t1", "content": "Launching skill: tdd"},
    {"type": "tool_result", "tool_use_id": "t7", "is_error": True, "content": "Unknown skill: missing-skill"},
    {"type": "tool_result", "tool_use_id": "t5", "content": "abc123 subject"},
    {"type": "tool_result", "tool_use_id": "t6", "is_error": True, "content": "Exit code 2\nls: missing: No such file"},
]}})
emit({"type": "system", "subtype": "permission_denied", "tool_name": "Skill", "tool_use_id": "t8",
      "message": "Permission to use Skill has been denied."})
for call, tool in (("t3", "Edit"), ("t4", "Write")):
    denial = "Permission to use %s has been denied." % tool
    emit({"type": "system", "subtype": "permission_denied", "tool_name": tool, "tool_use_id": call, "message": denial})
    emit({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": call, "is_error": True, "content": denial}]}})
if mode == "hang":
    emit({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t9", "name": "Skill", "input": {"skill": "interrupted-skill"}}]}})
    sys.stdout.buffer.write(b'{"type": "assistant", "message": "\xe2\x9c')
    sys.stdout.buffer.flush()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(45)"])
    record = Path(os.environ["FAKE_LOG"] + ".child")
    Path(str(record) + ".tmp").write_text(str(child.pid))
    os.replace(str(record) + ".tmp", record)
    time.sleep(60)
session = argv[argv.index("--resume") + 1] if "--resume" in argv else "session-%d" % os.getpid()
if mode == "dashed-answer":
    emit({"type": "result", "subtype": "success", "is_error": False, "session_id": session,
          "result": "Tests pass \u2014 done", "total_cost_usd": 0.25})
    sys.exit(0)
if mode == "line-separator":
    answer = {"type": "result", "subtype": "success", "is_error": False, "session_id": session,
              "result": "First line\u2028second line", "total_cost_usd": 0.25}
    sys.stdout.buffer.write((json.dumps(answer, ensure_ascii=False) + "\n").encode())
    sys.exit(0)
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
        checkout = Path(tempfile.mkdtemp(dir=self.temp)) / "checkout"
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
        self.child_record = Path(str(self.log) + ".child")
        self.env = dict(os.environ, PATH="{}{}{}".format(bin_dir, os.pathsep, os.environ["PATH"]),
                        CLAUDE_CONFIG_DIR=str(self.user_config), FAKE_LOG=str(self.log), CLAUDECODE="1")

    def recorded_child_exits(self):
        pid = int(self.child_record.read_text())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.05)
        return False

    def kill_recorded_child(self):
        if self.child_record.exists():
            try:
                os.kill(int(self.child_record.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass

    def start(self, *arguments, mode="", checkout=None, out=None):
        script = (checkout or self.checkout) / SCRIPT_PATH
        out = out or (checkout.parent / "out" if checkout else self.temp / "out")
        return subprocess.Popen([sys.executable, str(script), "--out", str(out), *arguments],
                                env=dict(self.env, FAKE_MODE=mode), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)

    def finish(self, script, timeout=60):
        try:
            return script.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            script.kill()
            script.communicate()
            raise

    def run_case(self, *arguments, **options):
        script = self.start(*arguments, **options)
        stdout, stderr = self.finish(script)
        result = subprocess.CompletedProcess(script.args, script.returncode, stdout, stderr)
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

    def test_sessions_use_the_requested_model_and_turn_limit(self):
        result, (session,) = self.run_case("--runs", "1", "--model", "sonnet", "--max-turns", "7", "Add a feature")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        argv = session["argv"]
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet")
        self.assertEqual(argv[argv.index("--max-turns") + 1], "7")

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
        self.assertIn("Skill denied-skill (denied)", report)
        self.assertIn("Denied: Edit 2/2, Skill 2/2, Write 2/2\n", report)
        self.assertIn("$0.50", report)
        self.assertIn(str(self.temp / "out"), report)
        self.assertEqual(len(list((self.temp / "out").glob("*.jsonl"))), 2)

    def test_refuses_to_run_when_the_checkout_could_approve_more_than_reading(self):
        tdd = (self.checkout / "skills/tdd/SKILL.md").read_text(encoding="utf-8")
        spaced = tdd.replace("---\n", "--- \n", 1).replace("\n---\n", "\n---\t\n", 1)
        allow = json.dumps({"permissions": {"allow": ["Write"]}})

        def write(path, text):
            def setup(checkout):
                (checkout / path).parent.mkdir(parents=True, exist_ok=True)
                (checkout / path).write_text(text, encoding="utf-8")
            return setup

        def plant_skill_and_restrict(folder, mode):
            def setup(checkout):
                (checkout / "components/demo/.claude/skills/ship").mkdir(parents=True)
                (checkout / folder).chmod(mode)
                self.addCleanup((checkout / folder).chmod, 0o755)
            return setup

        def unsearchable_claude(checkout):
            folder = checkout / "components/demo/.claude"
            folder.mkdir(parents=True)
            (checkout / "allow.json").write_text(allow)
            (folder / "settings.local.json").symlink_to(checkout / "allow.json")
            folder.chmod(0o444)
            self.addCleanup(folder.chmod, 0o755)

        def unreadable_skill(checkout):
            (checkout / "skills/tdd/SKILL.md").chmod(0o000)
            self.addCleanup((checkout / "skills/tdd/SKILL.md").chmod, 0o644)

        def unsearchable_skill_folder(checkout):
            folder = checkout / "skills/tdd/references"
            folder.mkdir()
            (folder / "notes.md").write_text("notes")
            folder.chmod(0o444)
            self.addCleanup(folder.chmod, 0o755)

        def link_parent(checkout):
            target = checkout.parent / "outside"
            (target / ".claude/skills/ship").mkdir(parents=True)
            (target / ".claude/skills/ship/SKILL.md").write_text("ship it")
            (checkout / "components").mkdir(exist_ok=True)
            (checkout / "components/linked").symlink_to(target)

        def link_folder(link, target):
            def setup(checkout):
                (checkout / target / "commands").mkdir(parents=True)
                (checkout / target / "commands/ship.md").write_text("ship it")
                (checkout / link).parent.mkdir(parents=True, exist_ok=True)
                (checkout / link).symlink_to(os.path.relpath(checkout / target, (checkout / link).parent))
            return setup

        def link_settings(checkout):
            (checkout / "allow.json").write_text(allow)
            (checkout / ".claude").mkdir(exist_ok=True)
            (checkout / ".claude/settings.json").symlink_to("../allow.json")

        def skill_frontmatter(lines):
            return write("skills/tdd/SKILL.md", tdd.replace("description:", lines + "\ndescription:", 1))

        def after_description(lines):
            closing = tdd.index("\n---\n")
            return write("skills/tdd/SKILL.md", tdd[:closing] + lines + tdd[closing:])

        loosening = {
            "an allow rule": (write(".claude/settings.json", allow), "permissions.allow"),
            "a hook": (write(".claude/settings.json", json.dumps({"hooks": {"PreToolUse": []}})), "hooks"),
            "a sandbox": (write(".claude/settings.json", json.dumps({"sandbox": {"enabled": True}})), "sandbox"),
            "malformed settings": (write(".claude/settings.json", "{not json"), "not a JSON object"),
            "a project command": (write(".claude/commands/ship.md", "ship it"), ".claude/commands"),
            "a nested project skill": (write("components/demo/.claude/skills/ship/SKILL.md", "ship it"),
                                       "components/demo/.claude/skills"),
            "a nested settings.json that allows": (write("components/demo/.claude/settings.json", allow),
                                                   "components/demo/.claude/settings.json"),
            "a project skill in a worktree": (write(".claude/worktrees/probe/.claude/skills/ship/SKILL.md", "ship it"),
                                              ".claude/worktrees/probe/.claude/skills"),
            "a project skill in node_modules": (write("node_modules/pkg/.claude/skills/ship/SKILL.md", "ship it"),
                                                "node_modules/pkg/.claude/skills"),
            "a symlinked .claude": (link_folder(".claude", "../linked"), ".claude is a symlink"),
            "a symlinked nested .claude": (link_folder("components/demo/.claude", "../linked"),
                                           "components/demo/.claude is a symlink"),
            "a .claude under a symlinked folder": (link_parent, "components/linked/.claude/skills"),
            "a nested .claude symlinked to a folder in the checkout": (
                link_folder("services/api/.claude", "common/claude"), "services/api/.claude is a symlink"),
            "a .claude first reached through a symlink": (
                link_folder("claude-config", "packages/app/.claude"), "packages/app/.claude/commands exists"),
            "a symlinked settings.json": (link_settings, ".claude/settings.json is a symlink"),
            "an unreadable nested .claude": (plant_skill_and_restrict("components/demo/.claude", 0o111),
                                             "components/demo/.claude cannot be read"),
            "a .claude in a folder that cannot be searched": (plant_skill_and_restrict("components/demo", 0o444),
                                                              "components/demo cannot be read"),
            "a .claude that cannot be searched": (unsearchable_claude, "components/demo/.claude cannot be read"),
            "an unreadable folder": (plant_skill_and_restrict("components", 0o000), "components cannot be read"),
            "an unreadable SKILL.md": (unreadable_skill, "skills/tdd/SKILL.md cannot be read"),
            "a skill folder that cannot be searched": (unsearchable_skill_folder,
                                                       "skills/tdd/references cannot be read"),
            "a skill's allowed-tools": (skill_frontmatter("allowed-tools: Write"), "skills/tdd/SKILL.md"),
            "a quoted allowed-tools key": (skill_frontmatter('"allowed-tools": Write'), "skills/tdd/SKILL.md"),
            "an escaped allowed-tools key": (skill_frontmatter('"allowed\\x2dtools": Write'), "skills/tdd/SKILL.md"),
            "allowed-tools after a dashed value": (after_description("\nargument-hint: a --- b\nallowed-tools: Write"),
                                                   "skills/tdd/SKILL.md"),
            "allowed-tools after a line separator and ---": (after_description("\u2028---\nallowed-tools: Write"),
                                                             "skills/tdd/SKILL.md"),
            "allowed-tools in frontmatter between spaced --- lines": (
                write("skills/tdd/SKILL.md", spaced.replace("\n---\t\n", "\nallowed-tools: Write\n---\t\n", 1)),
                "skills/tdd/SKILL.md declares allowed-tools"),
            "allowed-tools in frontmatter closed by ----": (
                write("skills/tdd/SKILL.md", tdd.replace("\n---\n", "\nallowed-tools: Write\n----\n", 1)),
                "skills/tdd/SKILL.md"),
        }
        for case, (setup, named) in loosening.items():
            with self.subTest(case):
                checkout = self.copy_checkout()
                setup(checkout)
                self.log.unlink(missing_ok=True)
                refused, sessions = self.run_case("--runs", "1", "Add a feature", checkout=checkout)

                self.assertNotEqual(refused.returncode, 0)
                self.assertIn(named, refused.stderr)
                self.assertNotIn("Traceback", refused.stderr)
                self.assertEqual(sessions, [])

        checkout = self.copy_checkout()
        narrowing = {"$schema": "https://json.schemastore.org/claude-code-settings.json",
                     "permissions": {"deny": ["WebFetch"], "ask": ["Bash(rm:*)"]}}
        (checkout / ".claude/worktrees/probe/.claude").mkdir(parents=True)
        (checkout / ".claude/worktrees/probe/.claude/settings.local.json").write_text("{}")
        (checkout / ".claude/settings.json").write_text(json.dumps(narrowing))
        (checkout / ".claude/settings.local.json").write_text(json.dumps({"permissions": {"allow": ["Write"]}}))
        (checkout / "loop").symlink_to(".")
        (checkout / "components/note").mkdir(parents=True)
        (checkout / "components/note/.claude").write_text("a file, not a folder")
        (checkout / ".venv/bin").mkdir(parents=True)
        (checkout / ".venv/bin/python").symlink_to("../../removed-python/bin/python3")
        (checkout / "vendor/lib").mkdir(parents=True)
        (checkout / "vendor/lib/lib").symlink_to("lib")
        (checkout / "vendor/notes").symlink_to("../README.md/notes")
        (checkout / "skills/tdd/SKILL.md").write_text(spaced, encoding="utf-8")
        self.log.unlink(missing_ok=True)
        allowed, sessions = self.run_case("--runs", "1", "Add a feature", checkout=checkout)

        self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
        self.assertEqual(len(sessions), 1)

    def git(self, folder, *arguments):
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.com",
                   GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.com")
        subprocess.run(["git", "-C", str(folder), *arguments], env=env, check=True, capture_output=True)

    def linked_worktree(self):
        main = self.copy_checkout()
        for arguments in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "checkout"],
                          ["worktree", "add", "-q", "--detach", str(main.parent / "lane")]):
            self.git(main, *arguments)
        return main

    def test_refuses_a_skill_in_the_main_checkout_that_a_linked_worktree_loads(self):
        main = self.linked_worktree()
        (main / ".claude").mkdir()
        (main / ".claude/settings.local.json").write_text("{}")

        allowed, sessions = self.run_case("--runs", "1", "Add a feature", checkout=main.parent / "lane")

        self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
        self.assertEqual(len(sessions), 1)

        (main / ".claude/skills/ship").mkdir(parents=True)
        (main / ".claude/skills/ship/SKILL.md").write_text("ship it")
        self.log.unlink()

        refused, sessions = self.run_case("--runs", "1", "Add a feature", checkout=main.parent / "lane",
                                          out=main.parent / "refused")

        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("checkout/.claude/skills exists", refused.stderr)
        self.assertNotIn("Traceback", refused.stderr)
        self.assertEqual(sessions, [])

    def test_finds_the_main_checkout_when_git_dir_points_elsewhere(self):
        main = self.linked_worktree()
        (main / ".claude/skills/ship").mkdir(parents=True)
        (main / ".claude/skills/ship/SKILL.md").write_text("ship it")
        decoy = Path(tempfile.mkdtemp(dir=self.temp)) / "decoy"
        decoy.mkdir()
        self.git(decoy, "init", "-q")
        self.env["GIT_DIR"] = str(decoy / ".git")

        refused, sessions = self.run_case("--runs", "1", "Add a feature", checkout=main.parent / "lane")

        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("checkout/.claude/skills exists", refused.stderr)
        self.assertEqual(sessions, [])

    def test_refuses_an_unreadable_settings_json_only_as_unreadable(self):
        checkout = self.copy_checkout()
        settings = checkout / ".claude/settings.json"
        settings.parent.mkdir()
        settings.write_text(json.dumps({"permissions": {"deny": ["WebFetch"]}}))
        settings.chmod(0o000)
        self.addCleanup(settings.chmod, 0o644)

        refused, sessions = self.run_case("--runs", "1", "Add a feature", checkout=checkout)

        self.assertNotEqual(refused.returncode, 0)
        self.assertIn(".claude/settings.json cannot be read", refused.stderr)
        self.assertEqual(refused.stderr.count("settings.json"), 1)
        self.assertEqual(sessions, [])

    def test_refuses_an_output_directory_that_already_holds_transcripts(self):
        (self.temp / "out").mkdir()
        (self.temp / "out/run-1-turn-1.jsonl").write_text("{}")

        result, sessions = self.run_case("--runs", "1", "Add a feature")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("run-1-turn-1.jsonl", result.stderr)
        self.assertEqual(sessions, [])

    def test_refuses_counts_and_limits_out_of_range_and_accepts_a_one_day_timeout(self):
        for option, value in (("--runs", "0"), ("--max-turns", "0"), ("--timeout", "-1"), ("--timeout", "inf"),
                              ("--timeout", "86401")):
            with self.subTest(option, value=value):
                result, sessions = self.run_case(option, value, "Add a feature", out=self.temp / (option + value))

                self.assertEqual(result.returncode, 2)
                self.assertIn("argument {}:".format(option), result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(sessions, [])

        accepted, sessions = self.run_case("--runs", "1", "--timeout", "86400", "Add a feature")

        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertEqual(len(sessions), 1)

    def test_runs_started_together_into_one_output_directory_leave_one_running(self):
        scripts = [self.start("--runs", "1", "Add a feature") for _ in range(3)]
        outputs = [self.finish(script) for script in scripts]

        self.assertEqual(sorted(script.returncode != 0 for script in scripts), [False, True, True], outputs)
        for _, stderr in outputs:
            self.assertNotIn("Traceback", stderr)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_followup_turn_resumes_the_same_session_and_case_json_records_both_prompts(self):
        result, sessions = self.run_case("--runs", "1", "--then", "Yes, do it", "Is the guidance clear?")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        first, second = sessions
        self.assertNotIn("--resume", first["argv"])
        session = second["argv"][second["argv"].index("--resume") + 1]
        self.assertEqual(second["argv"][second["argv"].index("-p") + 1], "Yes, do it")
        self.assertIn("Answered 'Is the guidance clear?' in {}".format(session), result.stdout)
        self.assertIn("Answered 'Yes, do it' in {}".format(session), result.stdout)
        case = json.loads((self.temp / "out/case.json").read_text())
        self.assertEqual(case["prompts"], ["Is the guidance clear?", "Yes, do it"])
        self.assertEqual(case["runs"], 1)

    def test_run_cut_off_mid_character_by_its_timeout_keeps_its_actions_and_fails_the_command(self):
        self.addCleanup(self.kill_recorded_child)
        started = time.monotonic()
        result, _ = self.run_case("--runs", "1", "--timeout", "1", "Add a feature", mode="hang")

        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(result.returncode, 1)
        self.assertIn("timed out after 1 s", result.stdout)
        self.assertIn("Edit skills/tdd/SKILL.md (denied)", result.stdout)
        self.assertIn("Skills: tdd 1/1\n", result.stdout)
        self.assertTrue(self.recorded_child_exits())

    def test_terminating_the_script_stops_every_session(self):
        self.addCleanup(self.kill_recorded_child)
        script = self.start("--runs", "1", "Add a feature", mode="hang")
        deadline = time.monotonic() + 30
        while not self.child_record.exists() and time.monotonic() < deadline:
            time.sleep(0.05)

        script.send_signal(signal.SIGTERM)
        self.finish(script, timeout=30)

        self.assertNotEqual(script.returncode, 0)
        self.assertTrue(self.recorded_child_exits())

    def test_signed_out_session_is_reported_and_fails_the_command(self):
        result, _ = self.run_case("--runs", "1", "Add a feature", mode="signed-out")

        self.assertEqual(result.returncode, 1)
        self.assertIn("Not logged in", result.stdout)

    def test_failed_turn_ends_its_run_without_sending_the_followups(self):
        result, sessions = self.run_case("--runs", "1", "--then", "Yes, do it", "Add a feature", mode="signed-out")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(sessions), 1)

    def test_crash_without_a_result_reports_stderr_and_fails_the_command(self):
        result, _ = self.run_case("--runs", "1", "Add a feature", mode="crash")

        self.assertEqual(result.returncode, 1)
        self.assertIn("fatal: config unreadable", result.stdout)

    def test_stderr_that_is_not_utf8_keeps_the_report(self):
        result, _ = self.run_case("--runs", "1", "Add a feature", mode="garbled-stderr")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Answered 'Add a feature' in session-", result.stdout)

    def test_answer_with_a_raw_line_separator_is_reported(self):
        result, _ = self.run_case("--runs", "1", "Add a feature", mode="line-separator")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("First line", result.stdout)
        self.assertIn("second line", result.stdout)

    def test_answer_that_the_output_encoding_cannot_hold_keeps_the_report(self):
        self.env["PYTHONIOENCODING"] = "latin-1"
        result, _ = self.run_case("--runs", "1", "Add a feature", mode="dashed-answer")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, "Tests pass .+ done")
        self.assertIn("Summary of 1 runs", result.stdout)

    def test_error_result_fails_the_command_even_when_claude_exits_0(self):
        result, _ = self.run_case("--runs", "1", "Add a feature", mode="api-error")

        self.assertEqual(result.returncode, 1)
        self.assertIn("API Error: 529 overloaded", result.stdout)


if __name__ == "__main__":
    unittest.main()
