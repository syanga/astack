#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys


def check(manifest):
    try:
        state = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return "check skipped: cannot read {}: {}".format(manifest, error)
    targets, commits, remote = sorted(state.get("targets", {})), state.get("commits", {}), state.get("remote")
    missing = [target for target in targets if target not in commits]
    gap = None
    if not targets:
        gap = "no installed targets"
    elif missing:
        gap = "no installed commit for " + ", ".join(missing)
    elif not remote:
        gap = "no origin remote"
    if gap:
        return "check skipped: the manifest records {}. Rerun ./install.sh from a Git checkout with an origin remote.".format(gap)
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", remote, "refs/heads/main"],
            capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL,
            env=dict(os.environ, GIT_TERMINAL_PROMPT="0"), start_new_session=True,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return "check skipped: cannot reach {}: {}".format(remote, error)
    if result.returncode == 2:
        return "check skipped: {} has no main branch".format(remote)
    if result.returncode != 0:
        reason = (result.stderr.strip().splitlines() or ["git exited with {}".format(result.returncode)])[0]
        return "check skipped: cannot read main from {}: {}".format(remote, reason)
    latest = result.stdout.split()[0]
    behind = ["{} at {}".format(target, commits[target][:12]) for target in targets if commits[target] != latest]
    if not behind:
        return "up to date: {}".format(latest[:12])
    return "update available: {}, main {}".format(", ".join(behind), latest[:12])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: check_update.py <manifest>")
    print(check(Path(sys.argv[1])))
