#!/usr/bin/env python3
from __future__ import annotations

import json
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
REASON = '''Run background Bash commands through the run-job helper, in one of these forms.
For a finite job or a watcher, set SECONDS well past its normal duration:
python3 {core} --timeout SECONDS -- COMMAND [ARG...]
For a long-lived process, such as a dev server:
python3 {core} --no-timeout -- COMMAND [ARG...]
Write each argument as plain text or in single quotes. Put cd, pipes, redirections, and other shell syntax inside sh -c '...', and set variables with env VAR=value.
For exit codes and watcher rules, read run-job/SKILL.md from the installed skills.'''.format(core=shlex.quote(str(CORE)))


def runs_core(command: str) -> bool:
    words = [word.replace("'", '') for word in re.findall(WORD, command)] if COMMAND.fullmatch(command) else []
    return (len(words) >= 2 and PYTHON.fullmatch(words[0]) is not None
            and words[1] in (str(CORE), os.path.realpath(CORE)))


def decide(event: dict) -> str | None:
    if event['tool_name'] != 'Bash' or event['tool_input'].get('run_in_background') is not True:
        return None
    return None if runs_core(event['tool_input']['command']) else REASON


def main():
    reason = decide(json.load(sys.stdin))
    if reason is not None:
        json.dump({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                          'permissionDecisionReason': reason}}, sys.stdout)


if __name__ == '__main__':
    main()
