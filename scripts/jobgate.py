#!/usr/bin/env python3
"""Admit heavy commands, such as test suites, builds, and renders, so that concurrent runs fit the machine.

    jobgate run [--max-workers N] PROFILE -- COMMAND [ARG...]
    jobgate status

See docs/jobgate.md in the astack repository.
"""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import asdict, dataclass, fields
import datetime
import fcntl
import json
import math
import os
from pathlib import Path
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

DEFAULT_SHARE = 0.5
PSI_HOLD_PERCENT = 10.0
DARWIN_LEVELS = {1: "normal", 2: "warn", 4: "critical"}
DARWIN_HOLD_LEVEL = 4
SLICE = "jobgate.slice"
CPU_WEIGHT = 30
SCOPE_OOM_POLICY_SYSTEMD = 253
IDLE_SECONDS = 120.0
POLL_SECONDS = 1.0
SAMPLE_SECONDS = 2.0
REPORT_SECONDS = 60.0
RUN_ENV = "JOBGATE_RUN"
WORKERS_ENV = "JOBGATE_WORKERS"
MACHINE_ENV = "JOBGATE_MACHINE_FILE"
FAILED = 125
CANNOT_EXECUTE = 126
NOT_FOUND = 127
PROFILE_KEYS = ("base_mem", "worker_mem", "max_workers", "workers_env")
MACHINE_KEYS = ("memory_share",)
SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*([KMGT])(?:i?B)?", re.IGNORECASE)
UNITS = {"K": 2**10, "M": 2**20, "G": 2**30, "T": 2**40}
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
RUN_ID = re.compile(r"[0-9]+-[0-9a-f]+")
# Python defines os.PRIO_DARWIN_* only from 3.12; these are the <sys/resource.h> values.
PRIO_DARWIN_PROCESS = 4
PRIO_DARWIN_BG = 0x1000
HID_IDLE = re.compile(rb'"HIDIdleTime" = (\d+)')

State = Literal["waiting", "running"]


class Refusal(Exception):
    pass


def say(text: str) -> None:
    try:
        print("jobgate: " + text, file=sys.stderr, flush=True)
    except OSError:
        pass


def size(count: int) -> str:
    return "{:.1f}G".format(count / 2**30) if count >= 2**30 else "{}M".format(math.ceil(count / 2**20))


def mebibytes(count: int) -> str:
    return "{}M".format(math.ceil(count / 2**20))


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
    share: float

    @property
    def budget(self) -> int:
        return int(self.memory * self.share)


@dataclass(frozen=True)
class Pressure:
    reading: str
    holding: bool


@dataclass
class Run:
    id: str
    seq: int
    pid: int
    profile: str
    repo: str
    command: str
    arrived: float
    state: State = "waiting"
    workers: int = 0
    memory: int = 0
    started: float | None = None


def parse_size(value: object, where: str) -> int:
    match = SIZE.fullmatch(value.strip()) if isinstance(value, str) else None
    if match is None:
        raise Refusal('{} must be a size such as "512M" or "2G", not {!r}'.format(where, value))
    return math.ceil(float(match.group(1)) * UNITS[match.group(2).upper()])


def read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as stream:
            return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise Refusal("cannot read {}: {}".format(path, error)) from None


def find_config(start: Path) -> Path:
    for directory in (start, *start.parents):
        candidate = directory / "jobgate.toml"
        if candidate.is_file():
            return candidate
    raise Refusal("no jobgate.toml in {} or its parents".format(start))


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
    where = "[{}] {{}} in {}".format(name, path)
    max_workers = table["max_workers"]
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
        raise Refusal("{} must be a whole number of at least 1, not {!r}".format(where.format("max_workers"), max_workers))
    workers_env = table["workers_env"]
    if not isinstance(workers_env, str) or not ENV_NAME.fullmatch(workers_env):
        raise Refusal("{} must be an environment variable name, not {!r}".format(where.format("workers_env"), workers_env))
    return Profile(name, parse_size(table["base_mem"], where.format("base_mem")),
                   parse_size(table["worker_mem"], where.format("worker_mem")), max_workers, workers_env)


def xdg_dir(variable: str, default: str) -> Path:
    value = os.environ.get(variable, "")
    return Path(value) if os.path.isabs(value) else Path.home() / default


def machine_config() -> Path:
    return xdg_dir("XDG_CONFIG_HOME", ".config") / "jobgate.toml"


def load_share() -> float:
    path = machine_config()
    if not path.is_file():
        return DEFAULT_SHARE
    data = read_toml(path)
    unknown = sorted(set(data) - set(MACHINE_KEYS))
    if unknown:
        raise Refusal("{} has unknown keys: {}".format(path, ", ".join(unknown)))
    share = data.get("memory_share", DEFAULT_SHARE)
    if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 < share <= 1:
        raise Refusal("memory_share in {} must be a number above 0 and at most 1, not {!r}".format(path, share))
    return float(share)


def injected() -> dict:
    path = os.environ.get(MACHINE_ENV)
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise Refusal("cannot read {} {}: {}".format(MACHINE_ENV, path, error)) from None


def machine() -> Machine:
    readings = injected()
    memory = readings.get("memory") or os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    if "cores" in readings:
        cores = readings["cores"]
    elif hasattr(os, "sched_getaffinity"):
        cores = len(os.sched_getaffinity(0))
    else:
        cores = os.cpu_count() or 1
    return Machine(int(memory), int(cores), load_share())


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
    readings = injected()
    if "psi" in readings:
        return psi_pressure(readings["psi"])
    if "darwin_level" in readings:
        return darwin_pressure(str(readings["darwin_level"]))
    if sys.platform == "darwin":
        try:
            result = subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], capture_output=True,
                                    text=True, timeout=10, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as error:
            return Pressure("unknown ({})".format(error), False)
        return darwin_pressure(result.stdout)
    try:
        return psi_pressure(Path("/proc/pressure/memory").read_text())
    except OSError as error:
        return Pressure("unknown ({})".format(error.strerror or error), False)


def fit(machine: Machine, running: list[Run], profile: Profile, limit: int) -> int:
    free_memory = machine.budget - sum(run.memory for run in running) - profile.base_mem
    free_cores = machine.cores - sum(run.workers for run in running)
    if free_memory < 0:
        return 0
    by_memory = limit if profile.worker_mem == 0 else free_memory // profile.worker_mem
    return max(0, min(limit, free_cores, by_memory))


def shortage(machine: Machine, running: list[Run], profile: Profile) -> str:
    free_cores = machine.cores - sum(run.workers for run in running)
    if free_cores < 1:
        return "for a core: gated workers use all {} cores".format(machine.cores)
    free_memory = max(0, machine.budget - sum(run.memory for run in running))
    return "for memory: 1 worker needs {}, and {} of the {} share is free".format(
        size(profile.memory(1)), size(free_memory), size(machine.budget))


class Queue:
    """The OS drops a process's flock when the process dies, so a crashed run cannot keep its reservation."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.runs = directory / "runs"

    @classmethod
    def open(cls) -> Queue:
        directory = xdg_dir("XDG_CACHE_HOME", ".cache") / "jobgate"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        (directory / "runs").mkdir(mode=0o700, exist_ok=True)
        return cls(directory)

    @contextlib.contextmanager
    def lock(self):
        fd = os.open(str(self.directory / "queue.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def live(self) -> list[Run]:
        runs = []
        for path in self.runs.glob("*.json"):
            try:
                fd = os.open(str(path), os.O_RDONLY)
            except FileNotFoundError:
                continue
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    run = read_run(fd)
                    if run is not None:
                        runs.append(run)
                    continue
                for leftover in (path, path.with_suffix(".scope"), path.with_suffix(".scope.tmp")):
                    with contextlib.suppress(FileNotFoundError):
                        leftover.unlink()
            finally:
                os.close(fd)
        return sorted(runs, key=lambda run: run.seq)

    def enter(self, profile: str, repo: Path, command: list[str]) -> Ticket:
        with self.lock():
            seq = max((run.seq for run in self.live()), default=0) + 1
            text = shlex.join(command)
            run = Run(id="{}-{}".format(seq, secrets.token_hex(4)), seq=seq, pid=os.getpid(), profile=profile,
                      repo=str(repo), command=text if len(text) <= 160 else text[:157] + "...", arrived=time.time())
            fd = os.open(str(self.runs / (run.id + ".json")), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            ticket = Ticket(self, run, fd)
            ticket.save()
            return ticket

    def holds(self, run_id: str) -> bool:
        if not RUN_ID.fullmatch(run_id):
            return False
        try:
            fd = os.open(str(self.runs / (run_id + ".json")), os.O_RDONLY)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        finally:
            os.close(fd)
        return False


def read_run(fd: int) -> Run | None:
    chunks = []
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    try:
        data = json.loads(b"".join(chunks))
        return Run(**{field.name: data[field.name] for field in fields(Run) if field.name in data})
    except (ValueError, TypeError, AttributeError):
        return None


class Ticket:
    def __init__(self, queue: Queue, run: Run, fd: int) -> None:
        self.queue = queue
        self.run = run
        self.fd = fd

    @property
    def scope_result(self) -> Path:
        return self.queue.runs / (self.run.id + ".scope")

    def save(self) -> None:
        os.ftruncate(self.fd, 0)
        os.pwrite(self.fd, json.dumps(asdict(self.run)).encode(), 0)

    def leave(self) -> None:
        with self.queue.lock():
            for path in (self.queue.runs / (self.run.id + ".json"), self.scope_result):
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
        os.close(self.fd)


def admit(queue: Queue, ticket: Ticket, machine: Machine, profile: Profile, limit: int) -> float:
    arrived = time.monotonic()
    next_report = arrived
    while True:
        with queue.lock():
            runs = queue.live()
            ahead = [run for run in runs if run.state == "waiting" and run.seq < ticket.run.seq]
            running = [run for run in runs if run.state == "running"]
            if ahead:
                reason = "behind {}".format(plural(len(ahead), "earlier run"))
            else:
                level = pressure()
                workers = fit(machine, running, profile, limit)
                if level.holding:
                    reason = "for memory pressure to ease: " + level.reading
                elif workers < 1:
                    reason = shortage(machine, running, profile)
                else:
                    ticket.run.state, ticket.run.workers = "running", workers
                    ticket.run.memory, ticket.run.started = profile.memory(workers), time.time()
                    ticket.save()
                    return time.monotonic() - arrived
        now = time.monotonic()
        if now >= next_report:
            say("{} waits {}; waited {}".format(profile.name, reason, duration(now - arrived)))
            next_report = now + REPORT_SECONDS
        time.sleep(POLL_SECONDS)


@dataclass(frozen=True)
class Exit:
    code: int
    signal: int | None = None


def exit_code(returncode: int) -> int:
    return 128 - returncode if returncode < 0 else returncode


def supervise(command: list[str], env: dict | None, sample: Callable[[int], None] | None = None) -> Exit:
    """The terminal already sends Ctrl-C to the command's process group, so jobgate only waits through SIGINT.

    exec resets a Python handler to the default but keeps SIG_IGN, so a signal that nohup ignores stays ignored.
    """
    child: list[subprocess.Popen] = []
    received: list[int] = []

    def forward(number, frame):
        received.append(number)
        if child:
            with contextlib.suppress(ProcessLookupError):
                child[0].send_signal(number)

    handlers = {signal.SIGTERM: forward, signal.SIGHUP: forward, signal.SIGINT: lambda number, frame: None}
    previous = {number: signal.signal(number, handler) for number, handler in handlers.items()
                if signal.getsignal(number) is not signal.SIG_IGN}
    try:
        try:
            child.append(subprocess.Popen(command, env=env))
        except FileNotFoundError:
            say("command not found: {}".format(command[0]))
            return Exit(NOT_FOUND)
        except OSError as error:
            say("cannot run {}: {}".format(command[0], error.strerror or error))
            return Exit(CANNOT_EXECUTE)
        for number in received:
            with contextlib.suppress(ProcessLookupError):
                child[0].send_signal(number)
        while True:
            if sample is not None:
                with contextlib.suppress(OSError, ValueError, subprocess.SubprocessError):
                    sample(child[0].pid)
            try:
                return Exit(exit_code(child[0].wait(timeout=SAMPLE_SECONDS)), received[0] if received else None)
            except subprocess.TimeoutExpired:
                continue
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


class Process(NamedTuple):
    parent: int
    resident_bytes: int


def process_table() -> dict[int, Process]:
    table = {}
    if sys.platform == "linux":
        page = os.sysconf("SC_PAGE_SIZE")
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                with open("/proc/{}/stat".format(entry.name), "rb") as stream:
                    parent = int(stream.read().rpartition(b")")[2].split()[1])
                with open("/proc/{}/statm".format(entry.name), "rb") as stream:
                    resident = int(stream.read().split()[1]) * page
            except (OSError, ValueError, IndexError):
                continue
            table[int(entry.name)] = Process(parent, resident)
        return table
    output = subprocess.run(["ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True, timeout=10,
                            stdin=subprocess.DEVNULL).stdout
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 3 and all(field.isdigit() for field in fields):
            table[int(fields[0])] = Process(int(fields[1]), int(fields[2]) * 1024)
    return table


def process_tree(root: int, table: dict[int, Process]) -> list[int]:
    children: dict[int, list[int]] = {}
    for pid, process in table.items():
        children.setdefault(process.parent, []).append(pid)
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


def idle_seconds() -> float:
    try:
        output = subprocess.run(["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-d", "4", "-r", "-k", "HIDIdleTime"],
                                capture_output=True, timeout=5, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return 0.0
    match = HID_IDLE.search(output)
    return int(match.group(1)) / 1e9 if match else 0.0


class TreeMonitor:
    def __init__(self, darwin: bool) -> None:
        self.darwin = darwin
        self.peak = 0
        self.background: bool | None = None
        self.levels: dict[int, bool] = {}

    def sample(self, root: int) -> None:
        table = process_table()
        tree = process_tree(root, table)
        self.peak = max(self.peak, sum(table[pid].resident_bytes for pid in tree))
        if self.darwin:
            self.prioritize(tree)

    def prioritize(self, tree: list[int]) -> None:
        background = idle_seconds() < IDLE_SECONDS
        if self.background is not None and background != self.background:
            say("background priority: the Mac is in use" if background else
                "normal priority: no keyboard or mouse input for {}".format(duration(IDLE_SECONDS)))
        self.background = background
        for pid in tree:
            if self.levels.get(pid) == background:
                continue
            try:
                os.setpriority(PRIO_DARWIN_PROCESS, pid, PRIO_DARWIN_BG if background else 0)
            except (ProcessLookupError, PermissionError):
                continue
            self.levels[pid] = background


def systemd_version() -> int:
    try:
        output = subprocess.run(["systemctl", "--version"], capture_output=True, text=True, timeout=10,
                                stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0
    match = re.match(r"systemd (\d+)", output)
    return int(match.group(1)) if match else 0


def set_slice_cap(budget: int) -> str | None:
    if shutil.which("systemd-run") is None:
        return "systemd-run is not installed"
    try:
        result = subprocess.run(["systemctl", "--user", "set-property", "--runtime", SLICE,
                                 "MemoryMax={}".format(budget), "CPUWeight={}".format(CPU_WEIGHT)],
                                capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        return "no systemd user session: {}".format(error)
    if result.returncode != 0:
        detail = (result.stderr.strip().splitlines() or ["systemctl exited with {}".format(result.returncode)])[-1]
        return "no systemd user session: {}".format(detail)
    return None


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data))
    os.replace(str(temporary), str(path))


def execute(command: list[str], env: dict, budget: int, ticket: Ticket, profile: Profile) -> tuple[Exit, int | None]:
    if sys.platform == "linux":
        failure = set_slice_cap(budget)
        if failure is None:
            policy = ["-p", "OOMPolicy=continue"] if systemd_version() >= SCOPE_OOM_POLICY_SYSTEMD else []
            scope = ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--slice=" + SLICE,
                     "--unit=jobgate-{}".format(ticket.run.id),
                     "--description=jobgate {} in {}".format(profile.name, ticket.run.repo), *policy, "--",
                     sys.executable, str(Path(__file__).resolve()), "_scope", str(ticket.scope_result), "--", *command]
            outcome = supervise(scope, env)
            result = read_json(ticket.scope_result)
            if "cgroup" in result or outcome.signal is not None:
                return outcome, result.get("peak")
            say("systemd-run could not start a scope, so this run has no kernel memory cap")
        else:
            say("the kernel memory cap is off ({}); admission and the pressure hold still apply".format(failure))
    monitor = TreeMonitor(darwin=sys.platform == "darwin")
    outcome = supervise(command, env, monitor.sample)
    return outcome, monitor.peak or None


def scope_main(result: Path, command: list[str]) -> int:
    """systemd removes a scope's cgroup when its last process exits, so the scope reads its own peak from inside."""
    with open("/proc/self/cgroup") as stream:
        path = next((line[3:].strip() for line in stream if line.startswith("0::")), "")
    cgroup = Path("/sys/fs/cgroup" + path)
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
    write_json(result, {"cgroup": str(cgroup), "peak": peak})
    return outcome.code


def git_state(repo: Path) -> tuple[str, str]:
    def git(*arguments: str) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True, text=True,
                                  timeout=60, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            return None

    head = git("rev-parse", "--verify", "--quiet", "HEAD")
    status = git("status", "--porcelain")
    commit = head.stdout.strip() if head and head.returncode == 0 else "none"
    dirty = "unknown" if status is None or status.returncode != 0 else ("yes" if status.stdout.strip() else "no")
    return commit, dirty


def logfmt(value: object) -> str:
    text = str(value)
    return json.dumps(text) if not text or re.search(r'[\s"=]', text) else text


def report(queue: Queue, entries: list[tuple[str, object]]) -> None:
    line = " ".join("{}={}".format(key, logfmt(value)) for key, value in entries)
    say(line)
    try:
        with open(queue.directory / "results.log", "a") as stream:
            stream.write(line + "\n")
    except OSError as error:
        say("cannot append to {}: {}".format(queue.directory / "results.log", error.strerror or error))


def nested(profile: Profile, workers: int, command: list[str], run_id: str) -> int:
    say("{} runs inside run {} with {}".format(profile.name, run_id, plural(workers, "worker")))
    env = dict(os.environ, **{profile.workers_env: str(workers), WORKERS_ENV: str(workers)})
    try:
        os.execvpe(command[0], command, env)
    except FileNotFoundError:
        say("command not found: {}".format(command[0]))
        return NOT_FOUND
    except OSError as error:
        say("cannot run {}: {}".format(command[0], error.strerror or error))
        return CANNOT_EXECUTE


def run(profile_name: str, max_workers: int | None, command: list[str]) -> int:
    config = find_config(Path.cwd())
    profile = load_profile(config, profile_name)
    limit = min(profile.max_workers, max_workers or profile.max_workers)
    queue = Queue.open()
    outer = os.environ.get(RUN_ENV, "")
    outer_workers = os.environ.get(WORKERS_ENV, "")
    if outer_workers.isdigit() and int(outer_workers) >= 1 and queue.holds(outer):
        return nested(profile, min(limit, int(outer_workers)), command, outer)
    found = machine()
    if profile.memory(1) > found.budget:
        raise Refusal("[{}] needs {} for 1 worker, more than this machine's {} share; lower its base_mem or "
                      "worker_mem in {}, or raise memory_share in {}".format(
                          profile.name, size(profile.memory(1)), size(found.budget), config, machine_config()))
    ticket = queue.enter(profile.name, config.parent, command)
    try:
        waited = admit(queue, ticket, found, profile, limit)
        workers = ticket.run.workers
        commit, dirty = git_state(config.parent)
        stated = profile.memory(workers)
        say("{} starts with {} ({}={}), {} stated{}".format(
            profile.name, plural(workers, "worker"), profile.workers_env, workers, size(stated),
            ", after waiting {}".format(duration(waited)) if waited >= 1 else ""))
        env = dict(os.environ, **{profile.workers_env: str(workers), RUN_ENV: ticket.run.id, WORKERS_ENV: str(workers)})
        outcome, peak = execute(command, env, found.budget, ticket, profile)
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
        ("exit", outcome.code),
    ])
    return outcome.code


def cap_text() -> str:
    if sys.platform == "darwin":
        return "none on macOS; the pressure hold is the backstop"
    try:
        result = subprocess.run(["systemctl", "--user", "show", SLICE, "-p", "MemoryMax", "-p", "CPUWeight",
                                 "-p", "MemoryCurrent"], capture_output=True, text=True, timeout=15,
                                stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        return "off: {}".format(error)
    if result.returncode != 0:
        return "off: " + (result.stderr.strip().splitlines() or ["systemctl exited with {}".format(result.returncode)])[-1]
    values = dict(line.partition("=")[::2] for line in result.stdout.splitlines() if "=" in line)
    shown = ["{}={}".format(key, size(int(values[key])) if values.get(key, "").isdigit() and key != "CPUWeight"
                            else values.get(key, "?")) for key in ("MemoryMax", "CPUWeight", "MemoryCurrent")]
    return "{} {}".format(SLICE, " ".join(shown))


def status() -> int:
    found = machine()
    level = pressure()
    queue = Queue.open()
    with queue.lock():
        runs = queue.live()
    running = [run for run in runs if run.state == "running"]
    now = time.time()
    source = machine_config() if machine_config().is_file() else "default"
    lines = [
        "share     {} ({:.0%} of {}; {})".format(size(found.budget), found.share, size(found.memory), source),
        "memory    {} of {} stated by running runs".format(size(sum(run.memory for run in running)), size(found.budget)),
        "cores     {} of {} used by gated workers".format(sum(run.workers for run in running), found.cores),
        "pressure  {}{}".format(level.reading, "; new runs wait" if level.holding else ""),
        "cap       {}".format(cap_text()),
    ]
    for run in runs:
        if run.state == "running":
            lines.append("running   {} pid {}, {}, {} stated, for {}, in {}: {}".format(
                run.profile, run.pid, plural(run.workers, "worker"), size(run.memory),
                duration(now - (run.started or now)), run.repo, run.command))
        else:
            lines.append("waiting   {} pid {}, for {}, in {}: {}".format(
                run.profile, run.pid, duration(now - run.arrived), run.repo, run.command))
    print("\n".join(lines))
    return 0


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        say(message)
        sys.exit(FAILED)


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
    gate = actions.add_parser("run", usage="jobgate run [--max-workers N] PROFILE -- COMMAND [ARG...]",
                              help="wait for room, then run the command with a worker count")
    gate.add_argument("--max-workers", type=positive, metavar="N", help="use at most N workers, below the profile's max_workers")
    gate.add_argument("profile", help="a [profile] table in the repository's jobgate.toml")
    actions.add_parser("status", help="show the share, pressure, cap, and runs")
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            return status()
        if not command:
            parser.error("give the command after --, as in: jobgate run test -- pytest")
        return run(args.profile, args.max_workers, command)
    except Refusal as refusal:
        say(str(refusal))
        return FAILED
    except KeyboardInterrupt:
        return 128 + signal.SIGINT


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
