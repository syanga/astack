"""Trust records for astack's Codex hooks.

Codex 0.163 runs a hook from hooks.json only when config.toml stores a matching
`hooks.state."<hooks.json path>:<snake_case event>:<group index>:<handler index>".trusted_hash`.
The hash covers the hook declaration, not the script it runs.
"""

import hashlib
import json
import re

HANDLER_FIELDS = {"type", "command", "timeout"}


def event_label(event):
    if event in ("SessionEnd", "Interrupt"):
        raise ValueError("Codex clamps {} hook timeouts, so astack cannot reproduce their trust hash".format(event))
    return re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()


def declaration_hash(label, group, handler):
    """Codex's hash of one handler: SHA-256 of the key-sorted, compact JSON of its normalized declaration."""
    if set(handler) != HANDLER_FIELDS or handler["type"] != "command":
        raise ValueError("astack can trust only command hooks with exactly these fields: " + ", ".join(sorted(HANDLER_FIELDS)))
    identity = {"event_name": label, "hooks": [{
        "type": "command", "command": handler["command"], "timeout": max(1, handler["timeout"]), "async": False,
    }]}
    if group.get("matcher") is not None:
        identity["matcher"] = group["matcher"]
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def trust(path, installed, source):
    events = json.loads(installed)["hooks"] if installed else {}
    state = {}
    for event, groups in source["hooks"].items():
        label = event_label(event)
        for group in groups["$entries"]:
            if group not in events.get(event, []):
                continue
            index = events[event].index(group)
            for position, handler in enumerate(group["hooks"]):
                key = "{}:{}:{}:{}".format(path, label, index, position)
                state[key] = {"trusted_hash": declaration_hash(label, group, handler)}
    return {"hooks": {"state": state}} if state else {}
