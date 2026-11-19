#!/usr/bin/env python3
"""Keep a Linux machine responsive when its memory runs out.

install sets min_ttl_ms to 1000 to turn on the kernel's thrashing prevention.
systemd-tmpfiles sets the same value again at each boot.
install changes nothing when another tmpfiles.d file already sets min_ttl_ms.
uninstall restores min_ttl_ms to its value from before the first install.
status reports min_ttl_ms and the tmpfiles.d file that sets it at boot, and needs no root.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
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
class BootRule:
    file: Path
    value: str


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


def boot_rules(host: Host) -> list[BootRule]:
    try:
        result = subprocess.run(["systemd-tmpfiles", "--root={}".format(host.root), "--cat-config", "--no-pager"],
                                capture_output=True, text=True)
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        raise GuardError("systemd-tmpfiles --cat-config failed: {}".format(result.stderr.strip()))
    # --cat-config prints the files in the order systemd-tmpfiles reads them. systemd-tmpfiles applies only the
    # first rule for a path (tmpfiles.d(5)).
    rules = []
    file = None
    for line in result.stdout.splitlines():
        header = re.fullmatch(r"# (/.*\.conf)", line)
        if header:
            file = Path(header.group(1))
            continue
        fields = line.split(None, 6)
        if file is not None and len(fields) > 1 and not fields[0].startswith("#") and fields[1] == MIN_TTL_MS_PATH:
            rules.append(BootRule(file, fields[6] if len(fields) == 7 else "-"))
    return rules


def _set_min_ttl_ms(host: Host, value: int, *, dry_run: bool) -> None:
    print("SET {} {}".format(host.min_ttl_ms_file, value))
    if not dry_run:
        host.min_ttl_ms_file.write_text("{}\n".format(value))


def install(host: Host, *, dry_run: bool) -> None:
    current = status(host)
    if not current.available:
        raise GuardError(UNAVAILABLE_REASON + ", so install changed nothing")
    others = ["{} in {}".format(rule.value, rule.file) for rule in boot_rules(host) if rule.file != host.boot_file]
    if others:
        raise GuardError("another tmpfiles.d rule already sets min_ttl_ms at boot ({}), so install changed nothing"
                         .format(", ".join(others)))
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


def describe(host: Host, current: Status) -> str:
    if current.available:
        text = "{}: min_ttl_ms {}".format("on" if current.min_ttl_ms else "off", current.min_ttl_ms)
    else:
        text = "unavailable: " + UNAVAILABLE_REASON
    rules = boot_rules(host)
    if rules:
        source = "astack" if rules[0].file == host.boot_file else str(rules[0].file)
        text += ", set to {} at boot by {}".format(rules[0].value, source)
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
            print(describe(host, status(host)))
        else:
            (install if args.command == "install" else uninstall)(host, dry_run=args.dry_run)
    except (OSError, GuardError) as error:
        print("memory_guard: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
