from dataclasses import dataclass
import json
import os
from pathlib import Path
import pwd
import shlex
from typing import Callable

import claude_router
import memory_guard

INSTALL = Path(__file__).resolve().parent.parent / "install.sh"
GUARD_README = "README.md#keep-a-linux-machine-responsive-when-memory-runs-out"
ROUTER_README = "README.md#install-the-optional-claude-router"


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
    if not current.available or current.installed:
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


def router_steps(state: dict, host: Host) -> list[str]:
    record = state.get("components", {}).get(claude_router.NAME, {})
    layout = record.get("layout")
    if not layout:
        return ["OPTIONAL claude-router is not installed ({})".format(ROUTER_README)]
    if record.get("pending"):
        command = "{} --component {}".format(shlex.quote(str(INSTALL)), claude_router.NAME)
        return todo("claude-router install", ROUTER_README, command)
    return best_effort(lambda: linger_step(layout, host)) + best_effort(lambda: setup_step(layout))


def best_effort(step: Callable[[], list[str]]) -> list[str]:
    try:
        return step()
    except Exception:
        return []


def pending(state: dict, host: Host | None = None) -> list[str]:
    machine = host or real_host()
    return best_effort(lambda: guard_step(machine)) + best_effort(lambda: router_steps(state, machine))
