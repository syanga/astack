import copy
from dataclasses import dataclass
import json
import math
from pathlib import Path
import stat

import astack_files as files
from _vendor.tomllib import loads as toml_loads
from _vendor.tomllib._parser import parse_key, parse_value, skip_chars

ENTRIES = "$entries"


class SettingsRefused(ValueError):
    """A refusal whose message names only a settings path, so it is safe to show."""


@dataclass
class SettingsChange:
    target: str
    path: Path
    conflicts: list[str]
    _original_hash: str | None
    _original: bytes
    _mode: int
    _rendered: str | None
    _record: dict | None
    _changed_keys: list[str]

    def check_unchanged(self, stage):
        if files.current_hash(self.path) != self._original_hash:
            raise ValueError("Settings changed during {}; retry: {}".format(stage, self.path))

    def apply(self, state, state_path, backup_root, dry_run=False):
        """Apply this change and save ownership; restore its payload if saving fails.

        A crash between the two writes leaves the applied change unowned.
        """
        self.check_unchanged("installation")
        if self.conflicts:
            backup = backup_root / str(self.path).lstrip("/")
            print("BACKUP {} -> {}".format(self.path, backup))
            if not dry_run and self._original_hash is not None:
                files.atomic_write(backup, self._original, self._mode)
        action = "REMOVE" if self._rendered is None else "MERGE" if self._rendered.encode() != self._original else "KEEP"
        print("{} SETTINGS {}{}".format(
            action, self.path, " [" + ", ".join(self._changed_keys) + "]" if self._changed_keys else ""))
        if dry_run:
            return
        old_settings = state.get("settings", {})
        new_settings = {name: dict(records) for name, records in old_settings.items()}
        records = new_settings.setdefault(self.target, {})
        if self._record is None:
            records.pop(str(self.path), None)
        else:
            records[str(self.path)] = self._record
        if not records:
            new_settings.pop(self.target, None)
        if action == "MERGE":
            files.atomic_write(self.path, self._rendered.encode(), self._mode)
        elif action == "REMOVE" and self.path.exists():
            self.path.unlink()
        state["settings"] = new_settings
        try:
            files.atomic_write(state_path, (json.dumps(state, indent=2) + "\n").encode(), 0o600)
        except OSError:
            state["settings"] = old_settings
            if self._original_hash is None:
                if self.path.exists():
                    self.path.unlink()
            else:
                files.atomic_write(self.path, self._original, self._mode)
            raise


def prepare(target, previous, desired, force=False):
    """Prepare changes without writing. Desired paths map to (format, values) pairs."""
    operations = []
    for filename in sorted(set(previous) | set(desired)):
        path = Path(filename)
        files.validate_parents(path)
        actual = files.current_hash(path)
        if actual == "symlink":
            raise ValueError("Settings destination symlinks are unsupported, even with --force: {}".format(path))
        data = path.read_bytes() if path.exists() else b""
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
        old = previous.get(filename)
        format_name, values = desired.get(filename, (old["format"] if old else None, {}))
        try:
            rendered, record, changes, conflicts = plan(
                format_name, data.decode("utf-8"), values, old, path.exists(), force)
        except SettingsRefused as error:
            raise ValueError("Cannot merge settings at {}: {}".format(path, error)) from None
        except ValueError as error:
            raise ValueError("Cannot merge settings at {} ({})".format(path, type(error).__name__)) from None
        operations.append(SettingsChange(
            target=target, path=path, conflicts=conflicts, _original_hash=actual,
            _original=data, _mode=mode, _rendered=rendered, _record=record, _changed_keys=changes))
    return operations


def json_loads(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON setting key: " + key)
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("Invalid JSON number: " + value)

    result = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(result, dict):
        raise ValueError("Settings must be an object")
    return result


@dataclass(frozen=True)
class Entries:
    values: list


def parse_entries(value, path):
    values = value[ENTRIES]
    if list(value) != [ENTRIES] or not isinstance(values, list) or not values:
        raise ValueError("{} must be the only key, with a non-empty list: {!r}".format(ENTRIES, path))
    if any(same(a, b) for i, a in enumerate(values) for b in values[i + 1:]):
        raise ValueError("{} values must be distinct: {!r}".format(ENTRIES, path))
    return Entries(values)


def leaves(data, prefix=()):
    for key, value in data.items():
        path = prefix + (key,)
        if key == ENTRIES:
            raise ValueError("{} needs a parent key: {!r}".format(ENTRIES, path))
        if isinstance(value, dict) and ENTRIES in value:
            yield path, parse_entries(value, path)
        elif isinstance(value, dict):
            yield from leaves(value, path)
        else:
            yield path, value


def list_at(lookup_result, path):
    items = lookup_result["value"] if lookup_result["exists"] else []
    if not isinstance(items, list):
        raise SettingsRefused("{} is not a list".format(".".join(path)))
    return items


def merge_entries(current, wanted, owned):
    """Return the new list, the owned values in it, and a "missing" or "duplicate" conflict per owned value."""
    result, kept, conflicts = list(current), [], []
    for value in owned:
        count = sum(same(item, value) for item in current)
        if count != 1:
            conflicts.append("missing" if count == 0 else "duplicate")
        elif any(same(value, item) for item in wanted):
            kept.append(value)
        else:
            result = [item for item in result if not same(item, value)]
    added = [value for value in wanted if not any(same(value, item) for item in result)]
    owned = [value for value in wanted if any(same(value, item) for item in kept + added)]
    return result + added, owned, conflicts


def lookup(data, path):
    for key in path:
        if not isinstance(data, dict):
            raise ValueError("Setting parent is not a table/object: " + repr(path))
        if key not in data:
            return {"exists": False}
        data = data[key]
    return {"exists": True, "value": data}


def same(left, right):
    # Python considers True == 1; configuration types must stay distinct.
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(same(a, b) for a, b in zip(left, right))
    return left == right


def literal(value):
    if value is None or isinstance(value, dict):
        raise ValueError("Codex managed values must be scalars or arrays of scalars")
    if isinstance(value, list):
        return "[" + ", ".join(literal(item) for item in value) + "]"
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite settings are unsupported")
    return json.dumps(value, ensure_ascii=False)


class JsonDocument:
    def __init__(self, text):
        self.text = text
        self.data = json_loads(text) if text else {}

    def get(self, path):
        return lookup(self.data, path)

    def set(self, path, entry):
        node = self.data
        for key in path[:-1]:
            if key not in node:
                if not entry["exists"]:
                    return
                node[key] = {}
            if not isinstance(node[key], dict):
                raise ValueError("Setting parent is not an object: " + repr(path))
            node = node[key]
        if entry["exists"]:
            node[path[-1]] = copy.deepcopy(entry["value"])
        else:
            node.pop(path[-1], None)
            # Leave empty objects intact: another setting may have created them.

    def render(self):
        return json.dumps(self.data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


class TomlDocument:
    def __init__(self, text):
        self.text = text
        self.scan()

    def scan(self):
        self.data = toml_loads(self.text)
        # Parser positions use normalized newlines; map spans back to original bytes.
        source = self.text.replace("\r\n", "\n")
        offsets = []
        index = 0
        while index < len(self.text):
            offsets.append(index)
            index += 2 if self.text.startswith("\r\n", index) else 1
        offsets.append(len(self.text))
        self.spans = {}
        self.tables = {(): 0}
        arrays = []
        header = ()
        pos = 0
        while pos < len(source):
            pos = skip_chars(source, pos, " \t\n")
            if pos == len(source):
                break
            if source[pos] == "#":
                end = source.find("\n", pos)
                pos = len(source) if end < 0 else end + 1
                continue
            start = pos
            if source[pos] == "[":
                array = source.startswith("[[", pos)
                key_start = skip_chars(source, pos + (2 if array else 1), " \t")
                end, header = parse_key(source, key_start)
                if array:
                    arrays.append(header)
                pos = end + (2 if array else 1)
                newline = source.find("\n", pos)
                pos = len(source) if newline < 0 else newline + 1
                if not any(header[:len(path)] == path for path in arrays):
                    self.tables[header] = offsets[pos]
                continue
            end, key = parse_key(source, pos)
            value_start = skip_chars(source, end + 1, " \t")
            value_end, _ = parse_value(source, value_start, float)
            path = header + key
            if not any(path[:len(prefix)] == prefix for prefix in arrays):
                self.spans[path] = tuple(offsets[p] for p in (start, value_start, value_end))
            pos = value_end
            # The document is already validated, so only whitespace/comment follows.
            newline = source.find("\n", pos)
            pos = len(source) if newline < 0 else newline + 1

    def get(self, path):
        entry = lookup(self.data, path)
        if entry["exists"]:
            if path not in self.spans:
                raise ValueError("Cannot manage a TOML table, inline-table child, or array-table child: " + repr(path))
            _, start, end = self.spans[path]
            # Preserve the original literal, including multiline strings and arrays.
            return {"exists": True, "raw": self.text[start:end]}
        return entry

    def set(self, path, entry):
        self.get(path)
        value = entry.get("raw")
        if entry["exists"] and value is None:
            value = literal(entry["value"])
        if path in self.spans:
            start, value_start, end = self.spans[path]
            if entry["exists"]:
                self.text = self.text[:value_start] + value + self.text[end:]
            else:
                # Retain a trailing comment when removing the assignment.
                self.text = self.text[:start] + self.text[end:]
        elif entry["exists"]:
            # Insert under the longest existing table, avoiding table redeclarations.
            parent = max((p for p in self.tables if path[:len(p)] == p), key=len)
            offset = self.tables[parent]
            key = ".".join(json.dumps(p, ensure_ascii=False) for p in path[len(parent):])
            newline = "\r\n" if "\r\n" in self.text else "\n"
            prefix = newline if offset and self.text[offset-1] != "\n" else ""
            self.text = self.text[:offset] + prefix + key + " = " + value + newline + self.text[offset:]
        self.scan()  # Reject incompatible dotted/inline-table structures before any write.

    def render(self):
        return self.text


def document(format_name, text):
    if format_name == "json":
        return JsonDocument(text)
    if format_name == "toml":
        return TomlDocument(text)
    raise ValueError("Unsupported settings format: " + format_name)


def plan(format_name, text, desired, previous, existed, force=False):
    """Return new text, ownership record, changed key labels, and local conflicts."""
    doc = document(format_name, text)
    desired = dict(leaves(desired))
    if format_name != "json" and any(isinstance(value, Entries) for value in desired.values()):
        raise ValueError(ENTRIES + " requires a JSON settings destination")
    prior = previous.get("keys", {}) if previous else {}
    keys = dict(prior)
    changes, conflicts = [], []

    def put(path, target):
        if not same(doc.get(path), target):
            doc.set(path, target)
            changes.append(".".join(path))

    def put_entries(path, items, owned, created):
        keep = items or (not created and doc.get(path)["exists"])
        put(path, {"exists": True, "value": items} if keep else {"exists": False})
        return {"entries": owned, "created": created} if owned else None

    for path in sorted(set(desired) | {tuple(json.loads(key)) for key in prior}):
        key = json.dumps(path)
        current = doc.get(path)
        old = prior.get(key)
        entries_old = old if old and "entries" in old else None
        value_old = None if entries_old else old
        wanted = desired.get(path)
        new_entries = wanted.values if isinstance(wanted, Entries) else []
        new_value = path in desired and not isinstance(wanted, Entries)
        record = None
        if entries_old:
            items, owned, reasons = merge_entries(list_at(current, path), new_entries, entries_old["entries"])
            conflicts.extend("{} ({} entry)".format(".".join(path), reason) for reason in sorted(set(reasons)))
            if "duplicate" in reasons and force:
                raise SettingsRefused("{} contains an astack entry twice. Remove one copy.".format(".".join(path)))
            if reasons and not force:
                continue
            record = put_entries(path, items, owned, entries_old["created"])
        elif value_old and not same(current, value_old["installed"]):
            conflicts.append(".".join(path))
            if not force:
                continue
        if value_old or new_value:
            before = value_old["before"] if value_old else doc.get(path)
            target = {"exists": True, "value": wanted} if new_value else before
            if format_name == "toml" and "value" in target:
                target = {"exists": True, "raw": literal(target["value"])}
            put(path, target)
            if new_value:
                record = {"before": before, "installed": doc.get(path)}
        if new_entries and not entries_old:
            current = doc.get(path)
            items, owned, _ = merge_entries(list_at(current, path), new_entries, [])
            record = put_entries(path, items, owned, not current["exists"])
        if record:
            keys[key] = record
        else:
            keys.pop(key, None)
    record = dict(previous) if previous else {
        "format": format_name, "created": not existed,
        "original": text, "last": text, "pristine": True,
    }
    record["pristine"] = record["pristine"] and text == record["last"]
    record["keys"] = keys
    if format_name == "json":
        original = document(format_name, record["original"])
        for path in sorted({tuple(json.loads(key))[:-1] for key in prior}, key=len, reverse=True):
            while path:
                if doc.get(path) == {"exists": True, "value": {}} and not original.get(path)["exists"]:
                    doc.set(path, {"exists": False})
                path = path[:-1]
    rendered = doc.render() if changes else text
    # Reuse original bytes only if all owned keys are restored and no external
    # edits occurred during ownership (including comments or formatting).
    if not keys:
        original = document(format_name, record["original"])
        if same(doc.data, original.data):
            if record["pristine"]:
                rendered = record["original"]
            if record["created"] and record["pristine"]:
                rendered = None
    record["last"] = rendered
    return rendered, record if keys else None, changes, conflicts
