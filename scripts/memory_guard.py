#!/usr/bin/env python3
"""Keep a Linux machine responsive when its memory runs out.

install sets min_ttl_ms to 1000 to turn on the kernel's thrashing prevention.
systemd-tmpfiles sets the same value again at each boot.
install changes nothing when another tmpfiles.d rule already sets min_ttl_ms at boot.
uninstall restores min_ttl_ms to its value from before the first install.
status reports min_ttl_ms, the value systemd-tmpfiles sets at boot, and whether the guard is installed.
status needs no root.
"""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import astack_files as files

MIN_TTL_MS = 1000
MIN_TTL_MS_PATH = "/sys/kernel/mm/lru_gen/min_ttl_ms"
BOOT_RULE = "w {} - - - - {{}}\n".format(MIN_TTL_MS_PATH)
ASTACK_MARKER = "set-by-astack-memory-guard"
BEFORE_INSTALL_PREFIX = "# min_ttl_ms before install: "
UNAVAILABLE_REASON = "the kernel's multi-gen LRU is missing or off"


class GuardError(Exception):
    pass


@dataclass(frozen=True)
class Host:
    root: Path

    @property
    def lru_gen_enabled_file(self) -> Path:
        return self.root / "sys/kernel/mm/lru_gen/enabled"

    @property
    def min_ttl_ms_file(self) -> Path:
        return self.root / MIN_TTL_MS_PATH.lstrip("/")

    @property
    def boot_file(self) -> Path:
        return self.root / "etc/tmpfiles.d/astack-memory-guard.conf"


@dataclass(frozen=True)
class AtBoot:
    value: str | None
    by_astack: bool
    value_without_astack: str | None


@dataclass(frozen=True)
class Status:
    lru_gen_on: bool
    min_ttl_ms: int | None
    min_ttl_ms_before_install: int | None

    @property
    def available(self) -> bool:
        return self.lru_gen_on and self.min_ttl_ms is not None

    @property
    def installed(self) -> bool:
        return self.min_ttl_ms_before_install is not None and bool(self.min_ttl_ms)


def real_host() -> Host:
    return Host(Path("/"))


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return None


def _min_ttl_ms_before_install(host: Host) -> int | None:
    text = _read(host.boot_file)
    if text is None:
        return None
    match = re.search("^{}(\\d+)$".format(re.escape(BEFORE_INSTALL_PREFIX)), text, re.MULTILINE)
    if match is None:
        raise GuardError("{} has no line that starts with '{}'".format(host.boot_file, BEFORE_INSTALL_PREFIX))
    return int(match.group(1))


def status(host: Host) -> Status:
    enabled = _read(host.lru_gen_enabled_file)
    ttl = _read(host.min_ttl_ms_file)
    return Status(
        lru_gen_on=enabled is not None and int(enabled, 16) & 1 == 1,
        min_ttl_ms=None if ttl is None else int(ttl),
        min_ttl_ms_before_install=_min_ttl_ms_before_install(host),
    )


def _tmpfiles(*arguments: str, config: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["systemd-tmpfiles", *arguments], input=config, capture_output=True, text=True,
                          env={**os.environ, "SYSTEMD_COLORS": "0"})


def _value_at_boot(host: Host, config: str) -> str | None:
    with tempfile.TemporaryDirectory(prefix="astack-memory-guard-") as scratch:
        replay = Host(Path(scratch))
        replay.min_ttl_ms_file.parent.mkdir(parents=True)
        replay.min_ttl_ms_file.touch()
        # Under --root, systemd-tmpfiles resolves the user and group names in rules from the root's /etc/passwd and
        # /etc/group, and it drops a rule whose names it cannot resolve.
        (replay.root / "etc").mkdir()
        for name in ("etc/passwd", "etc/group"):
            with contextlib.suppress(FileNotFoundError):
                shutil.copyfile(host.root / name, replay.root / name)
        result = _tmpfiles("--root=" + scratch, "--boot", "--create", "--prefix=/sys", "-", config=config)
        # systemd-tmpfiles exits with EX_DATAERR or EX_CANTCREAT when some lines fail, and boot skips those lines too.
        if result.returncode not in (0, os.EX_DATAERR, os.EX_CANTCREAT):
            raise GuardError("systemd-tmpfiles --create failed: " + result.stderr.strip())
        return replay.min_ttl_ms_file.read_bytes().decode(errors="replace").strip() or None


def at_boot(host: Host) -> AtBoot:
    try:
        result = _tmpfiles("--root={}".format(host.root), "--cat-config", "--no-pager")
    except FileNotFoundError:
        return AtBoot(value=None, by_astack=False, value_without_astack=None)
    if result.returncode != 0:
        raise GuardError("systemd-tmpfiles --cat-config failed: " + result.stderr.strip())
    config = result.stdout
    try:
        astack = "# {}\n{}".format(host.boot_file, host.boot_file.read_text())
    except FileNotFoundError:
        value = _value_at_boot(host, config)
        return AtBoot(value=value, by_astack=False, value_without_astack=value)
    if astack not in config or BOOT_RULE.format(MIN_TTL_MS) not in astack:
        raise GuardError("systemd-tmpfiles --cat-config does not list the rule in {}".format(host.boot_file))
    without = _value_at_boot(host, config.replace(astack, "", 1))
    # A w line does not truncate the file it writes, so a later, shorter value can leave digits of astack's value.
    # A marker longer than any min_ttl_ms value shows whether astack's write is the last one.
    marked = astack.replace(BOOT_RULE.format(MIN_TTL_MS), BOOT_RULE.format(ASTACK_MARKER))
    by_astack = _value_at_boot(host, config.replace(astack, marked, 1)) == ASTACK_MARKER
    return AtBoot(value=str(MIN_TTL_MS) if by_astack else without, by_astack=by_astack, value_without_astack=without)


def _set_min_ttl_ms(host: Host, value: int, *, dry_run: bool) -> None:
    print("SET {} {}".format(host.min_ttl_ms_file, value))
    if not dry_run:
        host.min_ttl_ms_file.write_text("{}\n".format(value))


def install(host: Host, *, dry_run: bool) -> None:
    current = status(host)
    if not current.available:
        raise GuardError(UNAVAILABLE_REASON + ", so install changed nothing")
    other = at_boot(host).value_without_astack
    if other is not None:
        raise GuardError("without astack's rule, another tmpfiles.d rule would set min_ttl_ms to {} at boot, so "
                         "install changed nothing. systemd-tmpfiles --cat-config lists the rules".format(other))
    before = current.min_ttl_ms if current.min_ttl_ms_before_install is None else current.min_ttl_ms_before_install
    print("WRITE {}".format(host.boot_file))
    if not dry_run:
        files.atomic_write(host.boot_file, (
            "# Written by astack scripts/memory_guard.py. Its uninstall restores min_ttl_ms and deletes this file.\n"
            "{}{}\n"
            "{}"
        ).format(BEFORE_INSTALL_PREFIX, before, BOOT_RULE.format(MIN_TTL_MS)).encode())
    _set_min_ttl_ms(host, MIN_TTL_MS, dry_run=dry_run)


def uninstall(host: Host, *, dry_run: bool) -> None:
    current = status(host)
    if current.min_ttl_ms_before_install is None:
        print("Nothing to uninstall: {} does not exist".format(host.boot_file))
        return
    if current.min_ttl_ms is not None:
        _set_min_ttl_ms(host, current.min_ttl_ms_before_install, dry_run=dry_run)
    print("REMOVE {}".format(host.boot_file))
    if not dry_run:
        host.boot_file.unlink()


def describe(host: Host) -> str:
    current = status(host)
    if current.available:
        text = "{}: min_ttl_ms {}".format("on" if current.min_ttl_ms else "off", current.min_ttl_ms)
    else:
        text = "unavailable: " + UNAVAILABLE_REASON
    if current.min_ttl_ms is not None:
        text += _describe_boot(host)
    return "thrashing prevention  {}\nmemory guard          {}".format(
        text, "installed" if current.installed else "not installed")


def _describe_boot(host: Host) -> str:
    try:
        boot = at_boot(host)
    except GuardError as error:
        return ", boot value unknown: {}".format(error)
    if boot.value is None:
        return ""
    if not boot.by_astack:
        source = "another tmpfiles.d rule"
    elif boot.value_without_astack is None:
        source = "astack"
    else:
        source = "astack, over another tmpfiles.d rule"
    return ", set to {} at boot by {}".format(boot.value, source)


def main(argv: Sequence[str] | None = None, host: Host | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("status", "install", "uninstall"))
    parser.add_argument("--dry-run", action="store_true", help="show the changes without making them")
    args = parser.parse_args(argv)
    if sys.platform != "linux":
        print("memory_guard: nothing to do on {}; it changes only Linux machines".format(sys.platform))
        return 0
    if args.command != "status" and not args.dry_run and os.geteuid() != 0:
        print("memory_guard: {} needs root; run it with sudo".format(args.command), file=sys.stderr)
        return 1
    host = host or real_host()
    try:
        if args.command == "status":
            print(describe(host))
        else:
            (install if args.command == "install" else uninstall)(host, dry_run=args.dry_run)
    except (OSError, GuardError) as error:
        print("memory_guard: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
