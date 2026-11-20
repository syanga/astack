#!/usr/bin/env python3
"""Remove astack's memory guard from a Linux machine.

Earlier versions of this script set /sys/kernel/mm/lru_gen/min_ttl_ms to 1000, and
/etc/tmpfiles.d/astack-memory-guard.conf set it again at each boot. At that value, kswapd
OOM-kills processes one after another while its page generations turn over within a second,
even with gigabytes of memory free.

uninstall restores min_ttl_ms to its value from before the first install and deletes the boot file.
uninstall --dry-run needs no root.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
from typing import Sequence

BEFORE_INSTALL_PREFIX = "# min_ttl_ms before install: "


class GuardError(Exception):
    pass


@dataclass(frozen=True)
class Host:
    root: Path

    @property
    def min_ttl_ms_file(self) -> Path:
        return self.root / "sys/kernel/mm/lru_gen/min_ttl_ms"

    @property
    def boot_file(self) -> Path:
        return self.root / "etc/tmpfiles.d/astack-memory-guard.conf"


def real_host() -> Host:
    return Host(Path("/"))


def installed(host: Host) -> bool:
    return host.boot_file.exists()


def _min_ttl_ms_before_install(host: Host) -> int:
    match = re.search("^{}(\\d+)$".format(re.escape(BEFORE_INSTALL_PREFIX)), host.boot_file.read_text(), re.MULTILINE)
    if match is None:
        raise GuardError("{} has no line that starts with '{}'".format(host.boot_file, BEFORE_INSTALL_PREFIX))
    return int(match.group(1))


def uninstall(host: Host, *, dry_run: bool) -> None:
    if not installed(host):
        print("Nothing to uninstall: {} does not exist".format(host.boot_file))
        return
    before = _min_ttl_ms_before_install(host)
    if host.min_ttl_ms_file.exists():
        print("SET {} {}".format(host.min_ttl_ms_file, before))
        if not dry_run:
            host.min_ttl_ms_file.write_text("{}\n".format(before))
    print("REMOVE {}".format(host.boot_file))
    if not dry_run:
        host.boot_file.unlink()


def main(argv: Sequence[str] | None = None, host: Host | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("uninstall",))
    parser.add_argument("--dry-run", action="store_true", help="show the changes without making them")
    args = parser.parse_args(argv)
    host = host or real_host()
    try:
        if installed(host) and not args.dry_run and os.geteuid() != 0:
            print("memory_guard: uninstall needs root; run it with sudo", file=sys.stderr)
            return 1
        uninstall(host, dry_run=args.dry_run)
    except (OSError, GuardError) as error:
        print("memory_guard: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
