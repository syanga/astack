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
# model a 2 KB preview, and agents do not reliably read the file. So
# settings/claude.json registers this hook once per part, and each part fits in
# PART_BYTES.
PARTS = 8
PART_BYTES = 9000
ARGS_LIMIT = 8000
LISTED_ARGS_LIMIT = 1000
WAIT_SECONDS = 8
FRESH_SECONDS = 15
STALE_SECONDS = 60
HEADER_ARGS_LIMIT = 200
BASE = 'Base directory for this skill: '
# Claude Code marks the skills it re-attached after compaction but cut short.
CUT = 'skill content truncated for compaction'
COMMAND = re.compile(r'<command-name>/?([^<\s]+)</command-name>')
ARGS = re.compile(r'<command-args>(.*?)</command-args>', re.S)
PLACEHOLDER = re.compile(r'\$ARGUMENTS(?!\[\d+\])')
ARGUMENTS_LINE = '\n\n\nARGUMENTS: '
SKILL_OPEN = '<skill name="{}">'
FRONTMATTER = re.compile(r'\A---\n.*?\n---\n', re.S)


class History(NamedTuple):
    context: list[dict]
    lineage: set[str]


class Invocation(NamedTuple):
    uuid: str
    name: str
    args: str
    base: str
    text: str

    @property
    def skill_file(self) -> Path:
        return Path(self.base) / 'SKILL.md'

    @property
    def recorded(self) -> str:
        return self.text[len(BASE) + len(self.base):].strip()


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
    boundary links to the history before it through logicalParentUuid. Compact
    hooks run before Claude Code writes the new boundary, so on compact nothing
    in the transcript is still in context."""
    position = {entry['uuid']: index for index, entry in enumerate(entries)}
    boundaries = [index for index, entry in enumerate(entries) if entry['subtype'] == 'compact_boundary']
    boundary = boundaries[-1] if boundaries else -1
    index = newest_before(entries, len(entries), set())
    context, lineage = [], set()
    in_context = source != 'compact'
    while index is not None and entries[index]['uuid'] not in lineage:
        entry = entries[index]
        lineage.add(entry['uuid'])
        in_context = in_context and index > boundary
        if in_context:
            context.append(entry)
        parent = entry['parentUuid'] or entry['logicalParentUuid']
        lost = parent is not None and parent not in position
        if lost:
            in_context = False
            index = newest_before(entries, index, lineage)
        else:
            index = position.get(parent)
    return History(context, lineage)


def newest_before(entries: list[dict], end: int, visited: set[str]) -> int | None:
    return next((index for index in range(end - 1, -1, -1)
                 if not entries[index]['isSidechain'] and entries[index]['uuid'] not in visited), None)


def invocations(entries: list[dict]) -> list[Invocation]:
    by_id = {entry['uuid']: entry for entry in entries}
    found = []
    for entry in entries:
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
        found.append(Invocation(entry['uuid'], name.group(1) if name else Path(base).name,
                                args.group(1).strip() if args else '', base, text))
    return found


def limited_args(invocation: Invocation) -> str:
    if len(invocation.args) <= ARGS_LIMIT:
        return invocation.args
    return invocation.args[:ARGS_LIMIT] + '\n[Arguments cut at {} characters.]'.format(ARGS_LIMIT)


def skill_source(invocation: Invocation) -> str | None:
    try:
        text = invocation.skill_file.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return None
    return FRONTMATTER.sub('', text, count=1).strip()


def expand(source: str, invocation: Invocation) -> str:
    """Fill $ARGUMENTS as Claude Code does, or append the arguments when it is absent. Unlike
    Claude Code, leave $ARGUMENTS[N], $N, and named placeholders as written, append the
    arguments when only those appear, and fill a backslash-escaped $ARGUMENTS."""
    if PLACEHOLDER.search(source):
        return PLACEHOLDER.sub(lambda _: limited_args(invocation), source)
    return source + ARGUMENTS_LINE + limited_args(invocation) if invocation.args else source


def recorded_body(invocation: Invocation) -> str:
    return invocation.recorded.rsplit(ARGUMENTS_LINE, 1)[0]


def missing(entries: list[dict], source: str) -> list[Invocation]:
    known = history(entries, source)
    ids = {entry['uuid'] for entry in known.context}
    attachments = [entry['attachment'] for entry in known.context if isinstance(entry['attachment'], dict)]
    earlier_output = '\n'.join(str(part) for attachment in attachments
                               if attachment.get('type') == 'hook_additional_context'
                               for part in attachment.get('content') or [])
    attached = {str(skill.get('content', '')).split('\n', 1)[0]: str(skill.get('content', ''))
                for attachment in attachments if attachment.get('type') == 'invoked_skills'
                for skill in attachment.get('skills') or [] if isinstance(skill, dict)}
    latest = {}
    for invocation in invocations(entries):
        if invocation.uuid in known.lineage:
            latest.pop(invocation.name, None)
            latest[invocation.name] = invocation
    result = []
    for invocation in latest.values():
        copy = attached.get(BASE + invocation.base)
        if invocation.uuid in ids or SKILL_OPEN.format(invocation.name) in earlier_output:
            continue
        source = skill_source(invocation)
        current = expand(source, invocation).rsplit(ARGUMENTS_LINE, 1)[0] if source is not None else None
        if copy is not None and CUT not in copy and any(
                body in copy for body in (current, recorded_body(invocation)) if body):
            continue
        result.append(invocation)
    return result


def skill_text(invocation: Invocation) -> str:
    source = skill_source(invocation)
    if source is None:
        return BASE + invocation.base + '\n\n' + invocation.recorded
    return BASE + invocation.base + '\n\n' + expand(source, invocation)


def message(restored: list[Invocation], left_out: list[Invocation]) -> str:
    named = ', '.join('`/{}`'.format(' '.join(filter(None, (item.name, ' '.join(item.args.split())[:HEADER_ARGS_LIMIT]))))
                      for item in left_out + restored)
    lines = ['Earlier history of this session is no longer in context. Before it dropped out, the user invoked '
             'these skills, in this order: {}. The text of each {}follows. Continue each unfinished task from '
             'where its work stands, and skip steps already done.'.format(named, 'that fit ' if left_out else '')]
    for item in left_out:
        lines.append('`/{}` did not fit. Read {} if its task is unfinished.'.format(item.name, item.skill_file)
                     + ('\nARGUMENTS: ' + item.args[:LISTED_ARGS_LIMIT] if item.args else ''))
    for item in restored:
        lines.append(SKILL_OPEN.format(item.name) + '\n{}\n</skill>'.format(skill_text(item)))
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
    notice = '[Cut for space. Read the rest in {}.]\n</skill>'.format(restored[0].skill_file)
    last = parts[PARTS - 1].encode()[:PART_BYTES - len(notice.encode()) - 1].decode(errors='ignore')
    return parts[:PARTS - 1] + [last.rsplit('\n', 1)[0] + '\n' + notice]


class PartOrder:
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
                        if time.time() - stale.stat().st_mtime > STALE_SECONDS:
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
    order = PartOrder(raw, part)
    try:
        parts = restoration(read_entries(event['transcript_path']), event['source'])
    except BaseException:
        order.finish(last=True)
        raise
    if part > len(parts):
        order.finish(last=True)
        return
    order.wait()
    json.dump({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': parts[part - 1]}}, sys.stdout)
    sys.stdout.flush()
    order.finish(last=part == len(parts))


if __name__ == '__main__':
    main()
