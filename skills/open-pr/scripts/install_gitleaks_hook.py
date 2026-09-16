#!/usr/bin/env python3
"""Install astack's Gitleaks pre-push hook in the current repository.

Finds the astack checkout through the state manifest the installer writes,
then runs that checkout's scripts/git_hooks.py. Extra arguments pass through,
for example --dry-run or --replace-gstack.
"""

import json
import os
from pathlib import Path
import subprocess
import sys


def manifest_path():
    root = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(root) / "astack/manifest.json"


def main(argv):
    path = manifest_path()
    try:
        source = json.loads(path.read_text(encoding="utf-8")).get("source")
    except (OSError, ValueError):
        source = None
    if not source:
        sys.exit("astack: {} records no checkout path; run ./install.sh from the astack checkout to record it".format(path))
    hooks = Path(source) / "scripts/git_hooks.py"
    if not hooks.is_file():
        sys.exit("astack: {} is missing; run ./install.sh from the current astack checkout".format(hooks))
    return subprocess.call([sys.executable, str(hooks), "install", "--repo", ".", *argv])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
