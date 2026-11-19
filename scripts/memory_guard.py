#!/usr/bin/env python3
"""Keep a Linux machine responsive when its memory runs out.

install sets min_ttl_ms to 1000 to turn on the kernel's thrashing prevention.
systemd-tmpfiles sets the same value again at each boot.
install changes nothing when another tmpfiles.d rule already sets min_ttl_ms at boot.
uninstall restores min_ttl_ms to its value from before the first install.
status reports min_ttl_ms and the value systemd-tmpfiles sets at boot, and needs no root.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import posixpath
import re
import subprocess
import sys
import tempfile
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import astack_files as files

MIN_TTL_MS = 1000
MIN_TTL_MS_PATH = "/sys/kernel/mm/lru_gen/min_ttl_ms"
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
    without_astack: str | None


@dataclass(frozen=True)
class Status:
    lru_gen_on: bool
    min_ttl_ms: int | None
    min_ttl_ms_before_install: int | None

    @property
    def available(self) -> bool:
        return self.lru_gen_on and self.min_ttl_ms is not None


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


def _value_at_boot(config: str) -> str | None:
    with tempfile.TemporaryDirectory(prefix="astack-memory-guard-") as scratch:
        target = Path(scratch) / MIN_TTL_MS_PATH.lstrip("/")
        target.parent.mkdir(parents=True)
        target.touch()
        result = _tmpfiles("--root=" + scratch, "--boot", "--create", "--prefix=" + posixpath.dirname(MIN_TTL_MS_PATH),
                           "-", config=config)
        # systemd-tmpfiles exits with EX_DATAERR or EX_CANTCREAT when some lines fail, and boot skips those lines too.
        if result.returncode not in (0, os.EX_DATAERR, os.EX_CANTCREAT):
            raise GuardError("systemd-tmpfiles --create failed: " + result.stderr.strip())
        return target.read_text().strip() or None


def at_boot(host: Host) -> AtBoot:
    try:
        result = _tmpfiles("--root={}".format(host.root), "--cat-config", "--no-pager")
    except FileNotFoundError:
        return AtBoot(value=None, without_astack=None)
    if result.returncode != 0:
        raise GuardError("systemd-tmpfiles --cat-config failed: " + result.stderr.strip())
    config = result.stdout
    value = _value_at_boot(config)
    try:
        astack = "# {}\n{}".format(host.boot_file, host.boot_file.read_text())
    except FileNotFoundError:
        return AtBoot(value=value, without_astack=value)
    if astack not in config:
        raise GuardError("systemd-tmpfiles --cat-config does not list {}".format(host.boot_file))
    return AtBoot(value=value, without_astack=_value_at_boot(config.replace(astack, "", 1)))


def _set_min_ttl_ms(host: Host, value: int, *, dry_run: bool) -> None:
    print("SET {} {}".format(host.min_ttl_ms_file, value))
    if not dry_run:
        host.min_ttl_ms_file.write_text("{}\n".format(value))


def install(host: Host, *, dry_run: bool) -> None:
    current = status(host)
    if not current.available:
        raise GuardError(UNAVAILABLE_REASON + ", so install changed nothing")
    other = at_boot(host).without_astack
    if other is not None:
        raise GuardError("another tmpfiles.d rule sets min_ttl_ms to {} at boot, so install changed nothing. "
                         "systemd-tmpfiles --cat-config lists the rules".format(other))
    before = current.min_ttl_ms if current.min_ttl_ms_before_install is None else current.min_ttl_ms_before_install
    print("WRITE {}".format(host.boot_file))
    if not dry_run:
        files.atomic_write(host.boot_file, (
            "# Written by astack scripts/memory_guard.py. Its uninstall restores min_ttl_ms and deletes this file.\n"
            "{}{}\n"
            "w {} - - - - {}\n"
        ).format(BEFORE_INSTALL_PREFIX, before, MIN_TTL_MS_PATH, MIN_TTL_MS).encode())
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
    boot = at_boot(host) if current.min_ttl_ms is not None else AtBoot(value=None, without_astack=None)
    if boot.value is not None:
        if boot.without_astack is None:
            source = "astack"
        elif boot.without_astack == boot.value:
            source = "another tmpfiles.d rule"
        else:
            source = "astack, over another tmpfiles.d rule"
        text += ", set to {} at boot by {}".format(boot.value, source)
    return "thrashing prevention  " + text


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
