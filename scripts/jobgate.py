#!/usr/bin/env python3
"""Admit heavy commands, such as test suites, builds, and renders, so that concurrent runs fit the machine.

    jobgate run [--max-workers N] PROFILE -- COMMAND [ARG...]
    jobgate status

See docs/jobgate.md in the astack repository.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import errno
from dataclasses import asdict, dataclass, fields, replace
import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import time
from typing import Callable, Literal, NamedTuple

sys.dont_write_bytecode = True
try:
    import tomllib
except ModuleNotFoundError:
    from _vendor import tomllib

DEFAULT_MEMORY_SHARE = 0.5
RECORD_SUFFIX = ".json"
SCOPE_SUFFIX = ".scope"
PSI_HOLD_PERCENT = 10.0
DARWIN_LEVELS = {1: "normal", 2: "warn", 4: "critical"}
DARWIN_HOLD_LEVEL = 4
UNIT_PREFIX = "jobgate"
SLICE = UNIT_PREFIX + ".slice"
CPU_WEIGHT = 30
OOM_POLICY_MIN_SYSTEMD = 253
# systemd-run gained --expand-environment in 254. Before 254 it never expands $VAR in a scope's command;
# 254 to 257 do not expand by default but warn on a "$", and 258 and later expand by default.
EXPAND_ENVIRONMENT_MIN_SYSTEMD = 254
POLL_SECONDS = 1.0
SAMPLE_SECONDS = 2.0
REPORT_SECONDS = 60.0
RUN_ENV = "JOBGATE_RUN"
WORKERS_ENV = "JOBGATE_WORKERS"
MACHINE_ENV = "JOBGATE_MACHINE_FILE"
HOME_ENV = "JOBGATE_HOME"
NO_MEMORY_CONTROLLER = 3
REFUSED = 125
CANNOT_EXECUTE = 126
NOT_FOUND = 127
PROFILE_KEYS = ("base_mem", "worker_mem", "max_workers", "workers_env")
MACHINE_KEYS = ("memory_share",)
SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*([KMGT])(?:i?B)?", re.IGNORECASE)
UNITS = {"K": 2**10, "M": 2**20, "G": 2**30, "T": 2**40}
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
TASKPOLICY = "/usr/sbin/taskpolicy"
RUSAGE_INFO_V0 = 0

Dirty = Literal["yes", "no", "unknown"]


class Refusal(Exception):
    pass


class Stopped(BaseException):
    def __init__(self, number: int) -> None:
        super().__init__(number)
        self.number = number


def stop(number, frame):
    raise Stopped(number)


def say(text: str) -> None:
    try:
        print("jobgate: " + text, file=sys.stderr, flush=True)
    except OSError:
        pass


def mebibytes(count: int) -> str:
    return "{}M".format(math.ceil(count / 2**20))


def size(count: int) -> str:
    return "{:.1f}G".format(count / 2**30) if count >= 2**30 else mebibytes(count)


def duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return "{}s".format(seconds)
    if seconds < 3600:
        return "{}m {:02d}s".format(seconds // 60, seconds % 60)
    return "{}h {:02d}m".format(seconds // 3600, seconds % 3600 // 60)


def plural(count: int, noun: str) -> str:
    return "{} {}{}".format(count, noun, "" if count == 1 else "s")


@dataclass(frozen=True)
class Profile:
    name: str
    base_mem: int
    worker_mem: int
    max_workers: int
    workers_env: str

    def memory(self, workers: int) -> int:
        return self.base_mem + workers * self.worker_mem


@dataclass(frozen=True)
class Machine:
    memory: int
    cores: int
    memory_share: float

    @property
    def share(self) -> int:
        return int(self.memory * self.memory_share)


@dataclass(frozen=True)
class Pressure:
    reading: str
    holding: bool


@dataclass(frozen=True)
class Admission:
    workers: int
    memory: int
    started: float


@dataclass
class Run:
    id: str
    seq: int
    pid: int
    profile: str
    repo: str
    command: str
    arrived: float
    admission: Admission | None = None
    wait_reason: str | None = None


def parse_size(value: object, where: str) -> int:
    match = SIZE.fullmatch(value.strip()) if isinstance(value, str) else None
    if match is None:
        raise Refusal('{} must be a size such as "512M" or "2G", not {!r}'.format(where, value))
    return math.ceil(float(match.group(1)) * UNITS[match.group(2).upper()])


def read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as stream:
            return tomllib.load(stream)
    except (OSError, ValueError) as error:
        raise Refusal("cannot read {}: {}".format(path, error)) from None


def run_captured(argv: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, errors="surrogateescape", timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", "{} timed out after {:g}s".format(argv[0], timeout))
    except OSError as error:
        return subprocess.CompletedProcess(argv, 127, "", "cannot run {}: {}".format(argv[0], error.strerror or error))


def last_error(result: subprocess.CompletedProcess) -> str:
    return (result.stderr.strip().splitlines() or ["{} exited with {}".format(result.args[0], result.returncode)])[-1]


def find_config(start: Path) -> Path:
    top = run_captured(["git", "-C", str(start), "rev-parse", "--show-toplevel"])
    root = Path(top.stdout.removesuffix("\n")) if top.returncode == 0 else start
    candidate = root / "jobgate.toml"
    if not candidate.is_file():
        raise Refusal("no jobgate.toml in {}".format(root))
    return candidate


def load_profile(path: Path, name: str) -> Profile:
    data = read_toml(path)
    table = data.get(name)
    if not isinstance(table, dict):
        names = ", ".join(key for key, value in data.items() if isinstance(value, dict)) or "none"
        raise Refusal("{} has no [{}] profile; its profiles: {}".format(path, name, names))
    unknown = sorted(set(table) - set(PROFILE_KEYS))
    missing = [key for key in PROFILE_KEYS if key not in table]
    if unknown or missing:
        problems = (["unknown keys: " + ", ".join(unknown)] if unknown else []) + (
            ["missing keys: " + ", ".join(missing)] if missing else [])
        raise Refusal("[{}] in {} has {}".format(name, path, "; ".join(problems)))
    def where(key: str) -> str:
        return "[{}] {} in {}".format(name, key, path)

    max_workers = table["max_workers"]
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
        raise Refusal("{} must be a whole number of at least 1, not {!r}".format(where("max_workers"), max_workers))
    workers_env = table["workers_env"]
    if not isinstance(workers_env, str) or not ENV_NAME.fullmatch(workers_env):
        raise Refusal("{} must be an environment variable name, not {!r}".format(where("workers_env"), workers_env))
    if workers_env in (RUN_ENV, WORKERS_ENV):
        raise Refusal("{} cannot be {}, which jobgate sets itself".format(where("workers_env"), workers_env))
    return Profile(name, parse_size(table["base_mem"], where("base_mem")),
                   parse_size(table["worker_mem"], where("worker_mem")), max_workers, workers_env)


def account_home() -> Path:
    if os.environ.get(HOME_ENV):
        return Path(os.environ[HOME_ENV])
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        return Path.home()


def machine_config() -> Path:
    return account_home() / ".config/jobgate.toml"


def load_memory_share() -> float:
    path = machine_config()
    if not path.is_file():
        return DEFAULT_MEMORY_SHARE
    data = read_toml(path)
    unknown = sorted(set(data) - set(MACHINE_KEYS))
    if unknown:
        raise Refusal("{} has unknown keys: {}".format(path, ", ".join(unknown)))
    memory_share = data.get("memory_share", DEFAULT_MEMORY_SHARE)
    if isinstance(memory_share, bool) or not isinstance(memory_share, (int, float)) or not 0 < memory_share <= 1:
        raise Refusal("memory_share in {} must be a number above 0 and at most 1, not {!r}".format(path, memory_share))
    return float(memory_share)


def test_readings() -> dict:
    path = os.environ.get(MACHINE_ENV)
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise Refusal("cannot read {} {}: {}".format(MACHINE_ENV, path, error)) from None


def machine() -> Machine:
    readings = test_readings()
    memory = readings.get("memory") or os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    if "cores" in readings:
        cores = readings["cores"]
    elif hasattr(os, "sched_getaffinity"):
        cores = len(os.sched_getaffinity(0))
    else:
        cores = os.cpu_count() or 1
    return Machine(int(memory), int(cores), load_memory_share())


class LiveShare:
    def __init__(self, host: Machine) -> None:
        self.host = host
        self.warned = False
        self.warning: str | None = None

    def read(self) -> Machine:
        try:
            self.host = replace(self.host, memory_share=load_memory_share())
        except Refusal as refusal:
            if not self.warned:
                self.warning = "{}; keeping memory_share {:g} until the file is valid".format(
                    refusal, self.host.memory_share)
            self.warned = True
        else:
            self.warned = False
        return self.host

    def print_warning(self) -> None:
        if self.warning is not None:
            say(self.warning)
            self.warning = None


def refuse_if_never_fits(profile: Profile, host: Machine, config: Path) -> None:
    if profile.memory(1) > host.share:
        raise Refusal("[{}] needs {} for 1 worker, more than this machine's {} share; lower its base_mem or "
                      "worker_mem in {}, or raise memory_share in {}".format(
                          profile.name, size(profile.memory(1)), size(host.share), config, machine_config()))


def psi_pressure(text: str) -> Pressure:
    match = re.search(r"^full avg10=(\d+(?:\.\d+)?)", text, re.MULTILINE)
    if match is None:
        return Pressure("unknown (no full avg10 in the memory pressure reading)", False)
    percent = float(match.group(1))
    return Pressure("full avg10={:.2f}% (holds at {:g}%)".format(percent, PSI_HOLD_PERCENT), percent >= PSI_HOLD_PERCENT)


def darwin_pressure(value: str) -> Pressure:
    level = int(value) if value.strip().isdigit() else None
    name = DARWIN_LEVELS.get(level, "unknown") if level is not None else "unknown"
    return Pressure("kern.memorystatus_vm_pressure_level={} ({}; holds at {}, {})".format(
        value.strip() or "?", name, DARWIN_HOLD_LEVEL, DARWIN_LEVELS[DARWIN_HOLD_LEVEL]), level == DARWIN_HOLD_LEVEL)


def pressure() -> Pressure:
    readings = test_readings()
    if "psi" in readings:
        return psi_pressure(readings["psi"])
    if "darwin_level" in readings:
        return darwin_pressure(str(readings["darwin_level"]))
    if sys.platform == "darwin":
        result = run_captured(["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"], 10)
        if result.returncode != 0:
            return Pressure("unknown ({})".format(last_error(result)), False)
        return darwin_pressure(result.stdout)
    try:
        return psi_pressure(Path("/proc/pressure/memory").read_text())
    except OSError as error:
        return Pressure("unknown ({})".format(error.strerror or error), False)


def meminfo_available(text: str) -> int | None:
    match = re.search(r"^MemAvailable:\s+(\d+) kB", text, re.MULTILINE)
    return int(match.group(1)) * 1024 if match else None


def vm_stat_available(text: str) -> int | None:
    page = re.search(r"page size of (\d+) bytes", text)
    counts = {name: re.search(r"^{}:\s+(\d+)\.".format(re.escape(name)), text, re.MULTILINE)
              for name in ("Pages free", "File-backed pages", "Pages purgeable")}
    if page is None or None in counts.values():
        return None
    return int(page.group(1)) * sum(int(match.group(1)) for match in counts.values())


def available_memory() -> int | None:
    readings = test_readings()
    if "meminfo" in readings:
        return meminfo_available(readings["meminfo"])
    if "vm_stat" in readings:
        return vm_stat_available(readings["vm_stat"])
    if sys.platform == "darwin":
        result = run_captured(["/usr/bin/vm_stat"], 10)
        return vm_stat_available(result.stdout) if result.returncode == 0 else None
    try:
        return meminfo_available(Path("/proc/meminfo").read_text())
    except OSError:
        return None


class Load(NamedTuple):
    stated: int
    workers: int
    available: int | None
    still_to_use: int
    in_use: dict[str, int | None]

    def share_left(self, machine: Machine) -> int:
        return machine.share - self.stated

    def free_memory_left(self) -> int | None:
        return None if self.available is None else self.available - self.still_to_use


def smaller(first: int | None, second: int | None) -> int | None:
    return None if first is None or second is None else min(first, second)


def load_of(queue: Queue, runs: list[Run]) -> Load:
    running = [run for run in runs if run.admission is not None]
    table = parents() if running else {}
    before = {run.id: memory_in_use(queue, run, table) for run in running}
    available = available_memory()
    in_use = {run.id: smaller(before[run.id], memory_in_use(queue, run, table)) for run in running}
    return Load(sum(run.admission.memory for run in running), sum(run.admission.workers for run in running), available,
                sum(max(0, run.admission.memory - (in_use[run.id] or 0)) for run in running), in_use)


def workers_within(memory: int | None, profile: Profile, limit: int) -> int:
    if memory is None:
        return limit
    if memory < profile.base_mem:
        return 0
    return limit if profile.worker_mem == 0 else (memory - profile.base_mem) // profile.worker_mem


def fit(machine: Machine, profile: Profile, limit: int, load: Load) -> int:
    return max(0, min(limit, machine.cores - load.workers, workers_within(load.share_left(machine), profile, limit),
                      workers_within(load.free_memory_left(), profile, limit)))


def shortage(machine: Machine, profile: Profile, load: Load) -> str:
    if load.workers >= machine.cores:
        return "for a core: gated workers use all {} cores".format(machine.cores)
    if profile.memory(1) > load.share_left(machine):
        return "for memory: 1 worker needs {}, and {} of the {} share is free".format(
            size(profile.memory(1)), size(max(0, load.share_left(machine))), size(machine.share))
    return "for free memory: 1 worker needs {}, and the machine has {} available, of which running runs may still " \
           "use {}".format(size(profile.memory(1)), size(load.available or 0), size(load.still_to_use))


def temporary(path: Path) -> Path:
    return path.with_name(path.name + ".tmp")


class RunFiles(NamedTuple):
    record: Path
    scope: Path

    @classmethod
    def of(cls, runs: Path, run_id: str) -> RunFiles:
        return cls(runs / (run_id + RECORD_SUFFIX), runs / (run_id + SCOPE_SUFFIX))

    def remove(self) -> None:
        for path in (self.record, self.scope, temporary(self.scope)):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()


class Queue:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.runs = directory / "runs"
        self.results = directory / "results.log"
        self.lock_file = directory / "queue.lock"

    @classmethod
    def open(cls) -> Queue:
        queue = cls(account_home() / ".cache/jobgate")
        try:
            queue.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            queue.runs.mkdir(mode=0o700, exist_ok=True)
            os.close(os.open(str(queue.lock_file), os.O_RDWR | os.O_CREAT, 0o600))
        except OSError as error:
            raise Refusal("cannot create {}: {}; jobgate keeps its queue in {}".format(
                error.filename or queue.directory, error.strerror or error, queue.directory)) from None
        return queue

    @contextlib.contextmanager
    def lock(self):
        fd = os.open(str(self.lock_file), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def live(self) -> list[Run]:
        runs = []
        for path in self.runs.glob("*" + RECORD_SUFFIX):
            try:
                fd = os.open(str(path), os.O_RDONLY)
            except FileNotFoundError:
                continue
            try:
                if locked_by_another(fd):
                    run = read_run(fd)
                    if run is not None:
                        runs.append(run)
                else:
                    RunFiles.of(self.runs, path.stem).remove()
            finally:
                os.close(fd)
        for path in self.runs.iterdir():
            files = RunFiles.of(self.runs, path.name.split(".", 1)[0])
            if not files.record.exists():
                files.remove()
        return sorted(runs, key=lambda run: run.seq)

    def enter(self, profile: str, repo: Path, command: list[str]) -> Ticket:
        with self.lock():
            seq = max((run.seq for run in self.live()), default=0) + 1
            text = shlex.join(command)
            run = Run(id="{}-{}".format(seq, secrets.token_hex(4)), seq=seq, pid=os.getpid(), profile=profile,
                      repo=str(repo), command=text if len(text) <= 160 else text[:157] + "...", arrived=time.time())
            fd = os.open(str(RunFiles.of(self.runs, run.id).record), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            ticket = Ticket(self, run, fd)
            ticket.save()
            return ticket


def locked_by_another(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    return False


def run_is_live(run_file: str) -> bool:
    if not os.path.isabs(run_file):
        return False
    try:
        fd = os.open(run_file, os.O_RDONLY)
    except OSError:
        return False
    try:
        return locked_by_another(fd)
    finally:
        os.close(fd)


def read_run(fd: int) -> Run | None:
    chunks = []
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    try:
        data = json.loads(b"".join(chunks))
        run = Run(**{field.name: data[field.name] for field in fields(Run) if field.name in data})
        if run.admission is not None:
            run.admission = Admission(**{field.name: run.admission[field.name] for field in fields(Admission)})
        return run
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


class Ticket:
    def __init__(self, queue: Queue, run: Run, fd: int) -> None:
        self.queue = queue
        self.run = run
        self.fd = fd

    @property
    def files(self) -> RunFiles:
        return RunFiles.of(self.queue.runs, self.run.id)

    def admit(self, admission: Admission) -> None:
        self.run.admission = admission
        self.run.wait_reason = None
        self.save()

    def record_wait_reason(self, reason: str) -> None:
        if reason != self.run.wait_reason:
            self.run.wait_reason = reason
            self.save()

    def save(self) -> None:
        os.ftruncate(self.fd, 0)
        os.pwrite(self.fd, json.dumps(asdict(self.run)).encode(), 0)

    def leave(self) -> None:
        with self.queue.lock():
            self.files.remove()
        os.close(self.fd)


def wait_for_admission(queue: Queue, ticket: Ticket, share: LiveShare, profile: Profile, limit: int,
                       config: Path) -> tuple[Admission, float]:
    arrived = time.monotonic()
    next_report = arrived
    while True:
        try:
            with queue.lock():
                machine = share.read()
                refuse_if_never_fits(profile, machine, config)
                runs = queue.live()
                ahead = [run for run in runs if run.admission is None and run.seq < ticket.run.seq]
                if ahead:
                    reason = "behind {}".format(plural(len(ahead), "earlier run"))
                else:
                    level = pressure()
                    load = load_of(queue, runs)
                    workers = fit(machine, profile, limit, load)
                    if level.holding:
                        reason = "for memory pressure to ease: " + level.reading
                    elif workers < 1:
                        reason = shortage(machine, profile, load)
                    else:
                        ticket.admit(Admission(workers, profile.memory(workers), time.time()))
                        return ticket.run.admission, time.monotonic() - arrived
                ticket.record_wait_reason(reason)
        finally:
            share.print_warning()
        now = time.monotonic()
        if now >= next_report:
            say("{} waits {}; waited {}".format(profile.name, reason, duration(now - arrived)))
            next_report = now + REPORT_SECONDS
        time.sleep(POLL_SECONDS)


@dataclass(frozen=True)
class Exit:
    code: int
    signal: int | None = None


class Measured(NamedTuple):
    exit: Exit
    peak: int | None = None
    oom_kills: int | None = None


def exit_code(returncode: int) -> int:
    return 128 - returncode if returncode < 0 else returncode


def supervise(command: list[str], env: dict | None, sample: Callable[[int], None] | None = None) -> Exit:
    """The terminal sends Ctrl-C to the whole foreground process group, so the command already gets SIGINT.

    exec resets a Python handler to the default but keeps SIG_IGN, so a signal that nohup ignores stays ignored.
    """
    child: list[subprocess.Popen] = []
    received: list[int] = []

    def forward(number, frame):
        received.append(number)
        if child:
            with contextlib.suppress(ProcessLookupError):
                child[0].send_signal(number)

    def record(number, frame):
        received.append(number)

    handlers = {signal.SIGTERM: forward, signal.SIGHUP: forward, signal.SIGINT: record}
    previous = {number: signal.signal(number, handler) for number, handler in handlers.items()
                if signal.getsignal(number) is not signal.SIG_IGN}
    try:
        try:
            child.append(subprocess.Popen(command, env=env))
        except OSError as error:
            return Exit(launch_failure(command, error))
        for number in received:
            with contextlib.suppress(ProcessLookupError):
                child[0].send_signal(number)
        while True:
            if sample is not None:
                with contextlib.suppress(OSError, ValueError, AttributeError, subprocess.SubprocessError):
                    sample(child[0].pid)
            try:
                return Exit(exit_code(child[0].wait(timeout=SAMPLE_SECONDS)), received[0] if received else None)
            except subprocess.TimeoutExpired:
                continue
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def parents() -> dict[int, int]:
    table = {}
    if sys.platform == "linux":
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                with open("/proc/{}/stat".format(entry.name), "rb") as stream:
                    table[int(entry.name)] = int(stream.read().rpartition(b")")[2].split()[1])
            except (OSError, ValueError, IndexError):
                continue
        return table
    for line in run_captured(["ps", "-axo", "pid=,ppid="], 10).stdout.splitlines():
        columns = line.split()
        if len(columns) == 2 and all(column.isdigit() for column in columns):
            table[int(columns[0])] = int(columns[1])
    return table


def process_tree(root: int, table: dict[int, int]) -> list[int]:
    children: dict[int, list[int]] = {}
    for pid, parent in table.items():
        children.setdefault(parent, []).append(pid)
    tree: list[int] = []
    seen: set[int] = set()
    pending = [root]
    while pending:
        pid = pending.pop()
        if pid in table and pid not in seen:
            seen.add(pid)
            tree.append(pid)
            pending.extend(children.get(pid, ()))
    return tree


def resident_bytes(pid: int) -> int | None:
    try:
        with open("/proc/{}/statm".format(pid), "rb") as stream:
            return int(stream.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


class RUsageInfoV0(ctypes.Structure):
    """struct rusage_info_v0 from macOS <sys/resource.h>."""

    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [(name, ctypes.c_uint64) for name in (
        "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups", "ri_pageins",
        "ri_wired_size", "ri_resident_size", "ri_phys_footprint", "ri_proc_start_abstime", "ri_proc_exit_abstime")]


class Libproc:
    library: ctypes.CDLL | None = None

    @classmethod
    def phys_footprint(cls, pid: int) -> int | None:
        if cls.library is None:
            cls.library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            cls.library.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
            cls.library.proc_pid_rusage.restype = ctypes.c_int
        info = RUsageInfoV0()
        if cls.library.proc_pid_rusage(pid, RUSAGE_INFO_V0, ctypes.byref(info)) != 0:
            return None
        return info.ri_phys_footprint


class PeakSampler:
    def __init__(self) -> None:
        self.peak = 0
        self.measure = resident_bytes if sys.platform == "linux" else Libproc.phys_footprint

    def sample(self, root: int) -> None:
        self.peak = max(self.peak, tree_total(root, parents(), self.measure))


def tree_total(root: int, table: dict[int, int], measure: Callable[[int], int | None]) -> int:
    sizes = (measure(pid) for pid in process_tree(root, table))
    return sum(size for size in sizes if size is not None)


def proc_bytes(pid: int, name: str, keys: tuple[bytes, ...]) -> int | None:
    try:
        with open("/proc/{}/{}".format(pid, name), "rb") as stream:
            values = dict(line.split(b":", 1) for line in stream if b":" in line)
        return sum(int(values[key].split()[0]) * 1024 for key in keys)
    except (OSError, ValueError, KeyError, IndexError):
        return None


def owned_bytes(pid: int) -> int | None:
    proportional = proc_bytes(pid, "smaps_rollup", (b"Pss_Anon", b"Pss_Shmem"))
    return proportional if proportional is not None else proc_bytes(pid, "status", (b"RssAnon", b"RssShmem"))


def cgroup_memory_in_use(cgroup: Path) -> int:
    stat = dict(line.split() for line in (cgroup / "memory.stat").read_text().splitlines() if len(line.split()) == 2)
    cache = int(stat.get("active_file", 0)) + int(stat.get("inactive_file", 0))
    return max(0, int((cgroup / "memory.current").read_text()) - cache)


def memory_in_use(queue: Queue, run: Run, table: dict[int, int]) -> int | None:
    cgroup = read_json(RunFiles.of(queue.runs, run.id).scope).get("cgroup")
    measure = owned_bytes if sys.platform == "linux" else Libproc.phys_footprint
    try:
        if cgroup:
            return cgroup_memory_in_use(Path(cgroup))
        return tree_total(run.pid, table, measure)
    except (OSError, ValueError, AttributeError):
        return None


def exec_failure(command: list[str], env: dict) -> int | None:
    name = command[0]
    if "/" in name:
        candidates = [name]
    else:
        candidates = [os.path.join(directory or ".", name) for directory in env.get("PATH", os.defpath).split(os.pathsep)]
    denied = False
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return None
        denied = denied or os.path.exists(candidate)
    error = PermissionError(errno.EACCES, os.strerror(errno.EACCES)) if denied else FileNotFoundError()
    return launch_failure(command, error)


def systemd_version() -> int:
    match = re.match(r"systemd (\d+)", run_captured(["systemctl", "--version"], 10).stdout)
    return int(match.group(1)) if match else 0


def set_slice_cap(share: int | None) -> str | None:
    if shutil.which("systemd-run") is None:
        return "systemd-run is not installed"
    cap = [] if share is None else ["MemoryMax={}".format(share)]
    result = run_captured(["systemctl", "--user", "set-property", "--runtime", SLICE, "CPUWeight={}".format(CPU_WEIGHT),
                           *cap], 15)
    if result.returncode != 0:
        return "no systemd user session: {}".format(last_error(result))
    return None


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_json(path: Path, data: dict) -> None:
    staged = temporary(path)
    staged.write_text(json.dumps(data))
    os.replace(str(staged), str(path))


def execute(command: list[str], env: dict, share: LiveShare, ticket: Ticket, profile: Profile) -> Measured:
    if sys.platform == "linux":
        with ticket.queue.lock():
            host = share.read()
            failure = set_slice_cap(None if share.warned else host.share)
        share.print_warning()
        if failure is None:
            version = systemd_version()
            options = ["-p", "OOMPolicy=continue"] if version >= OOM_POLICY_MIN_SYSTEMD else []
            if version >= EXPAND_ENVIRONMENT_MIN_SYSTEMD:
                options.append("--expand-environment=no")
            repo = os.fsencode(ticket.run.repo).decode(errors="backslashreplace")
            scope = ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--slice=" + SLICE,
                     "--unit={}-{}".format(UNIT_PREFIX, ticket.run.id),
                     "--description=jobgate {} in {}".format(profile.name, repo), *options, "--",
                     sys.executable, str(Path(__file__).resolve()), "_scope", str(ticket.files.scope), "--", *command]
            outcome = supervise(scope, env)
            result = read_json(ticket.files.scope)
            if "cgroup" in result:
                return Measured(outcome, result.get("peak"), result.get("oom_kills"))
            if outcome.signal is not None:
                return Measured(Exit(128 + outcome.signal, outcome.signal))
            failure = result.get("failure") or "systemd-run could not start a scope"
        say("the kernel memory cap is off ({}); admission and the pressure hold still apply".format(failure))
    elif sys.platform == "darwin":
        code = exec_failure(command, env)
        if code is not None:
            return Measured(Exit(code))
        if os.access(TASKPOLICY, os.X_OK):
            command = [TASKPOLICY, "-c", "utility", *command]
        else:
            say("{} is missing, so the command runs without the utility QoS clamp".format(TASKPOLICY))
    sampler = PeakSampler()
    outcome = supervise(command, env, sampler.sample)
    return Measured(outcome, sampler.peak or None)


def scope_main(result: Path, command: list[str]) -> int:
    """systemd removes a scope's cgroup when its last process exits, so the scope reads its own peak from inside."""
    with open("/proc/self/cgroup") as stream:
        path = next((line[3:].strip() for line in stream if line.startswith("0::")), "")
    cgroup = Path(test_readings().get("cgroup") or "/sys/fs/cgroup" + path)
    if not (cgroup / "memory.max").exists():
        write_json(result, {"failure": "the scope {} has no memory controller".format(cgroup)})
        return NO_MEMORY_CONTROLLER
    write_json(result, {"cgroup": str(cgroup)})
    peak_file = cgroup / "memory.peak"
    samples: list[int] = []

    def sample(pid: int) -> None:
        samples.append(int((cgroup / "memory.current").read_text()))

    outcome = supervise(command, None, None if peak_file.exists() else sample)
    try:
        peak: int | None = int(peak_file.read_text())
    except (OSError, ValueError):
        peak = max(samples, default=None)
    with contextlib.suppress(OSError):
        write_json(result, {"cgroup": str(cgroup), "peak": peak, "oom_kills": oom_kills(cgroup)})
    return outcome.code


def oom_kills(cgroup: Path) -> int | None:
    try:
        match = re.search(r"^oom_kill (\d+)$", (cgroup / "memory.events").read_text(), re.MULTILINE)
    except OSError:
        return None
    return int(match.group(1)) if match else None


def git_state(repo: Path) -> tuple[str, Dirty]:
    head = run_captured(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", "HEAD"])
    status = run_captured(["git", "-C", str(repo), "status", "--porcelain"])
    commit = head.stdout.strip() if head.returncode == 0 else "none"
    dirty: Dirty = "unknown" if status.returncode != 0 else ("yes" if status.stdout.strip() else "no")
    return commit, dirty


def logfmt(value: object) -> str:
    text = str(value)
    return json.dumps(text) if not text or re.search(r'[\s"=]', text) else text


def report(queue: Queue, entries: list[tuple[str, object]]) -> None:
    line = " ".join("{}={}".format(key, logfmt(value)) for key, value in entries)
    say(line)
    try:
        with open(queue.results, "a", errors="backslashreplace") as stream:
            stream.write(line + "\n")
    except OSError as error:
        say("cannot append to {}: {}".format(queue.results, error.strerror or error))


def launch_failure(command: list[str], error: OSError) -> int:
    if isinstance(error, FileNotFoundError):
        say("command not found: {}".format(command[0]))
        return NOT_FOUND
    say("cannot run {}: {}".format(command[0], error.strerror or error))
    return CANNOT_EXECUTE


def nested(profile: Profile, workers: int, command: list[str], run_file: str) -> int:
    say("{} runs inside run {} with {}".format(profile.name, Path(run_file).stem, plural(workers, "worker")))
    env = dict(os.environ, **{profile.workers_env: str(workers), WORKERS_ENV: str(workers)})
    try:
        os.execvpe(command[0], command, env)
    except OSError as error:
        return launch_failure(command, error)


def run(profile_name: str, max_workers: int | None, command: list[str]) -> int:
    config = find_config(Path.cwd())
    profile = load_profile(config, profile_name)
    limit = min(profile.max_workers, max_workers or profile.max_workers)
    outer = os.environ.get(RUN_ENV, "")
    outer_workers = os.environ.get(WORKERS_ENV, "")
    if outer_workers.isdigit() and int(outer_workers) >= 1 and run_is_live(outer):
        return nested(profile, min(limit, int(outer_workers)), command, outer)
    queue = Queue.open()
    host = machine()
    refuse_if_never_fits(profile, host, config)
    share = LiveShare(host)
    ticket = queue.enter(profile.name, config.parent, command)
    try:
        admission, waited = wait_for_admission(queue, ticket, share, profile, limit, config)
        workers, stated = admission.workers, admission.memory
        commit, dirty = git_state(config.parent)
        say("{} starts with {} ({}={}), {} stated{}".format(
            profile.name, plural(workers, "worker"), profile.workers_env, workers, size(stated),
            ", after waiting {}".format(duration(waited)) if waited >= 1 else ""))
        env = dict(os.environ, **{profile.workers_env: str(workers), RUN_ENV: str(ticket.files.record), WORKERS_ENV: str(workers)})
        outcome, peak, killed = execute(command, env, share, ticket, profile)
    finally:
        ticket.leave()
    if peak is not None and peak > stated:
        needed = math.ceil((peak - profile.base_mem) / workers / 2**20)
        say('peak {} is above the {} that [{}] states for {}; raise worker_mem to "{}M" in {}'.format(
            size(peak), size(stated), profile.name, plural(workers, "worker"), needed, config))
    report(queue, [
        ("time", datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        ("profile", profile.name), ("where", "local"), ("repo", config.parent), ("commit", commit),
        ("dirty", dirty), ("workers", workers), ("wait", "{}s".format(round(waited))),
        ("peak", mebibytes(peak) if peak is not None else "unknown"), ("stated", mebibytes(stated)),
        *([("oom_kills", killed)] if killed else []), ("exit", outcome.code),
    ])
    return outcome.code


def cap_text() -> str:
    if sys.platform == "darwin":
        return "none on macOS; the free-memory check and the pressure hold delay new runs instead"
    keys = ("MemoryMax", "MemoryCurrent", "CPUWeight")
    result = run_captured(["systemctl", "--user", "show", SLICE, *["-p" + key for key in keys]], 15)
    if result.returncode != 0:
        return "off: " + last_error(result)
    values = dict(line.partition("=")[::2] for line in result.stdout.splitlines() if "=" in line)
    return "{} {}".format(SLICE, " ".join(
        "{}={}".format(key, size(int(values[key])) if values.get(key, "").isdigit() and key != "CPUWeight"
                       else values.get(key, "?")) for key in keys))


def status() -> int:
    host = machine()
    level = pressure()
    queue = Queue.open()
    with queue.lock():
        runs = queue.live()
    load = load_of(queue, runs)
    now = time.time()
    source = machine_config() if machine_config().is_file() else "default"
    lines = [
        "share     {} ({:.0%} of {}; {})".format(size(host.share), host.memory_share, size(host.memory), source),
        "memory    {} of {} stated by running runs".format(size(load.stated), size(host.share)),
        "free      {}".format("unknown" if load.available is None else
                               "{} available; running runs may still use {}".format(size(load.available),
                                                                                    size(load.still_to_use))),
        "cores     {} of {} used by gated workers".format(load.workers, host.cores),
        "pressure  {}{}".format(level.reading, "; new runs wait" if level.holding else ""),
        "cap       {}".format(cap_text()),
    ]
    for run in runs:
        if run.admission is not None:
            lines.append("running   {} pid {}, {}, {} stated, {}for {}, in {}: {}".format(
                run.profile, run.pid, plural(run.admission.workers, "worker"), size(run.admission.memory),
                "" if load.in_use.get(run.id) is None else "uses {}, ".format(size(load.in_use[run.id])),
                duration(now - run.admission.started),
                run.repo, run.command))
        else:
            lines.append("waiting   {} pid {}, for {}{}, in {}: {}".format(
                run.profile, run.pid, duration(now - run.arrived),
                "" if run.wait_reason is None else ", waits " + run.wait_reason, run.repo, run.command))
    sys.stdout.reconfigure(errors="backslashreplace")
    print("\n".join(lines))
    return 0


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        say(message)
        sys.exit(REFUSED)


def positive(text: str) -> int:
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError("must be a whole number of at least 1, not {!r}".format(text))
    return int(text)


def main(argv: list[str]) -> int:
    if argv[:1] == ["_scope"] and len(argv) >= 4 and argv[2] == "--":
        return scope_main(Path(argv[1]), argv[3:])
    command: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, command = argv[:split], argv[split + 1:]
    parser = Parser(prog="jobgate", description="Admit heavy commands so that concurrent runs fit the machine.")
    actions = parser.add_subparsers(dest="action", required=True)
    run_parser = actions.add_parser("run", usage="jobgate run [--max-workers N] PROFILE -- COMMAND [ARG...]",
                                    help="wait for room, then run the command with a worker count")
    run_parser.add_argument("--max-workers", type=positive, metavar="N",
                            help="use at most N workers, below the profile's max_workers")
    run_parser.add_argument("profile", help="a [profile] table in the repository's jobgate.toml")
    actions.add_parser("status", help="show the share, pressure, cap, and runs")
    args = parser.parse_args(argv)
    for number in (signal.SIGTERM, signal.SIGHUP):
        if signal.getsignal(number) is not signal.SIG_IGN:
            signal.signal(number, stop)
    try:
        if args.action == "status":
            return status()
        if not command:
            parser.error("give the command after --, as in: jobgate run test -- pytest")
        return run(args.profile, args.max_workers, command)
    except Refusal as refusal:
        say(str(refusal))
        return REFUSED
    except KeyboardInterrupt:
        return 128 + signal.SIGINT
    except Stopped as stopped:
        return 128 + stopped.number


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
