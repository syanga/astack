#!/usr/bin/env python3
"""Run a prompt in fresh headless Claude Code sessions loaded with this checkout's skills.

Each run gets its own configuration directory. It holds the CLAUDE.md and skills
that `scripts/manage.py install` would deploy from this checkout for Claude
Code. The directory's settings.json copies apiKeyHelper, env, model, and
autoMemoryEnabled from your Claude Code settings. A session signed in only
through /login cannot sign in here. Create a token with `claude setup-token`
and export it as CLAUDE_CODE_OAUTH_TOKEN.

Sessions run in this checkout and may use only Read, Grep, Glob, Skill, and the
shell commands Claude Code treats as read-only. Every other tool call is denied,
so an attempt to edit, post, or merge appears in the report as a denied call
instead of happening. Sessions skip local settings
(.claude/settings.local.json). The script refuses to start when anything else
in the checkout could approve a call:

- a file or folder in the checkout, outside .git/ folders, that the script
  cannot read and so cannot check, including one under a symlinked folder
- a symlinked .claude/ folder anywhere in the checkout
- a symlink directly in a .claude/ folder, or an entry there other than
  settings.json, settings.local.json, or worktrees/
- a .claude/settings.json with anything but deny and ask rules
- a skill whose SKILL.md does not open with frontmatter between two lines of
  exactly ---, or whose frontmatter has a line break other than LF or CRLF
- a skill frontmatter key other than name, description,
  disable-model-invocation, and argument-hint, such as allowed-tools

A broken symlink, one that loops or whose target is missing, does not count as
a file the script cannot read.

These checks catch a checkout you trust that loosens sessions by accident. They
do not stop a checkout written to get past them, which could as easily edit
this script.

The report lists each run's skills, actions, and answers, then how many runs
used each skill and had each tool denied. The script exits 1 when any run times
out or fails. It stops every session when it is interrupted or terminated.
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import errno
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "scripts"))
import manage

ALLOWED = ("Read", "Grep", "Glob", "Skill")
CONTEXT_TOOLS = set(ALLOWED) | {"ToolSearch", "TodoWrite"}
DRAIN_SECONDS = 5
ACCEPTED_ENTRIES = {"settings.json", "settings.local.json", "worktrees"}
NARROWING_RULES = {"deny", "ask"}
FRONTMATTER_KEYS = {"name", "description", "disable-model-invocation", "argument-hint"}
OTHER_LINE_BREAKS = re.compile("[\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029]")
BROKEN_LINK_ERRORS = {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}
USER_SETTINGS = ("apiKeyHelper", "env", "model", "autoMemoryEnabled")
PARENT_SESSION_VARIABLES = (
    "CLAUDECODE", "CLAUDE_PID", "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_CHILD_NOTES_DIR",
    "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
)


live_sessions: set[subprocess.Popen] = set()
sessions_lock = threading.Lock()
stopping = threading.Event()


@dataclass
class Turn:
    prompt: str
    calls: list[dict] = field(default_factory=list)
    errors: dict[str, object] = field(default_factory=dict)
    succeeded: set[str] = field(default_factory=set)
    denied: set[str] = field(default_factory=set)
    result: dict | None = None
    problem: str | None = None


def build_config(root):
    harness = json.loads((REPO / "harnesses.json").read_text(encoding="utf-8"))["claude"]
    for name, (data, mode) in manage.desired_files(harness, root, False, manage.skill_files()).items():
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)
    source = manage.destination(harness["settings"], Path.home(), True)
    settings = json.loads(source.read_text(encoding="utf-8")) if source.exists() else {}
    copied = {key: settings[key] for key in USER_SETTINGS if key in settings}
    destination = manage.destination(harness["settings"], root, False)
    destination.write_text(json.dumps(copied), encoding="utf-8")
    return destination.parent


def unreadable_problem(path):
    return "{} cannot be read".format(path)


def settings_problems(path):
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return [unreadable_problem(path)]
    except ValueError:
        settings = None
    if not isinstance(settings, dict) or not isinstance(settings.get("permissions", {}), dict):
        return ["{} is not a JSON object of deny and ask rules".format(path)]
    keys = ["permissions." + key for key in settings.get("permissions", {}) if key not in NARROWING_RULES]
    keys += [key for key in settings if key != "permissions"]
    return ["{} sets {}".format(path, ", ".join(sorted(keys)))] if keys else []


def claude_folder_problems(folder):
    if folder.is_symlink():
        return ["{} is a symlink".format(folder)]
    problems = []
    for path in sorted(folder.iterdir()):
        if path.is_symlink():
            problems.append("{} is a symlink".format(path))
        elif path.name not in ACCEPTED_ENTRIES:
            problems.append("{} exists".format(path))
        elif path.name == "settings.json":
            problems += settings_problems(path)
    return problems


def exists_but_unreadable(path, mode):
    if os.access(path, mode):
        return False
    try:
        os.stat(path)
    except OSError as error:
        return error.errno not in BROKEN_LINK_ERRORS
    return True


def checkout_problems():
    problems = []
    info = REPO.stat()
    seen = {(info.st_dev, info.st_ino)}
    for folder, children, files in os.walk(REPO, followlinks=True,
                                          onerror=lambda error: problems.append(unreadable_problem(error.filename))):
        here = Path(folder)
        problems += [unreadable_problem(here / name) for name in files if exists_but_unreadable(here / name, os.R_OK)]
        unseen = []
        for child in sorted(child for child in children if child != ".git"):
            path = here / child
            if exists_but_unreadable(path, os.R_OK | os.X_OK):
                problems.append(unreadable_problem(path))
                continue
            if child.lower() == ".claude":
                problems += claude_folder_problems(path)
            info = path.stat()
            if (info.st_dev, info.st_ino) not in seen:
                seen.add((info.st_dev, info.st_ino))
                unseen.append(child)
        children[:] = unseen
    return sorted(set(problems))


def frontmatter_problem(text):
    lines = text.replace("\r\n", "\n").split("\n")
    if lines[0] != "---" or "---" not in lines[1:]:
        return "has no frontmatter between two lines of exactly ---"
    block = lines[1:lines.index("---", 1)]
    if any(OTHER_LINE_BREAKS.search(line) for line in block):
        return "has a line break other than LF or CRLF in its frontmatter"
    keys = [line.split(":", 1)[0].strip() for line in block
            if line and not line.startswith((" ", "\t", "#", "- ")) and line != "-"]
    unknown = [key for key in keys if key not in FRONTMATTER_KEYS]
    return "declares {}".format(", ".join(unknown)) if unknown else None


def read_only_problems():
    problems = checkout_problems()
    if problems:
        return problems
    for relative, data, _ in manage.skill_files():
        if len(relative.parts) == 2 and relative.name == "SKILL.md":
            problem = frontmatter_problem(data.decode("utf-8"))
            if problem:
                problems.append("skills/{} {}".format(relative, problem))
    return problems


def stop(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def stop_all(signum, frame):
    stopping.set()
    with sessions_lock:
        for process in live_sessions:
            stop(process)
    raise SystemExit(128 + signum)


def command(prompt, args, session):
    result = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
              "--permission-mode", "dontAsk", "--setting-sources", "user,project",
              "--allowedTools", ",".join(ALLOWED),
              "--max-turns", str(args.max_turns)]
    if args.model:
        result += ["--model", args.model]
    if session:
        result += ["--resume", session]
    return result


def read_events(text, turn):
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if event.get("type") == "system" and event.get("subtype") == "permission_denied":
            turn.denied.add(event.get("tool_use_id"))
        elif event.get("type") == "assistant" and isinstance(content, list):
            turn.calls += [block for block in content if block.get("type") == "tool_use"]
        elif event.get("type") == "user" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                if block.get("is_error"):
                    turn.errors[block.get("tool_use_id")] = block.get("content")
                else:
                    turn.succeeded.add(block.get("tool_use_id"))
        elif event.get("type") == "result":
            turn.result = event


def run(index, prompts, config, args):
    env = {key: value for key, value in os.environ.items() if key not in PARENT_SESSION_VARIABLES}
    env["CLAUDE_CONFIG_DIR"] = str(config)
    turns, session = [], None
    for number, prompt in enumerate(prompts, 1):
        turn = Turn(prompt)
        turns.append(turn)
        log = args.out / "run-{}-turn-{}.jsonl".format(index, number)
        with log.open("x", encoding="utf-8") as stdout, sessions_lock:
            if stopping.is_set():
                turn.problem = "stopped"
                break
            process = subprocess.Popen(command(prompt, args, session), cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=subprocess.PIPE, text=True, start_new_session=True)
            live_sessions.add(process)
        try:
            _, stderr = process.communicate(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            stop(process)
            turn.problem = "timed out after {:g} s".format(args.timeout)
            try:
                _, stderr = process.communicate(timeout=DRAIN_SECONDS)
            except subprocess.TimeoutExpired:
                process.stderr.close()
                process.wait()
                stderr = ""
        finally:
            with sessions_lock:
                live_sessions.discard(process)
        read_events(log.read_text(encoding="utf-8"), turn)
        result = turn.result or {}
        if turn.problem is None and process.returncode != 0:
            turn.problem = "exit {}: {}".format(process.returncode, result.get("result") or stderr.strip()[-300:])
        elif turn.problem is None and (result.get("is_error") or result.get("subtype") != "success"):
            errors = "; ".join(str(error) for error in result.get("errors") or [])
            turn.problem = result.get("result") or errors or result.get("subtype") or "no result"
        if turn.problem:
            break
        session = result.get("session_id")
    return turns


def first_line(text, limit):
    return (str(text).splitlines() or [""])[0][:limit]


def call_summary(call):
    data = call.get("input") or {}
    if "command" in data:
        return first_line(data["command"], 160)
    if "file_path" in data:
        path = Path(data["file_path"])
        return str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)
    if "description" in data:
        return str(data["description"])
    if "skill" in data:
        return str(data["skill"])
    return json.dumps(data)[:160]


def actions(turns):
    for turn in turns:
        for call in turn.calls:
            denied = call["id"] in turn.denied
            if call["name"] in CONTEXT_TOOLS and not denied and call["id"] not in turn.errors:
                continue
            outcome = ""
            if denied:
                outcome = " (denied)"
            elif call["id"] in turn.errors:
                outcome = " (failed: {})".format(first_line(turn.errors[call["id"]], 120))
            yield call["name"], denied, "{} {}{}".format(call["name"], call_summary(call), outcome)


def skills(turns):
    named = (call["input"].get("skill") for turn in turns for call in turn.calls
             if call["name"] == "Skill" and call["id"] in turn.succeeded)
    return list(dict.fromkeys(str(name) for name in named if name))


def tally(counts, runs):
    return ", ".join("{} {}/{}".format(name, count, runs) for name, count in sorted(counts.items())) or "none"


def report(results, out):
    total, skill_runs, denied_runs = 0.0, Counter(), Counter()
    for index, turns in enumerate(results, 1):
        cost = sum((turn.result or {}).get("total_cost_usd") or 0 for turn in turns)
        total += cost
        problem = next((turn.problem for turn in turns if turn.problem), None)
        print("Run {} of {}: {}, ${:.2f}".format(index, len(results), problem or "finished", cost))
        run_skills = skills(turns)
        print("  Skills: {}".format(", ".join(run_skills) or "none"))
        denied = set()
        listed = list(actions(turns))
        print("  Actions:" + ("" if listed else " none"))
        for name, was_denied, line in listed:
            if was_denied:
                denied.add(name)
            print("    " + line)
        skill_runs.update(run_skills)
        denied_runs.update(denied)
        for number, turn in enumerate(turns, 1):
            answer = (turn.result or {}).get("result") or "(none)"
            print("  Turn {} answer:".format(number))
            print("\n".join("    " + line for line in answer.splitlines()))
    runs = len(results)
    unfinished = [str(index) for index, turns in enumerate(results, 1) if any(turn.problem for turn in turns)]
    print("Summary of {} runs".format(runs))
    print("  Skills: {}".format(tally(skill_runs, runs)))
    print("  Denied: {}".format(tally(denied_runs, runs)))
    print("  Unfinished runs: {}".format(", ".join(unfinished) or "none"))
    print("  Cost: ${:.2f} at API prices".format(total))
    print("  Transcripts: {}".format(out))
    return 1 if unfinished else 0


def claim(out, case):
    try:
        out.mkdir(parents=True, exist_ok=True)
        held = sorted(path.name for path in out.iterdir())
        if held:
            return "{} already holds {}".format(out, held[0])
        with (out / "case.json").open("x", encoding="utf-8") as record:
            json.dump(case, record, indent=2)
    except FileExistsError as error:
        return "{} already exists".format(error.filename)
    except OSError as error:
        return "cannot write to {}: {}".format(out, error.strerror)
    return None


def positive(kind):
    def parse(text):
        value = kind(text)
        if not 0 < value < math.inf:
            raise argparse.ArgumentTypeError("must be greater than 0 and finite")
        return value
    parse.__name__ = kind.__name__
    return parse


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prompt", help="the first user message")
    parser.add_argument("--then", action="append", default=[], metavar="PROMPT",
                        help="a later user message in the same session. Repeat for more turns.")
    parser.add_argument("--runs", type=positive(int), default=3,
                        help="independent sessions to run in parallel (default 3)")
    parser.add_argument("--model", help="model for every session (default: your settings or account default)")
    parser.add_argument("--max-turns", type=positive(int), default=30,
                        help="agent turns allowed per message (default 30)")
    parser.add_argument("--timeout", type=positive(float), default=900,
                        help="seconds allowed per message (default 900)")
    parser.add_argument("--out", type=Path, help="a new or empty directory to hold the transcripts and case.json, "
                        "which records the prompts and options (default: a new temporary directory)")
    args = parser.parse_args()
    try:
        problems = read_only_problems()
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if problems:
        parser.error("sessions would not be read-only: " + "; ".join(problems))
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, stop_all)
    args.out = args.out or Path(tempfile.mkdtemp(prefix="run-case-"))
    prompts = [args.prompt, *args.then]
    problem = claim(args.out, {"prompts": prompts, "runs": args.runs, "model": args.model,
                               "max_turns": args.max_turns, "timeout": args.timeout})
    if problem:
        parser.error(problem)
    configs = Path(tempfile.mkdtemp(prefix="run-case-config-"))
    try:
        roots = [build_config(configs / str(index)) for index in range(1, args.runs + 1)]
        with ThreadPoolExecutor(args.runs) as pool:
            results = list(pool.map(lambda index: run(index, prompts, roots[index - 1], args),
                                    range(1, args.runs + 1)))
    finally:
        shutil.rmtree(configs, ignore_errors=True)
    return report(results, args.out)


if __name__ == "__main__":
    sys.exit(main())
