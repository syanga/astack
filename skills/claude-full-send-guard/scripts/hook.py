#!/usr/bin/env python3
from __future__ import annotations

import json
import mmap
from pathlib import Path
import re
import sys
from typing import NamedTuple


FULL_SEND_MARKER = b'full-send</command-name>'
FULL_SEND = re.compile(r'<command-message>[^<]*</command-message>\n<command-name>/?full-send</command-name>'
                       r'(?:\n<command-args>.*</command-args>)?', re.S)
STORE = re.compile(r'`(/[^`\n]+)/assumed-decisions\.md`|(?<![\w`])(/[^\s`\'"()<>\[\]{}\\*]+)/assumed-decisions\.md')
DISPATCH = re.compile(r'^\d+\.\s+Dispatch (paused|resumed)', re.M)
OPEN_GATE = re.compile(r'^## (\S+)\n+- Status: open$', re.M)
FINISHED = ('done', 'abandoned', 'zombie-reconciled')
LISTED_UNITS = 40


class Entry(NamedTuple):
    uuid: str
    parent: str | None
    sidechain: bool
    snapshot: bool
    full_send: bool
    stores: list[str]


class Unit(NamedTuple):
    name: str
    state: str


def content_of(entry: dict):
    message = entry.get('message')
    return message.get('content') if isinstance(message, dict) else None


def texts_of(entry: dict) -> list[str]:
    content = content_of(entry)
    if not isinstance(content, list):
        return []
    return [block['text'] for block in content
            if isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str)]


def read_entries(data: bytes) -> list[Entry]:
    entries = []
    for line in data.split(b'\n'):
        if b'"uuid"' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get('uuid'), str):
            continue
        parent = entry.get('parentUuid') or entry.get('logicalParentUuid')
        content = content_of(entry)
        sidechain = bool(entry.get('isSidechain'))
        stores = [match.group(1) or match.group(2)
                  for text in (texts_of(entry) if entry.get('type') == 'assistant' else [])
                  for match in STORE.finditer(text)]
        entries.append(Entry(
            uuid=entry['uuid'], parent=parent if isinstance(parent, str) else None, sidechain=sidechain,
            snapshot=entry.get('type') == 'system' and entry.get('subtype') == 'file_snapshot',
            full_send=(entry.get('type') == 'user' and not sidechain and isinstance(content, str)
                       and FULL_SEND.fullmatch(content.strip()) is not None),
            stores=stores))
    return entries


def newest_before(entries: list[Entry], end: int, seen: set[str]) -> int | None:
    return next((index for index in range(end - 1, -1, -1) if not entries[index].sidechain
                 and not entries[index].snapshot and entries[index].uuid not in seen), None)


def active_history(entries: list[Entry]) -> list[Entry]:
    position = {entry.uuid: index for index, entry in enumerate(entries)}
    index = newest_before(entries, len(entries), set())
    walked, seen = [], set()
    while index is not None and entries[index].uuid not in seen:
        entry = entries[index]
        seen.add(entry.uuid)
        walked.append(entry)
        if entry.parent is None:
            break
        index = position.get(entry.parent)
        if index is None or entries[index].uuid in seen:
            index = newest_before(entries, position[entry.uuid], seen)
    walked.reverse()
    return walked


def program_store(history: list[Entry]) -> Path | None:
    commands = [index for index, entry in enumerate(history) if entry.full_send]
    if not commands:
        return None
    return next((Path(store) for entry in history[commands[-1] + 1:] for store in entry.stores), None)


def read_units(store: Path) -> list[Unit] | None:
    lines = (store / 'units.tsv').read_text(encoding='utf-8').splitlines()
    if not lines or lines[0].split('\t')[:3] != ['id', 'track', 'state']:
        return None
    rows = (line.split('\t') for line in lines[1:] if line.strip())
    return [Unit(cells[0], cells[2]) for cells in rows if len(cells) >= 3]


def read_or_empty(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return ''


def paused(store: Path) -> bool:
    orders = DISPATCH.findall(read_or_empty(store / 'preferences.md'))
    return bool(orders) and orders[-1] == 'paused'


def block_message(store: Path, unfinished: list[Unit], gates: list[str]) -> str:
    listed = ', '.join('{} ({})'.format(unit.name, unit.state) for unit in unfinished[:LISTED_UNITS])
    if len(unfinished) > LISTED_UNITS:
        listed += ', and {} more'.format(len(unfinished) - LISTED_UNITS)
    return (
        'This session runs a /full-send program. Its store is {store}. No unit in the store is running, and this '
        'session has no background task or wakeup. These units are unfinished: {units}. Open gates: {gates}. '
        'Before you end the turn, start each unit whose dependencies are met and that no open gate blocks. '
        'Include the reviews and merges that full-send authorizes. The top of {store}/assumed-decisions.md says '
        'whether merging is authorized. Under "no merge", merge-ready units are finished. Run '
        '`orch unit set <unit> --state running` for each unit you start. Do the same for a unit whose worker runs '
        'outside this session. Park each decision you need from the user with `orch gate park`. Set a pending unit '
        'that waits on that decision blocked. If the user asked to pause, record it with '
        '`orch standing add "Dispatch paused: <reason>"`. If a PR watch will wake you, end the turn.'
    ).format(store=store, units=listed, gates=', '.join(gates) or 'none')


def mentions_full_send(path: Path) -> bool:
    with open(path, 'rb') as handle:
        try:
            with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
                return data.find(FULL_SEND_MARKER) >= 0
        except ValueError:
            return False


def block_reason(event: dict) -> str | None:
    if event.get('stop_hook_active') or event.get('background_tasks') or event.get('session_crons'):
        return None
    try:
        path = Path(event['transcript_path'])
        data = path.read_bytes() if mentions_full_send(path) else None
    except (KeyError, TypeError, OSError):
        return None
    store = program_store(active_history(read_entries(data))) if data is not None else None
    if store is None:
        return None
    try:
        units = read_units(store)
    except (OSError, UnicodeDecodeError):
        return None
    if not units or any(unit.state == 'running' for unit in units) or paused(store):
        return None
    gates = OPEN_GATE.findall(read_or_empty(store / 'gates.md'))
    finished = FINISHED + (('blocked',) if gates else ())
    unfinished = [unit for unit in units if unit.state not in finished]
    return block_message(store, unfinished, gates) if unfinished else None


def main():
    text = block_reason(json.load(sys.stdin))
    if text is not None:
        json.dump({'decision': 'block', 'reason': text}, sys.stdout)


if __name__ == '__main__':
    main()
