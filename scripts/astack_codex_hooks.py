"""Trust records for the hooks in astack's local Codex plugins.

Codex 0.163 runs a plugin hook only when config.toml stores a matching
`hooks.state."<plugin>@<marketplace>:hooks/hooks.json:<snake_case event>:<group index>:<handler index>".trusted_hash`.
The hash covers the hook declaration, not the script it runs.
"""

import hashlib
import json
from pathlib import Path
import re

HANDLER_FIELDS = {"type", "command", "timeout"}
HOOKS_FILE = "hooks/hooks.json"


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


def plugin_root(marketplace, name):
    manifest = json.loads((Path(marketplace) / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
    for entry in manifest["plugins"]:
        if entry["name"] == name:
            return Path(marketplace) / entry["source"]["path"]
    raise ValueError("Marketplace {} does not list plugin {}".format(marketplace, name))


def trust(plugin, marketplace):
    """Trust records for every hook in a local plugin's hooks/hooks.json."""
    path = plugin_root(marketplace, plugin.partition("@")[0]) / HOOKS_FILE
    if not path.exists():
        return {}
    state = {}
    for event, groups in json.loads(path.read_text(encoding="utf-8"))["hooks"].items():
        label = event_label(event)
        for index, group in enumerate(groups):
            for position, handler in enumerate(group["hooks"]):
                key = "{}:{}:{}:{}:{}".format(plugin, HOOKS_FILE, label, index, position)
                state[key] = {"trusted_hash": declaration_hash(label, group, handler)}
    return state
