import os
from pathlib import Path
import re
import shlex

import claude_router

NAME = "jobgate"
MARKER = "# astack: jobgate on PATH"


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


def on_path(directory):
    target = os.path.realpath(str(directory))
    return any(entry and os.path.realpath(entry) == target for entry in os.environ.get("PATH", "").split(os.pathsep))


PATH_ASSIGNMENT = re.compile(r"""(?:^|[\s;])(?:export\s+)?PATH=("[^"]*"|'[^']*'|[^\s;]+)""")


def shell_puts_on_path(path, directory, home):
    try:
        text = path.read_bytes().decode(errors="replace")
    except OSError:
        return False
    target = os.path.normpath(str(directory))
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for value in PATH_ASSIGNMENT.findall(line):
            for entry in value.strip("\"'").split(":"):
                entry = entry.replace("${HOME}", str(home)).replace("$HOME", str(home))
                if entry == "~" or entry.startswith("~/"):
                    entry = str(home) + entry[1:]
                if entry and os.path.normpath(entry) == target:
                    return True
    return False


def update_path(command, state, home, use_env, dry_run, save):
    records = state.setdefault("shell_path", {})
    owned = records.get(NAME)
    if command == "uninstall":
        if owned:
            line = owned["lines"][1]
            print("REMOVE {} {}".format(owned["file"], line))
            if not dry_run:
                try:
                    claude_router.remove_block(Path(owned["file"]), ("\n".join(owned["lines"]) + "\n").encode(),
                                               owned["separator"], owned["created"])
                except OSError as error:
                    print("WARN {} ({}); uninstall again to remove: {}".format(
                        owned["file"], claude_router.reason(error), " / ".join(owned["lines"])))
                else:
                    records.pop(NAME)
    else:
        directory = bin_path(home).parent
        escaped = re.sub(r'([\\"$`])', r"\\\1", str(directory))
        lines = (MARKER, 'export PATH="{}:$PATH"'.format(escaped))
        block = "\n".join(lines) + "\n"
        shell_file = Path(owned["file"]) if owned else claude_router.shell_file_for(home, use_env)
        if owned and shell_file.is_file() and block in shell_file.read_bytes().decode(errors="replace"):
            print("KEEP {}".format(shell_file))
            return
        records.pop(NAME, None)
        if on_path(directory):
            print("KEEP PATH ({} is on PATH)".format(directory))
        elif shell_file is None:
            print("SKIP PATH (no shell file known for SHELL={}; to add it yourself: {})".format(
                os.environ.get("SHELL", ""), lines[1]))
        elif shell_puts_on_path(shell_file, directory, home):
            print("KEEP {} (already puts {} on PATH)".format(shell_file, directory))
        elif dry_run:
            print("APPEND {} {}".format(shell_file, lines[1]))
        else:
            try:
                created, separator = claude_router.append_block(shell_file, block.encode())
            except OSError as error:
                print("WARN {} ({}); to add it yourself: {}".format(shell_file, claude_router.reason(error), lines[1]))
            else:
                records[NAME] = {"file": str(shell_file), "lines": list(lines), "created": created,
                                 "separator": separator}
                print("APPEND {} {}".format(shell_file, lines[1]))
    if not records:
        state.pop("shell_path", None)
    if state.get("shell_path", {}).get(NAME) != owned and not dry_run:
        save()


def prune(home, use_env, dry_run):
    lib = lib_dir(home, use_env)
    if dry_run:
        return
    for directory in (lib / "_vendor/tomllib", lib / "_vendor", lib, lib.parent):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
