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
    installed, remote = state.get("revision"), state.get("remote")
    if not installed or not remote:
        return "check skipped: the manifest records no commit or origin; rerun ./install.sh from a Git checkout with an origin remote"
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", remote, "refs/heads/main"],
            capture_output=True, text=True, timeout=20, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return "check skipped: cannot reach {}: {}".format(remote, error)
    if result.returncode == 2:
        return "check skipped: {} has no main branch".format(remote)
    if result.returncode != 0:
        reason = (result.stderr.strip().splitlines() or ["git exited with {}".format(result.returncode)])[0]
        return "check skipped: cannot read main from {}: {}".format(remote, reason)
    latest = result.stdout.split()[0]
    if latest == installed:
        return "up to date: {}".format(installed[:12])
    return "update available: installed {}, main {}".format(installed[:12], latest[:12])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: check_update.py <manifest>")
    print(check(Path(sys.argv[1])))
