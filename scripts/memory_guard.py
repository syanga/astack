#!/usr/bin/env python3
"""Keep a Linux machine responsive when its memory runs out.

install turns on the kernel's thrash guard and moves /tmp from RAM to disk at
the next reboot. uninstall reverts both. status reports them without root.
"""

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Literal, Optional, Sequence

# https://docs.kernel.org/admin-guide/mm/multigen_lru.html#thrashing-prevention:
# "N=1000 usually eliminates intolerable lags due to thrashing."
MIN_TTL_MS = 1000
FSTAB_MARKER = "# astack memory-guard: "
PREVIOUS = "# previous min_ttl_ms: "

Place = Literal["RAM", "disk"]
Run = Callable[[Sequence[str]], subprocess.CompletedProcess]
Say = Callable[[str], None]


@dataclass(frozen=True)
class Host:
    lru_gen: Path
    tmpfiles: Path
    fstab: Path
    mounts: Path
    tmp: Path
    run: Run


@dataclass(frozen=True)
class Guard:
    available: bool
    min_ttl_ms: Optional[int]
    at_boot: bool


@dataclass(frozen=True)
class Tmp:
    now: Place
    at_boot: Optional[Place]
    used: int


@dataclass(frozen=True)
class Status:
    guard: Guard
    tmp: Tmp


def real_host() -> Host:
    return Host(
        lru_gen=Path("/sys/kernel/mm/lru_gen"),
        tmpfiles=Path("/etc/tmpfiles.d/astack-memory-guard.conf"),
        fstab=Path("/etc/fstab"),
        mounts=Path("/proc/self/mounts"),
        tmp=Path("/tmp"),
        run=lambda argv: subprocess.run(argv, capture_output=True, text=True),
    )


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return None


def _mounts_tmp_on_tmpfs(line: str) -> bool:
    fields = line.split()
    return len(fields) >= 3 and not fields[0].startswith("#") and fields[1] == "/tmp" and fields[2] == "tmpfs"


def _disabled_by_us(line: str) -> bool:
    return line.startswith(FSTAB_MARKER) and _mounts_tmp_on_tmpfs(line[len(FSTAB_MARKER):])


def status(host: Host) -> Status:
    enabled = _read(host.lru_gen / "enabled")
    ttl = _read(host.lru_gen / "min_ttl_ms")
    available = enabled is not None and ttl is not None and int(enabled, 16) & 1 == 1
    guard = Guard(available, int(ttl) if available else None, host.tmpfiles.exists())

    now: Place = "disk"
    for line in host.mounts.read_text().splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[1] == "/tmp":
            now = "RAM" if fields[2] == "tmpfs" else "disk"
    lines = host.fstab.read_text().splitlines()
    at_boot: Optional[Place]
    if any(_mounts_tmp_on_tmpfs(line) for line in lines):
        at_boot = "RAM"
    elif now == "disk" or any(_disabled_by_us(line) for line in lines):
        at_boot = "disk"
    else:
        at_boot = None
    stats = os.statvfs(host.tmp)
    return Status(guard, Tmp(now, at_boot, (stats.f_blocks - stats.f_bfree) * stats.f_frsize))


def _previous_ttl(host: Host) -> int:
    for line in host.tmpfiles.read_text().splitlines():
        if line.startswith(PREVIOUS):
            return int(line[len(PREVIOUS):])
    return 0


def _replace(path: Path, text: str, verify: Callable[[str], None] = lambda candidate: None) -> None:
    descriptor, candidate = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(text)
        if path.exists():
            shutil.copymode(path, candidate)
        else:
            os.chmod(candidate, 0o644)
        verify(candidate)
        os.replace(candidate, path)
    except BaseException:
        os.unlink(candidate)
        raise


def _edit_fstab(host: Host, edit: Callable[[str], str], say: Say, dry_run: bool) -> None:
    say("EDIT {}".format(host.fstab))
    if dry_run:
        return

    def verify(candidate: str) -> None:
        check = host.run(["findmnt", "--verify", "--tab-file", candidate])
        if check.returncode != 0:
            raise ValueError("findmnt --verify rejected the edited {}, which is unchanged:\n{}{}".format(
                host.fstab, check.stdout, check.stderr))

    lines = host.fstab.read_text().splitlines(keepends=True)
    shutil.copy2(host.fstab, host.fstab.with_name(host.fstab.name + ".astack-backup"))
    _replace(host.fstab, "".join(edit(line) for line in lines), verify)
    host.run(["systemctl", "daemon-reload"])


def install(host: Host, *, dry_run: bool, say: Say) -> None:
    current = status(host)
    ttl_path = host.lru_gen / "min_ttl_ms"
    if current.guard.available:
        previous = _previous_ttl(host) if current.guard.at_boot else current.guard.min_ttl_ms
        say("WRITE {}".format(host.tmpfiles))
        say("SET {} {}".format(ttl_path, MIN_TTL_MS))
        if not dry_run:
            host.tmpfiles.parent.mkdir(parents=True, exist_ok=True)
            _replace(host.tmpfiles, "# Written by astack scripts/memory_guard.py. Its uninstall reverts this file.\n"
                     "{}{}\nw {} - - - - {}\n".format(PREVIOUS, previous, ttl_path, MIN_TTL_MS))
            ttl_path.write_text("{}\n".format(MIN_TTL_MS))
    else:
        say("SKIP thrash guard: the kernel's multi-gen LRU is missing or off")

    if current.tmp.at_boot == "RAM":
        _edit_fstab(host, lambda line: FSTAB_MARKER + line if _mounts_tmp_on_tmpfs(line) else line, say, dry_run)
        say("Reboot to move /tmp to disk.")
    elif current.tmp.now == "RAM" and current.tmp.at_boot == "disk":
        say("Reboot to move /tmp to disk.")
    elif current.tmp.at_boot is None:
        say("SKIP /tmp: its tmpfs mount does not come from {}".format(host.fstab))


def uninstall(host: Host, *, dry_run: bool, say: Say) -> None:
    current = status(host)
    if current.guard.at_boot:
        previous = _previous_ttl(host)
        say("REMOVE {}".format(host.tmpfiles))
        if current.guard.available:
            say("SET {} {}".format(host.lru_gen / "min_ttl_ms", previous))
        if not dry_run:
            host.tmpfiles.unlink()
            if current.guard.available:
                (host.lru_gen / "min_ttl_ms").write_text("{}\n".format(previous))

    if any(_disabled_by_us(line) for line in host.fstab.read_text().splitlines()):
        _edit_fstab(host, lambda line: line[len(FSTAB_MARKER):] if _disabled_by_us(line) else line, say, dry_run)
        say("Reboot to move /tmp back to RAM.")


def describe(current: Status) -> str:
    guard = current.guard
    if not guard.available:
        guard_text = "unavailable: the kernel's multi-gen LRU is missing or off"
    else:
        guard_text = "{}: min_ttl_ms {}".format("on" if guard.min_ttl_ms else "off", guard.min_ttl_ms)
        if guard.at_boot:
            guard_text += ", set at boot by astack"
    tmp = current.tmp
    tmp_text = "RAM, {:.1f} GiB used".format(tmp.used / 2**30) if tmp.now == "RAM" else "disk"
    if tmp.at_boot is None:
        tmp_text += ", mounted from outside /etc/fstab"
    elif tmp.at_boot != tmp.now:
        tmp_text += ", {} after the next reboot".format(tmp.at_boot)
    return "thrash guard  {}\n/tmp          {}".format(guard_text, tmp_text)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("status", "install", "uninstall"))
    parser.add_argument("--dry-run", action="store_true", help="show the changes without making them")
    args = parser.parse_args(argv)
    if sys.platform != "linux":
        print("memory_guard: nothing to do on {}; it changes only Linux machines".format(sys.platform))
        return 0
    host = real_host()
    if args.command == "status":
        print(describe(status(host)))
        return 0
    if not args.dry_run and os.geteuid() != 0:
        print("memory_guard: {} needs root; run it with sudo".format(args.command), file=sys.stderr)
        return 1
    try:
        (install if args.command == "install" else uninstall)(host, dry_run=args.dry_run, say=print)
    except (OSError, ValueError) as error:
        print("memory_guard: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
