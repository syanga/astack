#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Literal, NamedTuple


HARDEN_PR = Path(os.path.abspath(__file__)).parents[2] / 'harden-pr'
ATTRIBUTION_HEADER = re.compile(
    r"\[[^\r\n]+\] (?:on behalf of \S+|RESPONDING ON BEHALF OF \S[^\r\n]*)"
)
MENTION = re.compile(r'(?<![\w.-])pr\b(?:\s+-\S+(?:\s+[^\s-]\S*)?)*\s+merge\b|pulls/[^\s/]*/merge\b|'
                     r'mergePullRequest|enablePullRequestAutoMerge|enqueuePullRequest')
SHELL_INERT = re.compile(r'[A-Za-z0-9 \t._:/=@+,%-]+')
BOOLEAN_FLAGS = {'--squash', '-s', '--merge', '-m', '--rebase', '-r', '--auto', '--admin',
                 '--delete-branch', '-d'}
VALUE_FLAGS = {'--repo', '-R', '--match-head-commit', '--body', '-b', '--body-file', '-F',
               '--subject', '-t', '--author-email', '-A'}
SKIP_FLAGS = {'--disable-auto', '--help', '-h'}
TRUSTED = {'OWNER', 'MEMBER', 'COLLABORATOR'}
GH_TIMEOUT_SECONDS = 25
NOT_ALONE = ('This command mentions a PR merge in a form the merge gate cannot check. Run the merge alone on one '
             'line, with literal values, separate flags, and no quotes or shell syntax: gh pr merge <number> '
             '[--repo <owner>/<repo>] --match-head-commit <sha> [flags]. To write prose that mentions a PR merge, '
             'such as a commit message or PR body, put it in a file with the Write tool and pass the file with -F '
             'or --body-file. To search for it, use a pattern that breaks it up, such as pr.merge.')
UNSIGNED = ('No signed review exists on {url} at head {head}. Review this head, then post a signed review with '
            'python3 {script} review, following {posting}. '
            'If the user wants this merge without a review, ask them to merge it themselves.')
UNPINNED = 'Pass --match-head-commit {head} so the merge lands the reviewed head of {url}.'


def mentions_merge(command: str) -> bool:
    return MENTION.search(re.sub(r'[\'"\\]', '', command.replace('\\\n', ' '))) is not None


class MergeRequest(NamedTuple):
    selector: str | None
    repo: str | None
    head: str | None


def lone_merge(command: str) -> MergeRequest | Literal['skip'] | None:
    text = command.strip()
    words = text.split()
    if (not SHELL_INERT.fullmatch(text) or len(words) < 3 or os.path.basename(words[0]) != 'gh'
            or words[1:3] != ['pr', 'merge'] or any(word.startswith('=') for word in words)):
        return None
    selector, values, index, skip = None, {}, 3, False
    while index < len(words):
        word = words[index]
        name, equals, value = word.partition('=')
        if equals and name not in VALUE_FLAGS:
            return None
        if word in SKIP_FLAGS:
            skip, index = True, index + 1
            continue
        if name in VALUE_FLAGS and name.startswith('--') and equals:
            index += 1
        elif word in VALUE_FLAGS and index + 1 < len(words):
            value, index = words[index + 1], index + 2
        elif word in BOOLEAN_FLAGS:
            index += 1
            continue
        elif not word.startswith('-') and selector is None:
            selector, index = word, index + 1
            continue
        else:
            return None
        values['--repo' if name == '-R' else name] = value
    return 'skip' if skip else MergeRequest(selector, values.get('--repo'), values.get('--match-head-commit'))


def signed(review: dict) -> bool:
    first = (review.get('body') or '').split('\n', 1)[0].strip()
    return (ATTRIBUTION_HEADER.fullmatch(first) is not None and review.get('state') != 'PENDING'
            and review.get('authorAssociation') in TRUSTED)


def refusal(request: MergeRequest, cwd: str) -> str | None:
    label = request.selector or 'the current branch'
    command = ['gh', 'pr', 'view', *([request.selector] if request.selector else []),
               *(['--repo', request.repo] if request.repo else []),
               '--json', 'url,headRefOid,reviews']
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or 'gh exited with code {}'.format(result.returncode))
        pr = json.loads(result.stdout)
        head = pr['headRefOid']
        if not isinstance(head, str) or not head:
            raise ValueError('gh returned no head commit')
        covered = any(signed(review) and (review.get('commit') or {}).get('oid') == head
                      for review in pr.get('reviews') or [])
    except Exception as error:
        return 'Could not check the reviews on PR {}: {}'.format(label, error)
    url = pr.get('url') or label
    if not covered:
        return UNSIGNED.format(url=url, head=head, script=HARDEN_PR / 'scripts' / 'pr.py',
                               posting=HARDEN_PR / 'posting.md')
    return None if request.head == head else UNPINNED.format(head=head, url=url)


def decide(event: dict) -> str | None:
    if event['tool_name'] != 'Bash':
        return None
    command = event['tool_input'].get('command') or ''
    if not mentions_merge(command):
        return None
    request = lone_merge(command)
    if request is None:
        return NOT_ALONE
    if request == 'skip':
        return None
    return refusal(request, event.get('cwd') or os.getcwd())


def main():
    reason = decide(json.load(sys.stdin))
    if reason is not None:
        json.dump({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                          'permissionDecisionReason': reason}}, sys.stdout)


if __name__ == '__main__':
    main()
