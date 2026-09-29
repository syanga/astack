#!/usr/bin/env python3
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
PYTHON = re.compile(r'(?:/[A-Za-z0-9_./+-]*/)?python3(?:\.[0-9]+)?')
REASON = '''Run background Bash commands through the run-job helper, written literally.
For a finite job or a watcher, with a deadline well past its normal duration:
python3 {core} --timeout SECONDS -- COMMAND [ARG...]
For a long-lived process, such as a dev server:
python3 {core} --no-timeout -- COMMAND [ARG...]
Quote arguments with single quotes only. Put cd, VAR=value, pipes, redirections, and other shell syntax inside the command, as sh -c '...' or env VAR=value.
Read run-job/SKILL.md from the installed skills for exit codes and watcher rules.'''.format(core=shlex.quote(str(CORE)))


def words(command):
    for word in re.finditer(WORD, command):
        yield ''.join(segment.strip("'") if segment.startswith("'") else segment
                      for segment in re.findall(SEGMENT, word.group()))


def runs_core(command: str) -> bool:
    if not COMMAND.fullmatch(command):
        return False
    values = list(words(command))
    return (len(values) >= 2 and PYTHON.fullmatch(values[0]) is not None
            and values[1] in (str(CORE), os.path.realpath(CORE)))


def decide(event: dict) -> str | None:
    if event['tool_name'] != 'Bash' or event['tool_input'].get('run_in_background') is not True:
        return None
    command = event['tool_input']['command']
    if not isinstance(command, str):
        raise TypeError('tool_input.command is not a string')
    return None if runs_core(command) else REASON


def main():
    reason = decide(json.load(sys.stdin))
    if reason is not None:
        json.dump({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                          'permissionDecisionReason': reason}}, sys.stdout)


if __name__ == '__main__':
    main()
