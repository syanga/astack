from contextlib import suppress
from dataclasses import dataclass
import json
import os
from pathlib import Path
import pwd
import shlex

import claude_router
import memory_guard

GUARD_README = "README.md#keep-a-linux-machine-responsive-when-memory-runs-out"
ROUTER_README = "README.md#install-the-optional-claude-router"
UNREADABLE = (OSError, ValueError, KeyError, AttributeError, memory_guard.GuardError)


@dataclass(frozen=True)
class Host:
    guard: memory_guard.Host
    linger_dir: Path


def real_host() -> Host:
    # loginctl enable-linger USER creates the file USER here, and systemd-logind reads it at boot.
    return Host(guard=memory_guard.real_host(), linger_dir=Path("/var/lib/systemd/linger"))


def todo(name: str, readme: str, command: str) -> list[str]:
    return ["TODO {} ({}):".format(name, readme), "     " + command]


def guard_step(host: Host) -> list[str]:
    current = memory_guard.status(host.guard)
    if not current.available or current.min_ttl_ms_before_install is not None:
        return []
    script = shlex.quote(str(Path(memory_guard.__file__).resolve()))
    return todo("memory guard", GUARD_README, "sudo python3 {} install".format(script))


def linger_step(layout: dict, host: Host) -> list[str]:
    if layout["manager"] != "systemd" or (host.linger_dir / pwd.getpwuid(os.getuid()).pw_name).exists():
        return []
    return todo("claude-router linger", ROUTER_README, 'sudo loginctl enable-linger "$USER"')


def setup_step(layout: dict) -> list[str]:
    state_dir = Path(layout["state"])
    if (claude_router.read_journal(state_dir) or {}).get("phase") == "active":
        return []
    config = state_dir / "config.json"
    enrolled = config.exists() and bool(json.loads(config.read_text(encoding="utf-8")).get("accounts"))
    return todo("claude-router setup", ROUTER_README,
                "claude-router setup" if enrolled else "claude-router setup -account NAME")


def pending(state: dict, host: Host | None = None) -> list[str]:
    host = host or real_host()
    layout = state.get("components", {}).get(claude_router.NAME, {}).get("layout")
    steps = [lambda: guard_step(host)]
    if layout:
        steps += [lambda: linger_step(layout, host), lambda: setup_step(layout)]
    lines = []
    for step in steps:
        with suppress(*UNREADABLE):
            lines += step()
    if not layout:
        lines.append("OPTIONAL claude-router is not installed ({})".format(ROUTER_README))
    return lines
