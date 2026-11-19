#!/usr/bin/env python3
"""Run a prompt in fresh headless Claude Code sessions loaded with this checkout's skills.

Each run gets its own configuration directory. It holds the CLAUDE.md and skills
that `scripts/manage.py install` would deploy from this checkout for Claude
Code, plus the apiKeyHelper, env, model, and autoMemoryEnabled settings from
your Claude Code settings. A session signed in only through /login cannot sign
in here. Create a token with `claude setup-token` and export it as
CLAUDE_CODE_OAUTH_TOKEN.

Sessions run in this checkout and may use only Read, Grep, Glob, Skill, and the
shell commands Claude Code treats as read-only. Every other tool call is denied,
so an attempt to edit, post, or merge appears in the report as a denied call
instead of happening. Sessions skip local settings
(.claude/settings.local.json), so allow rules saved there do not apply. The
script refuses to start when a committed .claude/settings.json allows any other
tool. The report lists each run's skills, actions, and answers, then how many
runs used each skill and had each tool denied. The script exits 1 when any run
times out or fails.
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "scripts"))
import manage

ALLOWED = ("Read", "Grep", "Glob", "Skill")
CONTEXT_TOOLS = set(ALLOWED) | {"ToolSearch", "TodoWrite"}
GRACE_SECONDS = 5
USER_SETTINGS = ("apiKeyHelper", "env", "model", "autoMemoryEnabled")
PARENT_SESSION = (
    "CLAUDECODE", "CLAUDE_PID", "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_CHILD_NOTES_DIR",
    "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
)


@dataclass
class Turn:
    prompt: str
    calls: list[dict] = field(default_factory=list)
    errors: dict[str, object] = field(default_factory=dict)
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


def loosening_rules():
    path = REPO / ".claude/settings.json"
    settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    rules = settings.get("permissions", {}).get("allow", [])
    return [rule for rule in rules if rule.split("(", 1)[0] not in ALLOWED]


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
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                    turn.errors[block.get("tool_use_id")] = block.get("content")
        elif event.get("type") == "result":
            turn.result = event


def run(index, prompts, config, args):
    env = {key: value for key, value in os.environ.items() if key not in PARENT_SESSION}
    env["CLAUDE_CONFIG_DIR"] = str(config)
    turns, session = [], None
    for number, prompt in enumerate(prompts, 1):
        turn = Turn(prompt)
        turns.append(turn)
        log = args.out / "run-{}-turn-{}.jsonl".format(index, number)
        with log.open("w", encoding="utf-8") as stdout:
            process = subprocess.Popen(command(prompt, args, session), cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=subprocess.PIPE, text=True)
            try:
                _, stderr = process.communicate(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                turn.problem = "timed out after {:g} s".format(args.timeout)
                try:
                    _, stderr = process.communicate(timeout=GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    process.stderr.close()
                    process.wait()
                    stderr = ""
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
    return json.dumps(data)[:160]


def actions(turns):
    for turn in turns:
        for call in turn.calls:
            if call["name"] in CONTEXT_TOOLS:
                continue
            denied = call["id"] in turn.denied
            outcome = ""
            if denied:
                outcome = " (denied)"
            elif call["id"] in turn.errors:
                outcome = " (failed: {})".format(first_line(turn.errors[call["id"]], 120))
            yield call["name"], denied, "{} {}{}".format(call["name"], call_summary(call), outcome)


def skills(turns):
    named = (call["input"].get("skill") for turn in turns for call in turn.calls
             if call["name"] == "Skill" and call["id"] not in turn.errors)
    return list(dict.fromkeys(str(name) for name in named if name))


def tally(counts, runs):
    return ", ".join("{} {}/{}".format(name, count, runs) for name, count in sorted(counts.items())) or "none"


def report(results, args):
    total, fired, refused = 0.0, Counter(), Counter()
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
        fired.update(run_skills)
        refused.update(denied)
        for number, turn in enumerate(turns, 1):
            answer = (turn.result or {}).get("result") or "(none)"
            print("  Turn {} answer:".format(number))
            print("\n".join("    " + line for line in answer.splitlines()))
    runs = len(results)
    unfinished = [str(index) for index, turns in enumerate(results, 1) if any(turn.problem for turn in turns)]
    print("Summary of {} runs".format(runs))
    print("  Skills: {}".format(tally(fired, runs)))
    print("  Denied: {}".format(tally(refused, runs)))
    print("  Unfinished runs: {}".format(", ".join(unfinished) or "none"))
    print("  Cost: ${:.2f} at API prices".format(total))
    print("  Transcripts: {}".format(args.out))
    return 1 if unfinished else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prompt", help="the first user message")
    parser.add_argument("--then", action="append", default=[], metavar="PROMPT",
                        help="a later user message in the same session; repeat for more turns")
    parser.add_argument("--runs", type=int, default=3, help="independent sessions to run in parallel (default 3)")
    parser.add_argument("--model", help="model for every session (default: your settings or account default)")
    parser.add_argument("--max-turns", type=int, default=30, help="agent turns allowed per message (default 30)")
    parser.add_argument("--timeout", type=float, default=900, help="seconds allowed per message (default 900)")
    parser.add_argument("--out", type=Path, help="directory for transcripts (default: a new temporary directory)")
    args = parser.parse_args()
    loosening = loosening_rules()
    if loosening:
        parser.error("{} allows {}, so sessions would not be read-only".format(
            REPO / ".claude/settings.json", ", ".join(loosening)))
    args.out = args.out or Path(tempfile.mkdtemp(prefix="run-case-"))
    args.out.mkdir(parents=True, exist_ok=True)
    configs = Path(tempfile.mkdtemp(prefix="run-case-config-"))
    try:
        roots = [build_config(configs / str(index)) for index in range(1, args.runs + 1)]
        with ThreadPoolExecutor(args.runs) as pool:
            results = list(pool.map(lambda index: run(index, [args.prompt, *args.then], roots[index - 1], args),
                                    range(1, args.runs + 1)))
    finally:
        shutil.rmtree(configs, ignore_errors=True)
    return report(results, args)


if __name__ == "__main__":
    sys.exit(main())
