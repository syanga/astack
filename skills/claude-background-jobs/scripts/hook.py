#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
from pathlib import Path
import re
import shlex
import sys


CORE = Path(os.path.abspath(__file__)).parents[2] / 'run-job' / 'scripts' / 'run_job.py'
SEGMENT = r"[A-Za-z0-9_./:=@%+,-]|'[^']*'"
WORD = r'(?:{})+'.format(SEGMENT)
COMMAND = re.compile(r'[ \t]*{0}(?:[ \t]+{0})*[ \t\n]*'.format(WORD))
PYTHON = re.compile(r'(?s)(?:/(?:.*/)?)?python3(?:\.[0-9]+)?')
TOOL_DEFAULT_MS = 1800000
TOOL_MAX_MS = 7200000
MARGIN_SECONDS = 30
REASON = '''Run background Bash commands through the run-job helper, in one of these forms.
For a finite job or a watcher, set SECONDS well past its normal duration. Set the Bash tool's timeout to at least (SECONDS + {margin}) * 1000.
python3 {core} --timeout SECONDS -- COMMAND [ARG...]
For a long-lived process, such as a dev server:
python3 {core} --no-timeout -- COMMAND [ARG...]
Write each argument as plain text or in single quotes. Put cd, pipes, redirections, and other shell syntax inside sh -c '...', and set variables with env VAR=value.
For exit codes and watcher rules, read run-job/SKILL.md from the installed skills.'''.format(
    core=shlex.quote(str(CORE)), margin=MARGIN_SECONDS)
TIMEOUT_TOO_SHORT = '''The Bash tool stops this background command after {timeout} ms, which is not {margin} seconds past run-job's --timeout {seconds:.15g} deadline.
Send the same command again with the Bash tool's timeout set to {needed}.'''
DEADLINE_TOO_LONG = '''The Bash tool stops a background command after at most {limit} ms, which is not {margin} seconds past run-job's --timeout {seconds:.15g} deadline.
Use --timeout {longest} or less, and set the Bash tool's timeout to (the --timeout value + {margin}) * 1000.'''


def core_arguments(command: str) -> list[str] | None:
    words = [word.replace("'", '') for word in re.findall(WORD, command)] if COMMAND.fullmatch(command) else []
    if (len(words) >= 2 and PYTHON.fullmatch(words[0]) is not None
            and words[1] in (str(CORE), os.path.realpath(CORE))):
        return words[2:]
    return None


def deadline(arguments: list[str]) -> float | None:
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(CORE.parent))
    import run_job
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    run_job.add_arguments(parser)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            return run_job.spec_from(parser.parse_args(arguments)).timeout
    except SystemExit:
        return None


def decide(event: dict) -> str | None:
    tool_input = event['tool_input']
    if event['tool_name'] != 'Bash' or tool_input.get('run_in_background') is not True:
        return None
    arguments = core_arguments(tool_input['command'])
    if arguments is None:
        return REASON
    seconds = deadline(arguments)
    if seconds is None:
        return None
    longest = TOOL_MAX_MS // 1000 - MARGIN_SECONDS
    if seconds > longest:
        return DEADLINE_TOO_LONG.format(limit=TOOL_MAX_MS, seconds=seconds, longest=longest, margin=MARGIN_SECONDS)
    needed = math.ceil((seconds + MARGIN_SECONDS) * 1000)
    timeout = tool_input.get('timeout')
    if timeout is None:
        timeout = TOOL_DEFAULT_MS
    return (TIMEOUT_TOO_SHORT.format(timeout=timeout, margin=MARGIN_SECONDS, seconds=seconds, needed=needed)
            if timeout < needed else None)


def main():
    reason = decide(json.load(sys.stdin))
    if reason is not None:
        json.dump({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                          'permissionDecisionReason': reason}}, sys.stdout)


if __name__ == '__main__':
    main()
