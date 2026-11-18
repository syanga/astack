#!/usr/bin/env python3
"""Keep a Linux machine responsive when its memory runs out.

install turns on the kernel's thrashing prevention now and at each boot.
uninstall restores the previous setting. status reports it without root.
The setting at boot needs systemd-tmpfiles.
"""

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
from typing import Callable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import astack_files as files

MIN_TTL_MS = 1000
RECORD = "# min_ttl_ms before install: "

Say = Callable[[str], None]


class GuardError(Exception):
    pass


@dataclass(frozen=True)
class Host:
    lru_gen_enabled_file: Path
    min_ttl_ms_file: Path
    boot_file: Path


@dataclass(frozen=True)
class Status:
    lru_gen_on: bool
    min_ttl_ms: int | None
    min_ttl_ms_before_install: int | None

    @property
    def available(self) -> bool:
        return self.lru_gen_on and self.min_ttl_ms is not None


def real_host() -> Host:
    return Host(
        lru_gen_enabled_file=Path("/sys/kernel/mm/lru_gen/enabled"),
        min_ttl_ms_file=Path("/sys/kernel/mm/lru_gen/min_ttl_ms"),
        boot_file=Path("/etc/tmpfiles.d/astack-memory-guard.conf"),
    )


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return None


def _min_ttl_ms_before_install(host: Host) -> int | None:
    text = _read(host.boot_file)
    if text is None:
        return None
    match = re.search("^{}(\\d+)$".format(re.escape(RECORD)), text, re.MULTILINE)
    if match is None:
        raise GuardError("{} has no line that starts with '{}'".format(host.boot_file, RECORD))
    return int(match.group(1))


def status(host: Host) -> Status:
    enabled = _read(host.lru_gen_enabled_file)
    ttl = _read(host.min_ttl_ms_file)
    return Status(
        lru_gen_on=enabled is not None and int(enabled, 16) & 1 == 1,
        min_ttl_ms=None if ttl is None else int(ttl),
        min_ttl_ms_before_install=_min_ttl_ms_before_install(host),
    )


def _set_min_ttl_ms(host: Host, value: int, *, dry_run: bool, say: Say) -> None:
    say("SET {} {}".format(host.min_ttl_ms_file, value))
    if not dry_run:
        host.min_ttl_ms_file.write_text("{}\n".format(value))


def install(host: Host, *, dry_run: bool, say: Say) -> None:
    current = status(host)
    if not current.available:
        raise GuardError("the kernel's multi-gen LRU is missing or off, so install changed nothing")
    before = current.min_ttl_ms if current.min_ttl_ms_before_install is None else current.min_ttl_ms_before_install
    say("WRITE {}".format(host.boot_file))
    if not dry_run:
        files.atomic_write(host.boot_file, (
            "# Written by astack scripts/memory_guard.py. Its uninstall restores min_ttl_ms and deletes this file.\n"
            "{}{}\n"
            "w {} - - - - {}\n"
        ).format(RECORD, before, host.min_ttl_ms_file, MIN_TTL_MS).encode())
    _set_min_ttl_ms(host, MIN_TTL_MS, dry_run=dry_run, say=say)


def uninstall(host: Host, *, dry_run: bool, say: Say) -> None:
    current = status(host)
    if current.min_ttl_ms_before_install is None:
        say("Nothing to uninstall: {} does not exist".format(host.boot_file))
        return
    if current.min_ttl_ms is not None:
        _set_min_ttl_ms(host, current.min_ttl_ms_before_install, dry_run=dry_run, say=say)
    say("REMOVE {}".format(host.boot_file))
    if not dry_run:
        host.boot_file.unlink()


def describe(current: Status) -> str:
    if current.available:
        text = "{}: min_ttl_ms {}".format("on" if current.min_ttl_ms else "off", current.min_ttl_ms)
    else:
        text = "unavailable: the kernel's multi-gen LRU is missing or off"
    if current.min_ttl_ms_before_install is not None:
        text += ", set to {} at boot by astack".format(MIN_TTL_MS)
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
            print(describe(status(host)))
        else:
            (install if args.command == "install" else uninstall)(host, dry_run=args.dry_run, say=print)
    except (OSError, GuardError) as error:
        print("memory_guard: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
