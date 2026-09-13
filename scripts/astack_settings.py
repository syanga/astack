"""Plan reversible, per-key JSON/TOML edits; preserve unrelated configuration."""

import copy
import json
import math

from _vendor.tomllib import loads as toml_loads
from _vendor.tomllib._parser import parse_key, parse_value, skip_chars


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


def leaves(data, prefix=()):
    for key, value in data.items():
        path = prefix + (key,)
        if isinstance(value, dict):
            yield from leaves(value, path)
        else:
            yield path, value


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
                end, header = parse_key(source, pos + (2 if array else 1))
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
    prior = previous.get("keys", {}) if previous else {}
    keys = dict(prior)
    changes, conflicts = [], []
    for path in sorted(set(desired) | {tuple(json.loads(key)) for key in prior}):
        key = json.dumps(path)
        current = doc.get(path)
        old = prior.get(key)
        if old and not same(current, old["installed"]):
            conflicts.append(".".join(path))
            if not force:
                continue
        before = old["before"] if old else current
        target = {"exists": True, "value": desired[path]} if path in desired else before
        if format_name == "toml" and "value" in target:
            target = {"exists": True, "raw": literal(target["value"])}
        if not same(current, target):
            doc.set(path, target)
            changes.append(".".join(path))
        if path in desired:
            keys[key] = {"before": before, "installed": doc.get(path)}
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
