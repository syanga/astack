import os
from pathlib import Path
import re
import shlex
import sys

import claude_router

NAME = "jobgate"
MARKER = "# astack: jobgate on PATH"
BASH_LOGIN_FILES = (".bash_profile", ".bash_login", ".profile")


def lib_dir(home, use_env):
    return claude_router.env_dir("XDG_DATA_HOME", ".local/share", home, use_env) / "astack" / NAME


def bin_path(home):
    return home / ".local/bin" / NAME


def desired_files(scripts, home, use_env):
    lib = lib_dir(home, use_env)
    result = {str(lib / "jobgate.py"): ((scripts / "jobgate.py").read_bytes(), 0o644)}
    for source in sorted((scripts / "_vendor/tomllib").iterdir()):
        if source.is_file() and source.suffix not in (".pyc", ".pyo"):
            result[str(lib / "_vendor/tomllib" / source.name)] = (source.read_bytes(), 0o644)
    wrapper = '#!/bin/sh\nexec python3 {} "$@"\n'.format(shlex.quote(str(lib / "jobgate.py")))
    result[str(bin_path(home))] = (wrapper.encode(), 0o755)
    return result


def recorded_lib(state):
    for filename in state.get("targets", {}).get(NAME, {}):
        if Path(filename).name == "jobgate.py":
            return Path(filename).parent
    return None


def on_path(directory):
    target = os.path.realpath(str(directory))
    return any(entry and os.path.realpath(entry) == target for entry in os.environ.get("PATH", "").split(os.pathsep))


def startup_files(home, use_env):
    """Interactive non-login bash reads .bashrc. Login bash reads only the first of BASH_LOGIN_FILES that exists."""
    shell = Path(os.environ.get("SHELL", "")).name or ("zsh" if sys.platform == "darwin" else "bash")
    if shell == "zsh":
        zdotdir = os.environ.get("ZDOTDIR") if use_env else None
        return [Path(zdotdir or home) / ".zshrc"]
    if shell == "bash":
        login = next((home / name for name in BASH_LOGIN_FILES if (home / name).is_file()), home / ".profile")
        return [home / ".bashrc", login]
    return []


class PathRecords:
    def __init__(self, state, dry_run, save):
        self.state, self.dry_run, self.save = state, dry_run, save
        self.records = dict(state.get("shell_path", {}))
        self.entries = list(self.records.get(NAME) or [])

    def store(self):
        if self.entries:
            self.records[NAME] = self.entries
        else:
            self.records.pop(NAME, None)
        if self.records:
            self.state["shell_path"] = self.records
        else:
            self.state.pop("shell_path", None)
        if not self.dry_run:
            self.save()


def needs_separator(path):
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return bool(data) and not data.endswith(b"\n")


def remove_path_lines(state, dry_run, save):
    owned = PathRecords(state, dry_run, save)
    for entry in list(owned.entries):
        print("REMOVE {} {}".format(entry["file"], entry["lines"][1]))
        if dry_run:
            continue
        try:
            claude_router.remove_block(Path(entry["file"]), ("\n".join(entry["lines"]) + "\n").encode(),
                                       entry["separator"], entry["created"])
        except OSError as error:
            print("WARN {} ({}); uninstall again to remove: {}".format(
                entry["file"], claude_router.reason(error), " / ".join(entry["lines"])))
        else:
            owned.entries.remove(entry)
            owned.store()


def add_path_lines(state, home, use_env, dry_run, save):
    directory = bin_path(home).parent
    escaped = re.sub(r'([\\"$`])', r"\\\1", str(directory))
    lines = (MARKER, 'export PATH="{}:$PATH"'.format(escaped))
    block = "\n".join(lines) + "\n"
    if on_path(directory):
        print("KEEP PATH ({} is on PATH)".format(directory))
        return
    files = startup_files(home, use_env)
    if not files:
        print("SKIP PATH (no shell file known for SHELL={}; to add it yourself: {})".format(
            os.environ.get("SHELL", ""), lines[1]))
        return
    owned = PathRecords(state, dry_run, save)
    for path in files:
        entry = next((entry for entry in owned.entries if entry["file"] == str(path)), None)
        try:
            present = block in path.read_bytes().decode(errors="replace")
        except OSError:
            present = False
        if entry is not None and present:
            print("KEEP {}".format(path))
            continue
        print("APPEND {} {}".format(path, lines[1]))
        if dry_run:
            continue
        if entry is None:
            entry = {"file": str(path), "lines": list(lines)}
            owned.entries.append(entry)
        entry.update(created=not path.exists(), separator=needs_separator(path))
        owned.store()
        try:
            entry["created"], entry["separator"] = claude_router.append_block(path, block.encode())
        except OSError as error:
            print("WARN {} ({}); to add it yourself: {}".format(path, claude_router.reason(error), lines[1]))
            owned.entries.remove(entry)
        owned.store()


def prune(lib, dry_run):
    if lib is None or dry_run:
        return
    for directory in (lib / "_vendor/tomllib", lib / "_vendor", lib, lib.parent):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
