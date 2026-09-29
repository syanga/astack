#!/usr/bin/env python3
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import sys


CORE = Path(os.path.abspath(__file__)).parents[2] / 'run-job' / 'scripts' / 'run_job.py'
OPERATORS = ';&|()<>\n'
NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
ASSIGNMENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*=')
PYTHON = re.compile(r'(/.*/)?python3(\.[0-9]+)?')
REASON = '''Run background Bash commands through the run-job helper.
For a finite job or a watcher, set a deadline well past its normal duration:
python3 {core} --timeout SECONDS -- COMMAND [ARG...]
For a long-lived process, such as a dev server:
python3 {core} --no-timeout -- COMMAND [ARG...]
Put pipes, redirections, and other shell syntax inside sh -c '...'.
Read run-job/SKILL.md from the installed skills for exit codes and watcher rules.'''.format(core=shlex.quote(str(CORE)))


class Denied(Exception):
    pass


@dataclass(frozen=True)
class Word:
    raw: str
    parts: tuple[tuple[str, str], ...]


def expansion(command, index, parts):
    rest = command[index + 1:]
    if rest.startswith(('(', "'")):
        raise Denied
    if rest.startswith('{'):
        close = command.find('}', index + 2)
        body = command[index + 2:close]
        if close < 0 or re.search(r'[{(\'"\\`]', body):
            raise Denied
        parts.append(('var', body))
        return close + 1
    name = NAME.match(rest)
    parts.append(('var', name.group() if name else ''))
    return index + 1 + (len(name.group()) if name else 0)


def double_quoted(command, index, parts):
    while index < len(command):
        char = command[index]
        if char == '"':
            return index + 1
        if char == '`':
            raise Denied
        if char == '$':
            index = expansion(command, index, parts)
        elif command.startswith('\\\n', index):
            index += 2
        elif char == '\\' and command[index + 1:index + 2] in ('$', '`', '"', '\\'):
            parts.append(('text', command[index + 1]))
            index += 2
        else:
            parts.append(('text', char))
            index += 1
    raise Denied


def lex(command: str) -> list[Word | str]:
    tokens, parts, start, index = [], None, 0, 0
    while index < len(command):
        char = command[index]
        if command.startswith('\\\n', index):
            index += 2
            continue
        if char in ' \t' or char in OPERATORS:
            if parts is not None:
                tokens.append(Word(command[start:index], tuple(parts)))
                parts = None
            if char in OPERATORS:
                operator = '&&' if command.startswith('&&', index) else char
                tokens.append(operator)
                index += len(operator)
            else:
                index += 1
            continue
        if parts is None:
            if char == '#':
                newline = command.find('\n', index)
                index = len(command) if newline < 0 else newline
                continue
            parts, start = [], index
        if char == '\\':
            parts.append(('text', command[index + 1:index + 2]))
            index += 2
        elif char == "'":
            close = command.find("'", index + 1)
            if close < 0:
                raise Denied
            parts.append(('text', command[index + 1:close]))
            index = close + 1
        elif char == '"':
            index = double_quoted(command, index + 1, parts)
        elif char == '`':
            raise Denied
        elif char == '$':
            index = expansion(command, index, parts)
        elif char == '~' and index == start and command.startswith('~/', index):
            parts.append(('var', 'HOME'))
            index += 1
        else:
            parts.append(('text', char))
            index += 1
    if parts is not None:
        tokens.append(Word(command[start:], tuple(parts)))
    return tokens


def literal(token):
    if isinstance(token, Word) and all(kind == 'text' for kind, _ in token.parts):
        return ''.join(value for _, value in token.parts)
    return None


def expanded_path(token):
    home = os.environ.get('HOME', '')
    values = {'HOME': home, 'CLAUDE_CONFIG_DIR:-$HOME/.claude': os.environ.get('CLAUDE_CONFIG_DIR') or home + '/.claude'}
    if not isinstance(token, Word) or any(kind == 'var' and value not in values for kind, value in token.parts):
        return None
    path = ''.join(values[value] if kind == 'var' else value for kind, value in token.parts)
    return os.path.realpath(path) if os.path.isabs(path) else None


def goes_through_core(tokens):
    index = 0
    if literal(tokens[0] if tokens else None) == 'cd' and len(tokens) > 2 and isinstance(tokens[1], Word) \
            and tokens[2] == '&&':
        index = 3
    while index < len(tokens) and isinstance(tokens[index], Word) and ASSIGNMENT.match(tokens[index].raw):
        index += 1
    if index < len(tokens) and literal(tokens[index]) == 'exec':
        index += 1
    rest = tokens[index:]
    return (len(rest) >= 2 and PYTHON.fullmatch(literal(rest[0]) or '') is not None
            and expanded_path(rest[1]) == os.path.realpath(CORE)
            and all(isinstance(token, Word) for token in rest))


def decide(event: dict) -> str | None:
    if event['tool_name'] != 'Bash' or event['tool_input'].get('run_in_background') is not True:
        return None
    command = event['tool_input']['command']
    if not isinstance(command, str):
        raise TypeError('tool_input.command is not a string')
    try:
        return None if goes_through_core(lex(command.rstrip(' \t\n'))) else REASON
    except Denied:
        return REASON


def main():
    reason = decide(json.load(sys.stdin))
    if reason is not None:
        json.dump({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                          'permissionDecisionReason': reason}}, sys.stdout)


if __name__ == '__main__':
    main()
