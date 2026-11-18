#!/usr/bin/env python3
"""Keep a Linux machine responsive when its memory runs out.

install turns on the kernel's thrashing prevention and moves /tmp from RAM to
disk at the next reboot. uninstall reverts both. status reports them without
root. The script needs systemd.
"""

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Literal, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import astack_files as files

MIN_TTL_MS = 1000
FSTAB_MARKER = "# astack memory-guard: "
RECORD_MARKER = "# astack memory-guard record: "
COMMENT_OUT_YOURSELF = "Comment out that line yourself, then run install again."

Place = Literal["RAM", "disk"]
TmpMount = Literal["masked", "from fstab", "admin unit file", "unit file", "none"]
Run = Callable[[Sequence[str]], subprocess.CompletedProcess]
Say = Callable[[str], None]
Step = tuple[str, Callable[[], object]]


class GuardError(Exception):
    pass


@dataclass(frozen=True)
class Host:
    lru_gen_enabled_file: Path
    min_ttl_ms_file: Path
    record_file: Path
    fstab_file: Path
    mounts_file: Path
    run: Run


@dataclass(frozen=True)
class Record:
    min_ttl_ms_before: int | None
    masked_tmp_mount: bool


@dataclass(frozen=True)
class Status:
    min_ttl_ms: int | None
    record: Record | None
    tmp_now: Place
    fstab_mounts_tmpfs: bool
    tmp_mount: TmpMount

    @property
    def recorded(self) -> Record:
        return self.record or Record(None, False)

    @property
    def tmp_at_boot(self) -> Place:
        boots_tmpfs = self.tmp_mount not in ("masked", "none")
        return "RAM" if self.fstab_mounts_tmpfs or boots_tmpfs else "disk"


def real_host() -> Host:
    return Host(
        lru_gen_enabled_file=Path("/sys/kernel/mm/lru_gen/enabled"),
        min_ttl_ms_file=Path("/sys/kernel/mm/lru_gen/min_ttl_ms"),
        record_file=Path("/etc/tmpfiles.d/astack-memory-guard.conf"),
        fstab_file=Path("/etc/fstab"),
        mounts_file=Path("/proc/self/mounts"),
        run=lambda argv: subprocess.run(argv, capture_output=True, text=True),
    )


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return None


def _mounts_tmpfs_on_tmp(line: str) -> bool:
    fields = line.split()
    if len(fields) < 3 or fields[0].startswith("#") or fields[2] != "tmpfs":
        return False
    return posixpath.normpath(re.sub("/+", "/", fields[1])) == "/tmp"


def _disabled_by_us(line: str) -> bool:
    return line.startswith(FSTAB_MARKER) and _mounts_tmpfs_on_tmp(line[len(FSTAB_MARKER):])


def _systemctl(host: Host, *arguments: str) -> str:
    result = host.run(["systemctl", *arguments])
    if result.returncode != 0:
        raise GuardError("systemctl {} failed: {}".format(" ".join(arguments), result.stderr.strip()))
    return result.stdout


def _tmp_mount(host: Host) -> TmpMount:
    shown = _systemctl(host, "show", "tmp.mount", "--property=LoadState,Type,FragmentPath,SourcePath")
    unit = dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)
    if unit.get("LoadState") == "masked":
        return "masked"
    if unit.get("LoadState") != "loaded" or unit.get("Type") != "tmpfs":
        return "none"
    if unit.get("SourcePath") == str(host.fstab_file):
        return "from fstab"
    fragment = unit.get("FragmentPath", "")
    if fragment.startswith("/etc/"):
        return "admin unit file"
    return "unit file" if fragment else "none"


def _read_record(host: Host) -> Record | None:
    text = _read(host.record_file)
    if text is None:
        return None
    for line in text.splitlines():
        if line.startswith(RECORD_MARKER):
            try:
                return Record(**json.loads(line[len(RECORD_MARKER):]))
            except (TypeError, ValueError) as error:
                raise GuardError("{} has an unreadable record: {}".format(host.record_file, error))
    raise GuardError("{} has no astack memory-guard record".format(host.record_file))


def status(host: Host) -> Status:
    enabled = _read(host.lru_gen_enabled_file)
    ttl = _read(host.min_ttl_ms_file)
    available = enabled is not None and ttl is not None and int(enabled, 16) & 1 == 1

    tmp_now: Place = "disk"
    for line in host.mounts_file.read_text().splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[1] == "/tmp":
            tmp_now = "RAM" if fields[2] == "tmpfs" else "disk"
    return Status(
        min_ttl_ms=int(ttl) if available else None,
        record=_read_record(host),
        tmp_now=tmp_now,
        fstab_mounts_tmpfs=any(_mounts_tmpfs_on_tmp(line) for line in host.fstab_file.read_text().splitlines()),
        tmp_mount=_tmp_mount(host),
    )


def _checked_fstab(host: Host, edit: Callable[[str], str]) -> str | None:
    original = host.fstab_file.read_text()
    text = "".join(edit(line) for line in original.splitlines(keepends=True))
    if text == original:
        return None
    with tempfile.NamedTemporaryFile("w", prefix="astack-fstab-") as candidate:
        candidate.write(text)
        candidate.flush()
        check = host.run(["findmnt", "--verify", "--tab-file", candidate.name])
    if check.returncode != 0:
        raise GuardError("findmnt --verify rejected the edited {}, so nothing changed:\n{}{}".format(
            host.fstab_file, check.stdout, check.stderr))
    return text


def _fstab_step(host: Host, text: str, change: str) -> Step:
    def replace() -> None:
        files.atomic_write(host.fstab_file, text.encode(), host.fstab_file.stat().st_mode & 0o7777)
        _systemctl(host, "daemon-reload")

    return "EDIT {}: {}".format(host.fstab_file, change), replace


def _ttl_step(host: Host, value: int) -> Step:
    return "SET {} {}".format(host.min_ttl_ms_file, value), lambda: host.min_ttl_ms_file.write_text("{}\n".format(value))


def _run_steps(steps: list[Step], *, dry_run: bool, say: Say) -> None:
    for message, action in steps:
        say(message)
        if not dry_run:
            action()


def install(host: Host, *, dry_run: bool, say: Say) -> None:
    current = status(host)
    if current.tmp_mount == "admin unit file":
        raise GuardError("a unit file in /etc/systemd/system defines tmp.mount, so systemctl cannot mask it. "
                         "Remove that file or mask tmp.mount yourself, then run install again.")
    fstab_text = _checked_fstab(host, lambda line: FSTAB_MARKER + line if _mounts_tmpfs_on_tmp(line) else line)
    if current.tmp_mount == "from fstab" and fstab_text is None:
        raise GuardError("{} mounts /tmp in a line this script does not recognize. {}".format(
            host.fstab_file, COMMENT_OUT_YOURSELF))
    before = current.recorded
    ttl_before = current.min_ttl_ms if before.min_ttl_ms_before is None else before.min_ttl_ms_before
    may_mask = (fstab_text is not None and current.tmp_mount != "masked") or current.tmp_mount == "unit file"
    backup = host.fstab_file.with_name(host.fstab_file.name + ".astack-backup")

    def write_record(masked: bool) -> None:
        text = "# Written by astack scripts/memory_guard.py. Its uninstall reverts what this file records.\n"
        text += RECORD_MARKER + json.dumps(asdict(Record(ttl_before, before.masked_tmp_mount or masked))) + "\n"
        if current.min_ttl_ms is not None:
            text += "w {} - - - - {}\n".format(host.min_ttl_ms_file, MIN_TTL_MS)
        files.atomic_write(host.record_file, text.encode())

    def mask() -> None:
        unit = _tmp_mount(host)
        if unit == "unit file":
            _systemctl(host, "mask", "tmp.mount")
            write_record(True)
        elif unit == "from fstab":
            raise GuardError("{} still mounts /tmp after the edit, so tmp.mount stays unmasked. {}".format(
                host.fstab_file, COMMENT_OUT_YOURSELF))

    steps: list[Step] = [("WRITE {}".format(host.record_file), lambda: write_record(False))]
    if current.min_ttl_ms is None:
        say("SKIP thrashing prevention: the kernel's multi-gen LRU is missing or off")
    else:
        steps.append(_ttl_step(host, MIN_TTL_MS))
    if fstab_text is not None:
        steps.append(("COPY {} to {}".format(host.fstab_file, backup), lambda: shutil.copy2(host.fstab_file, backup)))
        steps.append(_fstab_step(host, fstab_text, "comment out its tmpfs /tmp line"))
    if may_mask:
        steps.append(("MASK tmp.mount if a unit file would mount a tmpfs on /tmp at boot", mask))
    _run_steps(steps, dry_run=dry_run, say=say)
    if current.tmp_now == "RAM":
        say("Reboot to move /tmp to disk.")


def uninstall(host: Host, *, dry_run: bool, say: Say) -> None:
    current = status(host)
    record = current.recorded
    fstab_text = _checked_fstab(host, lambda line: line[len(FSTAB_MARKER):] if _disabled_by_us(line) else line)
    unmask = current.tmp_mount == "masked" and (fstab_text is not None or record.masked_tmp_mount)
    previous_ttl = record.min_ttl_ms_before

    steps: list[Step] = []
    if unmask:
        steps.append(("UNMASK tmp.mount", lambda: _systemctl(host, "unmask", "tmp.mount")))
    if fstab_text is not None:
        steps.append(_fstab_step(host, fstab_text, "restore its tmpfs /tmp line"))
    if previous_ttl is not None and current.min_ttl_ms is not None:
        steps.append(_ttl_step(host, previous_ttl))
    if current.record is not None:
        steps.append(("REMOVE {}".format(host.record_file), host.record_file.unlink))
    _run_steps(steps, dry_run=dry_run, say=say)
    if (unmask or fstab_text is not None) and current.tmp_now == "disk":
        say("Reboot to move /tmp back to RAM.")


def describe(current: Status) -> str:
    if current.min_ttl_ms is None:
        prevention = "unavailable: the kernel's multi-gen LRU is missing or off"
    else:
        prevention = "{}: min_ttl_ms {}".format("on" if current.min_ttl_ms else "off", current.min_ttl_ms)
        if current.record is not None and current.record.min_ttl_ms_before is not None:
            prevention += ", set at boot by astack"
    tmp = current.tmp_now
    if current.tmp_at_boot != current.tmp_now:
        tmp += " now, {} after the next reboot".format(current.tmp_at_boot)
    lines = ["thrashing prevention  " + prevention, "/tmp                  " + tmp]
    if current.tmp_mount == "masked" and current.fstab_mounts_tmpfs:
        lines.append("warning               tmp.mount is masked, but /etc/fstab mounts /tmp, so boot can fail."
                     " Run install, or unmask tmp.mount.")
    return "\n".join(lines)


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
