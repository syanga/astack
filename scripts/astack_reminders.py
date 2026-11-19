from dataclasses import dataclass
import os
from pathlib import Path
import pwd
import shlex

import claude_router
import memory_guard

GUARD_README = "README.md#keep-a-linux-machine-responsive-when-memory-runs-out"
ROUTER_README = "README.md#install-the-optional-claude-router"


@dataclass(frozen=True)
class Host:
    guard: memory_guard.Host
    linger_file: Path


def real_host() -> Host:
    # loginctl enable-linger creates this file, and systemd-logind reads it at boot.
    linger = Path("/var/lib/systemd/linger") / pwd.getpwuid(os.getuid()).pw_name
    return Host(guard=memory_guard.real_host(), linger_file=linger)


def guard_steps(state: dict, host: Host) -> list[str]:
    current = memory_guard.status(host.guard)
    if not current.available or current.min_ttl_ms_before_install is not None:
        return []
    script = shlex.quote(str(Path(memory_guard.__file__).resolve()))
    return ["TODO memory guard ({}):".format(GUARD_README), "     sudo python3 {} install".format(script)]


def router_steps(state: dict, host: Host) -> list[str]:
    layout = state.get("components", {}).get(claude_router.NAME, {}).get("layout")
    if not layout:
        return ["OPTIONAL claude-router is not installed ({})".format(ROUTER_README)]
    steps = []
    if layout["manager"] == "systemd" and not host.linger_file.exists():
        steps.append(("linger", 'sudo loginctl enable-linger "$USER"'))
    if not claude_router.is_activated(Path(layout["state"]), state):
        steps.append(("setup", "claude-router setup -account NAME"))
    return [line for name, command in steps
            for line in ("TODO claude-router {} ({}):".format(name, ROUTER_README), "     " + command)]


def pending(state: dict, host: Host | None = None) -> list[str]:
    lines = []
    for steps in (guard_steps, router_steps):
        try:
            lines += steps(state, host or real_host())
        except (OSError, ValueError, KeyError, memory_guard.GuardError):
            pass
    return lines
