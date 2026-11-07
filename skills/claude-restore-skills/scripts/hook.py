#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from typing import NamedTuple


# Claude Code saves hook context over 10,000 characters to a file and shows the
# model a 2 KB preview, so settings/claude.json runs this hook once per part.
PARTS = 8
PART_BYTES = 9000
ARGS_LIMIT = 8000
LISTED_ARGS_LIMIT = 1000
WAIT_SECONDS = 5
FRESH_SECONDS = 15
BASE = 'Base directory for this skill: '
# Claude Code marks the skills it re-attached after compaction but cut short.
CUT = 'skill content truncated for compaction'
COMMAND = re.compile(r'<command-name>/?([^<\s]+)</command-name>')
ARGS = re.compile(r'<command-args>(.*?)</command-args>', re.S)
FRONTMATTER = re.compile(r'\A---\n.*?\n---\n', re.S)


class History(NamedTuple):
    context: list[dict]
    lineage: set[str]
    oldest: int


class Invocation(NamedTuple):
    uuid: str
    position: int
    name: str
    args: str
    base: str
    text: str


def read_entries(path: str) -> list[dict]:
    with open(path, 'rb') as handle:
        if BASE.encode() not in handle.read():
            return []
        handle.seek(0)
        entries = []
        for line in handle:
            if b'"uuid"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or not isinstance(entry.get('uuid'), str):
                continue
            slim = {key: entry.get(key) for key in (
                'uuid', 'parentUuid', 'logicalParentUuid', 'type', 'subtype', 'isSidechain', 'isMeta', 'sourceToolUseID')}
            slim['message'] = entry.get('message') if entry.get('type') == 'user' else None
            attachment = entry.get('attachment')
            keep = isinstance(attachment, dict) and attachment.get('type') in ('hook_additional_context', 'invoked_skills')
            slim['attachment'] = attachment if keep else None
            entries.append(slim)
    return entries


def text_of(entry: dict) -> str:
    content = (entry.get('message') or {}).get('content')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(block.get('text', '') for block in content if isinstance(block, dict))
    return ''


def history(entries: list[dict], source: str) -> History:
    """Claude Code rebuilds context from the newest entry's parent chain, back to
    the last compaction or to a parent missing from the transcript. A compaction
    boundary links to the history before it through logicalParentUuid."""
    position = {entry['uuid']: index for index, entry in enumerate(entries)}
    boundaries = [index for index, entry in enumerate(entries) if entry['subtype'] == 'compact_boundary']
    boundary = boundaries[-1] if boundaries else (len(entries) if source == 'compact' else -1)
    index = next((index for index in range(len(entries) - 1, -1, -1) if not entries[index]['isSidechain']), None)
    context, lineage, oldest = [], set(), len(entries)
    in_context = True
    while index is not None and entries[index]['uuid'] not in lineage:
        entry = entries[index]
        lineage.add(entry['uuid'])
        oldest = index
        in_context = in_context and index > boundary
        if in_context:
            context.append(entry)
        index = position.get(entry['parentUuid'] or entry['logicalParentUuid'])
    return History(context, lineage, oldest)


def invocations(entries: list[dict]) -> list[Invocation]:
    by_id = {entry['uuid']: entry for entry in entries}
    found = []
    for position, entry in enumerate(entries):
        if entry['type'] != 'user' or not entry['isMeta'] or entry['sourceToolUseID'] or entry['isSidechain']:
            continue
        blocks = (entry['message'] or {}).get('content')
        text = next((block['text'] for block in blocks if isinstance(block, dict)
                     and isinstance(block.get('text'), str) and block['text'].startswith(BASE)),
                    None) if isinstance(blocks, list) else None
        if text is None:
            continue
        command = text_of(by_id.get(entry['parentUuid']) or {})
        base = text[len(BASE):].split('\n', 1)[0].strip()
        name = COMMAND.search(command)
        args = ARGS.search(command)
        found.append(Invocation(entry['uuid'], position, name.group(1) if name else Path(base).name,
                                args.group(1).strip() if args else '', base, text))
    return found


def current_body(invocation: Invocation) -> str | None:
    try:
        text = (Path(invocation.base) / 'SKILL.md').read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return None
    return FRONTMATTER.sub('', text, count=1).strip()


def missing(entries: list[dict], source: str) -> list[Invocation]:
    known = history(entries, source)
    ids = {entry['uuid'] for entry in known.context}
    attachments = [entry['attachment'] for entry in known.context if isinstance(entry['attachment'], dict)]
    restored = '\n'.join(str(part) for attachment in attachments if attachment.get('type') == 'hook_additional_context'
                         for part in attachment.get('content') or [])
    attached = {str(skill.get('content', '')).split('\n', 1)[0]: str(skill.get('content', ''))
                for attachment in attachments if attachment.get('type') == 'invoked_skills'
                for skill in attachment.get('skills') or [] if isinstance(skill, dict)}
    latest = {}
    for invocation in invocations(entries):
        if invocation.uuid in known.lineage or invocation.position < known.oldest:
            latest.pop(invocation.name, None)
            latest[invocation.name] = invocation
    result = []
    for invocation in latest.values():
        copy = attached.get(BASE + invocation.base)
        body = current_body(invocation)
        if invocation.uuid in ids or '<skill name="{}">'.format(invocation.name) in restored:
            continue
        if copy is not None and CUT not in copy and (body is None or body in copy):
            continue
        result.append(invocation)
    return result


def skill_text(invocation: Invocation) -> str:
    body = current_body(invocation)
    if body is None:
        return BASE + invocation.base + '\n\n' + invocation.text[len(BASE) + len(invocation.base):].strip()
    text = BASE + invocation.base + '\n\n' + body
    if not invocation.args:
        return text
    args = invocation.args[:ARGS_LIMIT]
    if len(invocation.args) > ARGS_LIMIT:
        args += '\n[Arguments cut at {} characters.]'.format(ARGS_LIMIT)
    return text + '\n\n\nARGUMENTS: ' + args


def message(restored: list[Invocation], left_out: list[Invocation]) -> str:
    named = ', '.join('`/{}`'.format(' '.join(filter(None, (item.name, ' '.join(item.args.split())[:200]))))
                      for item in left_out + restored)
    lines = ['Earlier history of this session is no longer in context. Before it dropped out, the user invoked '
             'these skills, in this order: {}. The text of each {}follows. Continue each unfinished task from '
             'where its work stands, and skip steps already done.'.format(named, 'that fit ' if left_out else '')]
    for item in left_out:
        lines.append('`/{}` did not fit. Read {} if its task is unfinished.'.format(item.name, Path(item.base) / 'SKILL.md')
                     + ('\nARGUMENTS: ' + item.args[:LISTED_ARGS_LIMIT] if item.args else ''))
    for item in restored:
        lines.append('<skill name="{}">\n{}\n</skill>'.format(item.name, skill_text(item)))
    return '\n\n'.join(lines)


def split(text: str) -> list[str]:
    """Split at line breaks, because Claude Code joins the parts with newlines."""
    lines = []
    for line in text.split('\n'):
        width = PART_BYTES // 4
        lines += [line[start:start + width] for start in range(0, len(line), width)] if len(line.encode()) > PART_BYTES else [line]
    parts, current, size = [], [], -1
    for line in lines:
        grown = size + 1 + len(line.encode())
        if current and grown > PART_BYTES:
            parts.append('\n'.join(current))
            current, grown = [], len(line.encode())
        current.append(line)
        size = grown
    if current:
        parts.append('\n'.join(current))
    return parts


def restoration(entries: list[dict], source: str) -> list[str]:
    restored, left_out = missing(entries, source), []
    while len(restored) > 1 and len(split(message(restored, left_out))) > PARTS:
        left_out.append(restored.pop(0))
    if not restored:
        return []
    parts = split(message(restored, left_out))
    if len(parts) <= PARTS:
        return parts
    notice = '[Cut for space. Read the rest in {}.]\n</skill>'.format(Path(restored[0].base) / 'SKILL.md')
    last = parts[PARTS - 1].encode()[:PART_BYTES - len(notice.encode()) - 1].decode(errors='ignore')
    return parts[:PARTS - 1] + [last.rsplit('\n', 1)[0] + '\n' + notice]


class Turn:
    """Claude Code runs the parts in parallel and joins their output in the order
    the processes exit. All parts of one run receive the same event."""

    def __init__(self, raw: bytes, part: int):
        self.folder = Path(tempfile.gettempdir()) / 'claude-restore-skills-{}'.format(os.getuid())
        self.prefix = hashlib.sha256(raw).hexdigest()[:16]
        self.part = part
        try:
            self.folder.mkdir(mode=0o700, exist_ok=True)
            status = os.lstat(self.folder)
            self.usable = stat.S_ISDIR(status.st_mode) and status.st_uid == os.getuid()
            if self.usable and part == 1:
                for stale in self.folder.iterdir():
                    try:
                        if time.time() - stale.stat().st_mtime > 60:
                            stale.unlink()
                    except OSError:
                        pass
            if self.usable:
                staged = self.marker(part).with_suffix('.tmp')
                staged.write_text('{} {}'.format(os.getpid(), os.getppid()))
                os.replace(staged, self.marker(part))
        except OSError:
            self.usable = False

    def marker(self, part: int) -> Path:
        return self.folder / '{}-{}'.format(self.prefix, part)

    def wait(self):
        if not self.usable or self.part == 1:
            return
        previous = self.marker(self.part - 1)
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline and not self.exited(previous):
            time.sleep(0.02)
        time.sleep(0.05)

    def exited(self, previous: Path) -> bool:
        try:
            if time.time() - previous.stat().st_mtime > FRESH_SECONDS:
                return False
            pid, parent = (int(value) for value in previous.read_text().split())
        except (OSError, ValueError):
            return False
        # Claude Code runs each part through its own shell and orders output by that shell's exit.
        watched = [pid] if parent == os.getppid() else [pid, parent]
        for process in watched:
            try:
                os.kill(process, 0)
                return False
            except ProcessLookupError:
                continue
            except OSError:
                return False
        return True

    def finish(self, last: bool):
        for part in ([self.part - 1] if self.part > 1 else []) + ([self.part] if last else []):
            try:
                self.marker(part).unlink()
            except OSError:
                pass


def main():
    part = int(sys.argv[1])
    raw = sys.stdin.buffer.read()
    event = json.loads(raw)
    if event.get('source') not in ('resume', 'compact'):
        return
    turn = Turn(raw, part)
    try:
        parts = restoration(read_entries(event['transcript_path']), event['source'])
    except BaseException:
        turn.finish(last=True)
        raise
    if part > len(parts):
        turn.finish(last=True)
        return
    turn.wait()
    json.dump({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': parts[part - 1]}}, sys.stdout)
    sys.stdout.flush()
    turn.finish(last=part == len(parts))


if __name__ == '__main__':
    main()
