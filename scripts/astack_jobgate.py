from dataclasses import asdict, dataclass
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

import claude_router

NAME = "jobgate"
MARKER = "# astack: jobgate on PATH"
BASH_LOGIN_FILES = (".bash_profile", ".bash_login", ".profile")
BOAT_INSTALL = "curl -fsSL https://boat.dev/install | sh"
BOAT_SETUP = "docs/jobgate-setup.md#run-heavy-commands-on-boat"


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


@dataclass
class PathLine:
    file: str
    marker: str
    export: str
    created: bool = False
    separator: bool = False
    existing: int = 0

    @property
    def block(self):
        return (self.marker + "\n" + self.export + "\n").encode()


class Record:
    def __init__(self, state, save):
        self.state, self.save = state, save
        record = state.get(NAME, {})
        self.lib = record.get("lib")
        self.path_lines = [PathLine(**line) for line in record.get("path_lines", [])]

    def store(self):
        path_lines = [asdict(line) for line in self.path_lines]
        record = {key: value for key, value in (("lib", self.lib), ("path_lines", path_lines)) if value}
        if record:
            self.state[NAME] = record
        else:
            self.state.pop(NAME, None)
        self.save()


def recorded_lib(state):
    if state.get(NAME, {}).get("lib"):
        return Path(state[NAME]["lib"])
    for filename in state.get("targets", {}).get(NAME, {}):
        if Path(filename).name == "jobgate.py":
            return Path(filename).parent
    return None


def on_path(directory):
    target = os.path.realpath(str(directory))
    return any(entry and os.path.realpath(entry) == target for entry in os.environ.get("PATH", "").split(os.pathsep))


def startup_files(home, use_env):
    """Interactive zsh reads .zshrc and login zsh .zprofile. Interactive non-login bash reads .bashrc, and login bash
    reads only the first of BASH_LOGIN_FILES that exists."""
    shell = Path(os.environ.get("SHELL", "")).name or ("zsh" if sys.platform == "darwin" else "bash")
    if shell == "zsh":
        zdotdir = Path(os.path.abspath(os.path.expanduser((os.environ.get("ZDOTDIR") if use_env else None) or str(home))))
        return [zdotdir / ".zshrc", zdotdir / ".zprofile"]
    if shell == "bash":
        login = next((home / name for name in BASH_LOGIN_FILES if (home / name).is_file()), home / ".profile")
        return [home / ".bashrc", login]
    return []


def copies(path, block):
    try:
        return path.read_bytes().count(block)
    except OSError:
        return 0


def needs_separator(path):
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return bool(data) and not data.endswith(b"\n")


def remove_last_block(path, block, separator, created, existing):
    target = path.resolve() if path.is_symlink() else path
    for _ in range(3):
        try:
            data = target.read_bytes()
        except FileNotFoundError:
            return False
        if data.count(block) <= existing:
            return False
        index = data.rfind(block)
        end = index + len(block)
        if separator and end == len(data) and data[index - 1:index] == b"\n":
            index -= 1
        rest = data[:index] + data[end:]
        mode = stat.S_IMODE(target.stat().st_mode)
        fd, temporary = tempfile.mkstemp(prefix=".astack-", dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(rest)
                os.fchmod(stream.fileno(), mode)
            if target.read_bytes() != data:
                continue
            if created and not rest:
                target.unlink()
            else:
                os.replace(temporary, target)
            return True
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    raise OSError("the file kept changing during removal")


def remove_path_lines(state, dry_run, save):
    owned = Record(state, save)
    for line in list(owned.path_lines):
        print("REMOVE {} {}".format(line.file, line.export))
        if dry_run:
            continue
        try:
            remove_last_block(Path(line.file), line.block, line.separator, line.created, line.existing)
        except OSError as error:
            print("WARN {} ({}); uninstall again to remove: {} / {}".format(
                line.file, claude_router.reason(error), line.marker, line.export))
        else:
            owned.path_lines.remove(line)
            owned.store()


def add_path_lines(state, home, use_env, dry_run, save):
    directory = bin_path(home).parent
    escaped = re.sub(r'([\\"$`])', r"\\\1", str(directory))
    export = 'export PATH="{}:$PATH"'.format(escaped)
    if on_path(directory):
        print("KEEP PATH ({} is on PATH)".format(directory))
        return
    startup = startup_files(home, use_env)
    if not startup:
        print("SKIP PATH (no shell file known for SHELL={}; to add it yourself: {})".format(
            os.environ.get("SHELL", ""), export))
        return
    owned = Record(state, save)
    for path in startup:
        line = next((line for line in owned.path_lines if line.file == str(path)), None)
        if line is not None and copies(path, line.block) > line.existing:
            print("KEEP {}".format(path))
            continue
        print("APPEND {} {}".format(path, export))
        if dry_run:
            continue
        if line is None:
            line = PathLine(str(path), MARKER, export)
            owned.path_lines.append(line)
        line.created, line.separator, line.existing = not path.exists(), needs_separator(path), copies(path, line.block)
        owned.store()
        try:
            line.created, line.separator = claude_router.append_block(path, line.block)
        except OSError as error:
            print("WARN {} ({}); to add it yourself: {}".format(path, claude_router.reason(error), export))
            owned.path_lines.remove(line)
        owned.store()


def update_shell_and_library(command, state, home, use_env, recorded, dry_run, save):
    if command == "install":
        add_path_lines(state, home, use_env, dry_run, save)
        current = lib_dir(home, use_env)
        if recorded != current:
            prune(recorded, dry_run)
        if not dry_run:
            record = Record(state, save)
            record.lib = str(current)
            record.store()
        return
    remove_path_lines(state, dry_run, save)
    prune(recorded, dry_run)
    if not dry_run and recorded is not None:
        record = Record(state, save)
        record.lib = None
        record.store()


def offer_boat(home, dry_run):
    # boat's installer (https://boat.dev/install) links the CLI into ~/.local/bin.
    search = os.pathsep.join([os.environ.get("PATH", ""), str(bin_path(home).parent)])
    if shutil.which("boat", path=search):
        return
    print("OPTIONAL boat is not installed, so jobgate runs commands only on this machine ({}):".format(BOAT_SETUP))
    print("     " + BOAT_INSTALL)
    if dry_run or not claude_router.on_terminal():
        return
    try:
        answer = input("Install boat now? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if answer.strip().lower() not in ("y", "yes"):
        return
    try:
        status = subprocess.run(["sh", "-c", BOAT_INSTALL], env=dict(os.environ, HOME=str(home))).returncode
        failure = "exit status {}".format(status) if status else None
    except KeyboardInterrupt:
        print()
        failure = "interrupted"
    if not shutil.which("boat", path=search):
        print("WARN boat install ({}); to try again: {}".format(failure or "boat is still missing", BOAT_INSTALL))
    elif failure:
        # boat's installer ends with its sign-in, `boat onboard`, so a failure after the CLI is in place is the sign-in's.
        print("WARN boat sign-in ({}); to sign in: boat login".format(failure))


def prune(lib, dry_run):
    if lib is None or dry_run:
        return
    for directory in (lib / "_vendor/tomllib", lib / "_vendor", lib, lib.parent):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
