#!/usr/bin/env python3
"""Admit heavy commands, such as test suites, builds, and renders, so that concurrent runs fit the machine.

    jobgate run [--max-workers N] [--local] PROFILE -- COMMAND [ARG...]
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
import fnmatch
import functools
import hashlib
import io
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
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from typing import Callable, Iterator, Literal, NamedTuple, NoReturn

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
CONFIG_NAME = "jobgate.toml"
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
BOAT_TABLE = "boat"
BOAT_KEYS = ("setup", "setup_inputs", "type", "exclude")
BOAT_TYPES = ("small", "default", "large", "xlarge")
BOAT_TTL_SECONDS = 3600
BOAT_NEW_SECONDS = 600
BOAT_EXTEND_SECONDS = 600
BOAT_OFF_SECONDS = 3600
BOAT_SAVE_SECONDS = 900
BOAT_SAVE_POLL_SECONDS = 10
BOAT_LOST_SECONDS = 600
BOAT_PAUSE_SECONDS = 5
BOAT_SILENT_SECONDS = 60
BOAT_DELETE_TRIES = 3
BOAT_DELETE_SECONDS = 60
READ_BYTES = 2**20
BOAT_LOGS_KEPT = 20
BOAT_PAYMENT_STATUS = 402
BOAT_PAYMENT_CODES = ("trial_compute_limit_reached",)
BOX_HOME = "/home/user"
BOX_UPLOAD = ".jobgate-upload.tgz"
BOX_FORMAT = "1"
BOX_MEMORY_SHARE = 0.9
BOX_SETUP_FAILED = 4
BOX_REFUSED = 5
BOX_PREPARE = ('set -e; run="$HOME/.jobgate/run"; rm -rf "$run"; mkdir -p "$run"; tar -xzf "$HOME/{0}" -C "$run"; '
               'rm -f "$HOME/{0}"; exec python3 "$run/jobgate.py" _box prepare "$run/run.json"').format(BOX_UPLOAD)
BOX_RUN = 'exec python3 "$HOME/.jobgate/run/jobgate.py" _box run "$HOME/.jobgate/run/run.json"'
RUN_REF = "refs/jobgate/run"
GIT_SECONDS = 600
SECRET_NAMES = (".env", ".env.*", "*.env", ".envrc", "*~", "*.pem", "*.key", "*.p12", "*.pfx", ".netrc", ".npmrc",
                ".pypirc", ".yarnrc.yml", ".pgpass", ".git-credentials", "credentials", "credentials.json",
                "application_default_credentials.json", "*.tfvars", "*.tfstate", "*.tfstate.*", "*.jks", "*.keystore",
                "*.ppk", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*")
REPO_PATH = re.compile(r"[^*?\[\]!#\\\n\x00]+")
COMMIT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
GITLINK_MODE = "160000"
GIT_SELECTORS = ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX", "GIT_QUARANTINE_PATH")
VENDORED = ("tomllib/__init__.py", "tomllib/_parser.py", "tomllib/_re.py", "tomllib/_types.py")
RESULT_PREFIX = "jobgate: time="
RESULT_LINE_CHARS = 4096

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


def run_captured(argv: list[str], timeout: float = 60, env: dict | None = None,
                 new_session: bool = False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, errors="surrogateescape", timeout=timeout,
                              stdin=subprocess.DEVNULL, env=env, start_new_session=new_session)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", "{} timed out after {:g}s".format(argv[0], timeout))
    except OSError as error:
        return subprocess.CompletedProcess(argv, 127, "", "cannot run {}: {}".format(argv[0], error.strerror or error))


def last_error(result: subprocess.CompletedProcess) -> str:
    return (result.stderr.strip().splitlines() or ["{} exited with {}".format(result.args[0], result.returncode)])[-1]


def find_config(start: Path) -> Path:
    top = run_captured(["git", "-C", str(start), "rev-parse", "--show-toplevel"])
    root = Path(top.stdout.removesuffix("\n")) if top.returncode == 0 else start
    candidate = root / CONFIG_NAME
    if not candidate.is_file():
        raise Refusal("no jobgate.toml in {}".format(root))
    return candidate


def load_profile(path: Path, data: dict, name: str) -> Profile:
    table = data.get(name) if name != BOAT_TABLE else None
    if not isinstance(table, dict):
        names = ", ".join(key for key, value in data.items() if isinstance(value, dict) and key != BOAT_TABLE) or "none"
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


@dataclass(frozen=True)
class BoatSettings:
    setup: str
    setup_inputs: tuple[str, ...]
    type: str
    exclude: tuple[str, ...]

    def sparse_patterns(self) -> list[str]:
        return ["/*"] + ["!/" + path for path in self.exclude]

    def excludes(self, path: str) -> bool:
        return any(path == excluded or path.startswith(excluded + "/") for excluded in self.exclude)


def repo_path(value: object, where: str) -> str:
    parts = value.rstrip("/").split("/") if isinstance(value, str) else []
    if not parts or any(part in ("", ".", "..") for part in parts) or not REPO_PATH.fullmatch(str(value)):
        raise Refusal('{} must be a path inside the repository, such as "scripts/boat-setup.sh", not {!r}'.format(
            where, value))
    return "/".join(parts)


def load_boat(path: Path, data: dict) -> BoatSettings | None:
    if BOAT_TABLE not in data:
        return None
    table = data[BOAT_TABLE]
    if not isinstance(table, dict):
        raise Refusal("{} in {} must be a [boat] table".format(BOAT_TABLE, path))
    unknown = sorted(set(table) - set(BOAT_KEYS))
    if unknown:
        raise Refusal("[boat] in {} has unknown keys: {}".format(path, ", ".join(unknown)))
    if "setup" not in table:
        raise Refusal("[boat] in {} needs setup, the path of the script that sets up a box".format(path))

    def where(key: str) -> str:
        return "[boat] {} in {}".format(key, path)

    def paths(key: str) -> tuple[str, ...]:
        value = table.get(key, [])
        if not isinstance(value, list):
            raise Refusal("{} must be a list of paths, not {!r}".format(where(key), value))
        return tuple(repo_path(item, where(key)) for item in value)

    box_type = table.get("type", "default")
    if box_type not in BOAT_TYPES:
        raise Refusal("{} must be one of {}, not {!r}".format(where("type"), ", ".join(BOAT_TYPES), box_type))
    settings = BoatSettings(repo_path(table["setup"], where("setup")), paths("setup_inputs"), box_type, paths("exclude"))
    needed = next((path for path in (CONFIG_NAME, settings.setup, *settings.setup_inputs) if settings.excludes(path)),
                  None)
    if needed is not None:
        raise Refusal("{} covers {}, which the box needs".format(where("exclude"), needed))
    return settings


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
    cache = sum(int(stat.get(key, 0)) for key in ("active_file", "inactive_file", "slab_reclaimable"))
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
    status = run_captured(["git", "-C", str(repo), "--no-optional-locks", "status", "--porcelain",
                           "--untracked-files=normal"])
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


class BoatUnavailable(Exception):
    def __init__(self, code: str, reason: str, status: object = None) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.status = status
        self.box: str | None = None
        self.step = "start"


class BoatDied(Exception):
    def __init__(self, step: str, reason: str, out_of_memory: bool = False) -> None:
        super().__init__(reason)
        self.step = step
        self.reason = reason
        self.out_of_memory = out_of_memory
        self.box: str | None = None


class Fallback(NamedTuple):
    code: str


@dataclass(frozen=True)
class Source:
    store: Path
    head: str
    commit: str
    branch: str
    left_out: tuple[str, ...]
    object_format: str


class Plan(NamedTuple):
    name: str
    prefix: str
    setup_key: str


def scratch_env() -> dict:
    return {key: value for key, value in os.environ.items() if key not in GIT_SELECTORS}


def git(args: list[str], cwd: Path | None = None, git_dir: Path | None = None, env: dict | None = None,
        stdin_text: str | None = None, config: tuple[str, ...] = ()) -> str:
    if env is None and git_dir is not None:
        env = scratch_env()
    argv = ["git", *(option for setting in config for option in ("-c", setting)),
            *(["--git-dir", str(git_dir)] if git_dir is not None else []), *args]
    feed: dict = ({"input": stdin_text.encode(errors="surrogateescape")} if stdin_text is not None
                  else {"stdin": subprocess.DEVNULL})
    try:
        result = subprocess.run(argv, cwd=None if cwd is None else str(cwd), env=env, capture_output=True,
                                timeout=GIT_SECONDS, **feed)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BoatUnavailable("git", "git {} failed: {}".format(args[0], error)) from None
    if result.returncode != 0:
        errors = subprocess.CompletedProcess(argv, result.returncode, "", result.stderr.decode(errors="replace"))
        raise BoatUnavailable("git", "git {} failed: {}".format(args[0], last_error(errors)))
    return result.stdout.decode(errors="surrogateescape")


def may_hold_secret(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in SECRET_NAMES)


def take_source(root: Path, scratch: Path, dirty: Dirty) -> Source:
    head = run_captured(["git", "-C", str(root), "rev-parse", "--verify", "-q", "HEAD"])
    if head.returncode != 0:
        raise BoatUnavailable("no_commit", "the repository has no commit yet")
    branch = run_captured(["git", "-C", str(root), "symbolic-ref", "--short", "-q", "HEAD"]).stdout.strip()
    objects = git(["rev-parse", "--path-format=absolute", "--git-path", "objects"], cwd=root).strip()
    object_format = git(["rev-parse", "--show-object-format"], cwd=root).strip()
    store = scratch / "store.git"
    git(["init", "-q", "--bare", "--object-format=" + object_format, str(store)], env=scratch_env())
    (store / "objects/info/alternates").write_bytes(os.fsencode(objects) + b"\n")
    commit, left_out = head.stdout.strip(), ()
    if dirty != "no":
        commit, left_out = commit_worktree(root, scratch, store, objects, commit)
    git(["update-ref", RUN_REF, commit], git_dir=store)
    return Source(store, head.stdout.strip(), commit, branch, left_out, object_format)


def commit_worktree(root: Path, scratch: Path, store: Path, objects: str, head: str) -> tuple[str, tuple[str, ...]]:
    index = scratch / "index"
    env = dict(os.environ, GIT_INDEX_FILE=str(index), GIT_OBJECT_DIRECTORY=str(store / "objects"),
               GIT_ALTERNATE_OBJECT_DIRECTORIES=objects, GIT_LITERAL_PATHSPECS="1", GIT_AUTHOR_NAME="jobgate",
               GIT_AUTHOR_EMAIL="jobgate@localhost", GIT_COMMITTER_NAME="jobgate", GIT_COMMITTER_EMAIL="jobgate@localhost")
    config = ("core.splitIndex=false",)
    try:
        shutil.copy2(git(["rev-parse", "--path-format=absolute", "--git-path", "index"], cwd=root).strip(), str(index))
    except OSError:
        git(["read-tree", head], cwd=root, env=env, config=config)
    git(["add", "-u"], cwd=root, env=env, config=config)
    untracked = [name for name in git(["ls-files", "-z", "--others", "--exclude-standard"], cwd=root, env=env,
                                      config=config).split("\0") if name]
    sent = [name for name in untracked if not may_hold_secret(name)]
    if sent:
        git(["add", "--pathspec-from-file=-", "--pathspec-file-nul"], cwd=root, env=env, stdin_text="\0".join(sent),
            config=config)
    tree = git(["write-tree"], cwd=root, env=env, config=config).strip()
    left_out = tuple(name for name in untracked if may_hold_secret(name))
    if tree == git(["rev-parse", head + "^{tree}"], cwd=root, env=env).strip():
        return head, left_out
    return git(["commit-tree", tree, "-p", head, "-m", "jobgate: uncommitted changes"], cwd=root, env=env).strip(), left_out


def line_ending_forms(chunks: Iterator[bytes]) -> Iterator[tuple[bytes, bytes]]:
    held = b""
    for chunk in chunks:
        data = held + chunk
        held = b"\r" if data.endswith(b"\r") else b""
        lf = data[:len(data) - len(held)].replace(b"\r\n", b"\n")
        yield lf, lf.replace(b"\n", b"\r\n")
    yield held, held


def content_ids(read: Callable[[], Iterator[bytes]], algorithm: str, with_raw: bool) -> set[str]:
    size = lf_size = crlf_size = 0

    def counted() -> Iterator[bytes]:
        nonlocal size
        for chunk in read():
            size += len(chunk)
            yield chunk

    for lf, crlf in line_ending_forms(counted()):
        lf_size += len(lf)
        crlf_size += len(crlf)
    raw = hashlib.new(algorithm, b"blob %d\0" % size) if with_raw else None
    lf_digest = hashlib.new(algorithm, b"blob %d\0" % lf_size) if lf_size != size else None
    crlf_digest = hashlib.new(algorithm, b"blob %d\0" % crlf_size) if crlf_size != size else None
    if raw is None and lf_digest is None and crlf_digest is None:
        return set()

    def tapped() -> Iterator[bytes]:
        for chunk in read():
            if raw is not None:
                raw.update(chunk)
            yield chunk

    for lf, crlf in line_ending_forms(tapped()):
        if lf_digest is not None:
            lf_digest.update(lf)
        if crlf_digest is not None:
            crlf_digest.update(crlf)
    return {digest.hexdigest() for digest in (raw, lf_digest, crlf_digest) if digest is not None}


@contextlib.contextmanager
def git_lines(store: Path, *commands: list[str]) -> Iterator[Iterator[str]]:
    processes: list[subprocess.Popen] = []
    with tempfile.TemporaryFile() as errors:
        upstream = None
        for args in commands:
            process = subprocess.Popen(["git", "--git-dir", str(store), *args], stdin=upstream or subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=errors, env=scratch_env())
            if upstream is not None:
                upstream.close()
            upstream = process.stdout
            processes.append(process)
        timer = threading.Timer(GIT_SECONDS, lambda: [process.kill() for process in processes])
        timer.start()
        output = processes[-1].stdout
        assert output is not None
        try:
            yield (line.rstrip(b"\n").decode(errors="surrogateescape") for line in output)
        finally:
            timer.cancel()
            output.close()
            codes = [process.wait() for process in processes]
        if any(codes):
            errors.seek(0)
            reason = (errors.read().decode(errors="replace").strip().splitlines() or ["exit {}".format(codes)])[-1]
            raise BoatUnavailable("git", "git {} failed: {}".format(commands[-1][0], reason))


class BlobReader:
    def __init__(self, store: Path) -> None:
        self.process = subprocess.Popen(["git", "--git-dir", str(store), "cat-file", "--batch"], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=scratch_env())
        self.timer = threading.Timer(GIT_SECONDS, self.process.kill)
        self.timer.start()

    def chunks(self, blob: str) -> Iterator[bytes]:
        stdin, stdout = self.process.stdin, self.process.stdout
        assert stdin is not None and stdout is not None
        stdin.write(blob.encode() + b"\n")
        stdin.flush()
        header = stdout.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            raise BoatUnavailable("git", "git cat-file cannot read {}, which the copy check needs: {}".format(
                blob, b" ".join(header).decode(errors="replace") or "no answer"))
        remaining = int(header[2])
        while remaining:
            chunk = stdout.read(min(READ_BYTES, remaining))
            if not chunk:
                raise BoatUnavailable("git", "git cat-file stopped while it read {}".format(blob))
            remaining -= len(chunk)
            yield chunk
        stdout.read(1)

    def close(self) -> None:
        self.timer.cancel()
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()
        self.process.kill()
        self.process.wait()


def file_chunks(descriptor: int, size: int, path: str) -> Iterator[bytes]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    total = 0
    while True:
        chunk = os.read(descriptor, READ_BYTES)
        if not chunk:
            break
        total += len(chunk)
        yield chunk
    if total != size:
        raise OSError("{} changed while jobgate read it".format(path))


def unreachable(error: OSError) -> bool:
    return isinstance(error, FileNotFoundError) or error.errno == errno.ELOOP


def worktree_paths(root: Path, settings: BoatSettings) -> Iterator[str]:
    walked: set[tuple[int, int]] = set()

    def fail(error: OSError) -> NoReturn:
        raise error

    for excluded in settings.exclude:
        top = str(root / excluded)
        try:
            is_directory = stat.S_ISDIR(os.stat(top).st_mode)
        except OSError as error:
            if unreachable(error):
                continue
            raise
        if not is_directory:
            yield top
            continue
        for directory, subdirectories, names in os.walk(top, onerror=fail, followlinks=True):
            identity = os.stat(directory)
            if (identity.st_dev, identity.st_ino) in walked:
                subdirectories.clear()
                continue
            walked.add((identity.st_dev, identity.st_ino))
            yield from (os.path.join(directory, name) for name in names)


def worktree_ids(root: Path, settings: BoatSettings, algorithm: str) -> set[str]:
    ids: set[str] = set()
    for path in worktree_paths(root, settings):
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as error:
            if unreachable(error):
                continue
            raise
        try:
            info = os.fstat(descriptor)
            if stat.S_ISREG(info.st_mode):
                ids |= content_ids(functools.partial(file_chunks, descriptor, info.st_size, path), algorithm, True)
        finally:
            os.close(descriptor)
    return ids


def history_blobs(source: Source, settings: BoatSettings) -> set[str]:
    blobs = set()
    with git_lines(source.store, ["rev-list", RUN_REF],
                   ["--literal-pathspecs", "diff-tree", "--stdin", "-r", "-m", "--root", "--no-renames", "--no-commit-id",
                    "--raw", "--no-abbrev", "--", *settings.exclude]) as changes:
        for line in changes:
            fields = line.split("\t", 1)[0].split()
            if len(fields) < 4 or not line.startswith(":"):
                continue
            for mode, blob in ((fields[0][1:], fields[2]), (fields[1], fields[3])):
                if mode not in ("000000", GITLINK_MODE) and COMMIT_ID.fullmatch(blob):
                    blobs.add(blob)
    return blobs


def private_blobs(source: Source, settings: BoatSettings, root: Path) -> set[str]:
    history = history_blobs(source, settings)
    blobs = set(history)
    if history:
        with contextlib.closing(BlobReader(source.store)) as reader:
            for blob in sorted(history):
                blobs |= content_ids(functools.partial(reader.chunks, blob), source.object_format, False)
    blobs |= worktree_ids(root, settings, source.object_format)
    blobs.discard(git(["hash-object", "--stdin"], git_dir=source.store, stdin_text="").strip())
    return blobs


def check_exclude(source: Source, settings: BoatSettings, root: Path) -> None:
    if not settings.exclude:
        return
    excluded_blobs = private_blobs(source, settings, root)
    if not excluded_blobs:
        return
    patterns = "".join(line + "\n" for line in settings.sparse_patterns())
    blob = git(["hash-object", "-w", "--stdin"], git_dir=source.store, stdin_text=patterns).strip()
    with git_lines(source.store, ["rev-list", "--objects", "--filter=sparse:oid=" + blob, RUN_REF]) as objects:
        elsewhere = sorted({line.split(" ", 1)[1] for line in objects
                            if " " in line and line.split(" ", 1)[0] in excluded_blobs})
    if elsewhere:
        shown = ", ".join(elsewhere[:5]) + ("" if len(elsewhere) <= 5 else ", and {} more".format(len(elsewhere) - 5))
        raise BoatUnavailable("excluded_elsewhere", "content from the excluded paths is also at {}, in the history or "
                              "the uncommitted changes. Add {} to exclude. After that, the next run can name other "
                              "paths with the same content".format(
                                  shown, "these paths" if len(elsewhere) > 1 else "this path"))


def check_box_paths(source: Source, settings: BoatSettings, cwd: str) -> None:
    for path in (CONFIG_NAME, cwd):
        if path == ".":
            continue
        listing = git(["ls-tree", "-r", "-z", source.commit, "--", path], git_dir=source.store)
        entries = [entry.split("\t", 1) for entry in listing.split("\0") if "\t" in entry]
        if not any(info.split()[1] != "commit" and not settings.excludes(name) for info, name in entries):
            raise BoatUnavailable("missing_on_box", "the box would not get {}, which the run needs. The commit lacks "
                                  "its files, or exclude leaves them out".format(path))


def make_plan(source: Source, settings: BoatSettings) -> Plan:
    digest = hashlib.sha256(BOX_FORMAT.encode())
    for path in (settings.setup, *settings.setup_inputs):
        answer = git(["cat-file", "--batch-check", "--follow-symlinks"], git_dir=source.store,
                     stdin_text="{}:{}\n".format(source.commit, path)).strip()
        parts = answer.split()
        kind = parts[1] if len(parts) == 3 else ""
        if path == settings.setup and kind != "blob":
            raise BoatUnavailable("no_setup", "the setup script {} is not in the repository".format(settings.setup))
        if kind in ("blob", "tree"):
            content = parts[0]
        else:
            content = answer if answer.startswith("symlink ") else ""
        digest.update("{}\0{}\n".format(path, content).encode(errors="surrogateescape"))
    digest.update("\0".join(settings.exclude).encode(errors="surrogateescape"))
    roots = sorted(git(["rev-list", "--max-parents=0", source.commit], git_dir=source.store).split())
    prefix = "jobgate-{}-".format(roots[0][:12])
    key = digest.hexdigest()
    return Plan(prefix + key[:12], prefix, key)


def make_bundle(source: Source, settings: BoatSettings, basis: object, path: Path) -> bool:
    revs = [RUN_REF]
    if isinstance(basis, str) and COMMIT_ID.fullmatch(basis) and run_captured(
            ["git", "--git-dir", str(source.store), "cat-file", "-e", basis + "^{commit}"], env=scratch_env()).returncode == 0:
        if run_captured(["git", "--git-dir", str(source.store), "merge-base", "--is-ancestor", source.commit,
                         basis], env=scratch_env()).returncode == 0:
            return False
        revs.append("^" + basis)
    options = []
    if settings.exclude:
        patterns = "".join(line + "\n" for line in settings.sparse_patterns())
        blob = git(["hash-object", "-w", "--stdin"], git_dir=source.store, stdin_text=patterns).strip()
        options.append("--filter=sparse:oid=" + blob)
    git(["bundle", "create", "-q", str(path), *options, *revs], git_dir=source.store)
    return True


def pack_upload(scratch: Path, spec: dict) -> Path:
    here = Path(__file__).resolve()
    vendor = here.parent / "_vendor"
    path = scratch / "upload.tgz"
    with tarfile.open(str(path), "w:gz") as archive:
        archive.add(str(here), "jobgate.py")
        for name in VENDORED:
            if (vendor / name).is_file():
                archive.add(str(vendor / name), "_vendor/" + name)
        data = json.dumps(spec).encode()
        info = tarfile.TarInfo("run.json")
        info.size, info.mode, info.mtime = len(data), 0o644, int(time.time())
        archive.addfile(info, io.BytesIO(data))
        if spec["bundle"]:
            archive.add(str(scratch / "run.bundle"), "run.bundle")
    return path


def repo_path_entries(root: Path) -> list[str]:
    top = os.path.realpath(str(root))
    entries = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        real = os.path.realpath(entry) if entry else ""
        if real == top or real.startswith(top + os.sep):
            entries.append(os.path.relpath(real, top))
    return entries


def boat_env() -> dict:
    return dict(os.environ, BOAT_NO_UPDATE="1")


def boat_objects(result: subprocess.CompletedProcess) -> list[dict]:
    found = []
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        line = line.strip()
        if line.startswith("{"):
            with contextlib.suppress(ValueError):
                value = json.loads(line)
                if isinstance(value, dict):
                    found.append(value)
    return found


def boat_failure(result: subprocess.CompletedProcess, objects: list[dict]) -> dict | None:
    error = next((value for value in objects if value.get("event") == "error"), None)
    if error is not None:
        return error
    if result.returncode != 0:
        return {"code": "boat_failed", "error": last_error(result)}
    return None


def output_of(result: subprocess.CompletedProcess) -> str:
    return "".join(text if text.endswith("\n") else text + "\n" for text in (result.stdout, result.stderr) if text)


def describe(failure: dict) -> str:
    text = failure.get("error") or failure.get("message") or failure.get("code") or "unknown error"
    return str(text).strip().splitlines()[0]


class BoatLog:
    def __init__(self) -> None:
        directory = account_home() / ".cache/jobgate/boat"
        self.path: Path | None = None
        self.stream = None
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            logs = sorted(directory.glob("*.log"))
            for old in logs[:max(0, len(logs) - BOAT_LOGS_KEPT + 1)]:
                old.unlink()
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
            self.path = directory / "{}-{}.log".format(stamp, secrets.token_hex(3))
            self.stream = open(str(self.path), "w", encoding="utf-8", errors="backslashreplace")
        except OSError as error:
            say("cannot keep a boat log in {}: {}".format(directory, error.strerror or error))

    @property
    def where(self) -> str:
        return str(self.path) if self.stream is not None else "none"

    def write(self, text: str) -> None:
        if self.stream is not None:
            with contextlib.suppress(OSError, ValueError):
                self.stream.write(text)
                self.stream.flush()

    def note(self, text: str) -> None:
        say(text)
        self.entry(text)

    def entry(self, text: str) -> None:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.write("{} jobgate: {}\n".format(stamp, text))

    def close(self) -> None:
        if self.stream is not None:
            with contextlib.suppress(OSError):
                self.stream.close()


class Relay:
    def __init__(self, log: BoatLog) -> None:
        self.log = log
        self.partial = ""
        self.continued = False
        self.results: list[str] = []

    def __call__(self, kind: str, data: str) -> None:
        self.log.write(data)
        if kind == "stdout":
            self.emit(sys.stdout, data)
            return
        lines = (self.partial + data).split("\n")
        self.partial = lines.pop()
        for line in lines:
            self.error_line(line + "\n")
        if self.partial and (self.continued or len(self.partial) > RESULT_LINE_CHARS or not (
                RESULT_PREFIX.startswith(self.partial) or self.partial.startswith(RESULT_PREFIX))):
            self.emit(sys.stderr, self.partial)
            self.partial = ""
            self.continued = True

    def error_line(self, line: str) -> None:
        if not self.continued and line.startswith(RESULT_PREFIX):
            self.results.append(line[len("jobgate: "):].strip())
        else:
            self.emit(sys.stderr, line)
        self.continued = False

    def finish(self) -> None:
        if self.partial:
            self.error_line(self.partial)
            self.partial = ""

    def result(self) -> dict:
        try:
            return dict(field.split("=", 1) for field in shlex.split(self.results[-1]) if "=" in field)
        except (IndexError, ValueError):
            return {}

    @staticmethod
    def emit(stream, text: str) -> None:
        with contextlib.suppress(OSError):
            stream.write(text)
            stream.flush()


class Box:
    def __init__(self, cli: str, log: BoatLog) -> None:
        self.cli = cli
        self.log = log
        self.id: str | None = None

    def call(self, args: list[str], timeout: float, expected: tuple[str, ...] = (),
             new_session: bool = False) -> tuple[subprocess.CompletedProcess, list[dict]]:
        return self.keep(run_captured([self.cli, *args], timeout, env=boat_env(), new_session=new_session), expected)

    def keep(self, result: subprocess.CompletedProcess,
             expected: tuple[str, ...] = ()) -> tuple[subprocess.CompletedProcess, list[dict]]:
        objects = boat_objects(result)
        failure = boat_failure(result, objects)
        if failure is not None and failure.get("code") not in expected:
            self.log.write("boat {} exited with {}:\n{}".format(shlex.join(result.args[1:]), result.returncode,
                                                               output_of(result)))
        return result, objects

    def create(self, box_type: str, name: str) -> bool:
        for from_snapshot in (True, False):
            args = ["new", "--type", box_type, "--no-env", "--fail-fast", "--ttl", str(BOAT_TTL_SECONDS), "--json"]
            if from_snapshot:
                args += ["--from", name, "--no-snapshots"]
            result, objects = self.new(args, ("snapshot_not_found",) if from_snapshot else ())
            failure = boat_failure(result, objects)
            if failure is None and self.id is not None:
                return from_snapshot
            if from_snapshot and failure is not None and failure.get("code") == "snapshot_not_found":
                if self.id is not None:
                    self.delete()
                    self.id = None
                continue
            failure = failure or {"code": "no_box", "error": "boat new printed no box id"}
            code = str(failure.get("code") or "boat_failed")
            if self.id is not None and failure.get("status") != BOAT_PAYMENT_STATUS and code not in BOAT_PAYMENT_CODES:
                raise BoatDied("start", "boat created the box but could not start it: " + describe(failure))
            raise BoatUnavailable(code, describe(failure), failure.get("status"))
        raise AssertionError("unreachable")

    def new(self, args: list[str], expected: tuple[str, ...]) -> tuple[subprocess.CompletedProcess, list[dict]]:
        argv = [self.cli, *args]
        lines: list[str] = []
        with tempfile.TemporaryFile() as errors:
            try:
                process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors,
                                           env=boat_env(), text=True, encoding="utf-8", errors="replace")
            except OSError as error:
                return self.keep(subprocess.CompletedProcess(
                    argv, 127, "", "cannot run {}: {}".format(argv[0], error.strerror or error)), expected)
            output = process.stdout
            assert output is not None
            fired = threading.Event()

            def expire() -> None:
                fired.set()
                process.kill()

            timer = threading.Timer(BOAT_NEW_SECONDS, expire)
            timer.start()
            try:
                for line in output:
                    lines.append(line)
                    with contextlib.suppress(ValueError):
                        value = json.loads(line)
                        if isinstance(value, dict) and isinstance(value.get("id"), str) and value["id"]:
                            self.id = value["id"]
                process.wait()
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()
                    process.wait()
                output.close()
            errors.seek(0)
            stderr = errors.read().decode("utf-8", "replace")
        if fired.is_set():
            stderr += "\nboat new timed out after {}".format(duration(BOAT_NEW_SECONDS))
        return self.keep(subprocess.CompletedProcess(argv, process.returncode, "".join(lines), stderr), expected)

    def state(self) -> dict:
        _, objects = self.call(["exec", str(self.id), "--json", "--", "sh", "-c", 'cat "$HOME/.jobgate/state.json"'], 120)
        for value in objects:
            if value.get("exitCode") == 0 and isinstance(value.get("stdout"), str):
                with contextlib.suppress(ValueError):
                    state = json.loads(value["stdout"])
                    return state if isinstance(state, dict) else {}
        return {}

    def upload(self, path: Path) -> None:
        result, objects = self.call(["scp", str(path), "{}:{}/{}".format(self.id, BOX_HOME, BOX_UPLOAD)], 3600)
        failure = boat_failure(result, objects)
        if failure is not None:
            raise BoatDied("upload", "the copy to the box failed: " + describe(failure))

    def phase(self, step: str, script: str, relay: Relay) -> int:
        result, objects = self.call(["exec", str(self.id), "--detach", "--json", "--", "sh", "-c", script], 120)
        pid = next((value.get("pid") or value.get("processId") for value in objects
                    if isinstance(value.get("pid") or value.get("processId"), int)), None)
        failure = boat_failure(result, objects)
        if failure is not None or pid is None:
            raise BoatDied(step, "the box did not start it: " + describe(failure or {"error": "no process id"}))
        try:
            return self.follow(step, pid, relay)
        finally:
            relay.finish()

    def follow(self, step: str, pid: int, relay: Relay) -> int:
        printed = {"stdout": 0, "stderr": 0}
        lost_since: float | None = None
        ended: dict | None = None
        killed: dict | None = None
        lost = float(test_readings().get("boat_lost_seconds") or BOAT_LOST_SECONDS)
        while True:
            frame = self.attach(pid, printed, relay)
            if frame is not None and not (frame.get("signal") and "oomKilled" not in frame):
                return self.exit_code(step, frame)
            killed = frame or killed
            try:
                status = self.status(step, pid)
                if status is None:
                    lost_since = lost_since or time.monotonic()
                    if time.monotonic() - lost_since >= lost:
                        raise BoatDied(step, "lost contact with the box for {}".format(duration(lost)))
            except BoatDied as died:
                if ended is not None:
                    return self.exit_code(step, ended)
                if step == "run" and killed is not None and killed.get("signal") == "SIGKILL":
                    raise BoatDied(step, "boat killed the run step with SIGKILL and did not say why ({}). It may have "
                                         "run out of memory".format(died.reason), True) from None
                raise
            if status is not None:
                lost_since = None
                if killed is not None:
                    return self.exit_code(step, dict(killed, **status))
                if not status.get("running"):
                    if ended is not None:
                        return self.exit_code(step, status)
                    ended = status
            time.sleep(BOAT_PAUSE_SECONDS)

    def attach(self, pid: int, printed: dict, relay: Relay) -> dict | None:
        # `boat exec --attach` replays a process's output from offset 0 on every attach.
        argv = [self.cli, "exec", str(self.id), "--attach", str(pid), "--json"]
        exit_frame = None
        other: list[str] = []
        silent = float(test_readings().get("boat_silent_seconds") or BOAT_SILENT_SECONDS)
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors,
                                       env=boat_env(), text=True, encoding="utf-8", errors="replace")
            output = process.stdout
            assert output is not None
            heard = time.monotonic()
            replayed = {"stdout": 0, "stderr": 0}
            done, fired = threading.Event(), threading.Event()

            def watch() -> None:
                while not done.wait(min(silent, BOAT_PAUSE_SECONDS)):
                    if time.monotonic() - heard >= silent:
                        fired.set()
                        process.kill()
                        return

            threading.Thread(target=watch, daemon=True).start()
            try:
                for line in output:
                    heard = time.monotonic()
                    try:
                        frame = json.loads(line)
                    except ValueError:
                        frame = None
                    kind = frame.get("type") if isinstance(frame, dict) else None
                    if kind in ("stdout", "stderr") and isinstance(frame.get("data"), str):
                        data = frame["data"]
                        end = frame.get("nextOffset")
                        if isinstance(end, int):
                            start, replayed[kind] = replayed[kind], end
                            if end <= printed[kind]:
                                continue
                            raw = data.encode()
                            if start < printed[kind] and len(raw) == end - start:
                                data = raw[printed[kind] - start:].decode(errors="replace")
                            printed[kind] = end
                        relay(kind, data)
                    elif kind == "exit":
                        exit_frame = frame
                    else:
                        other.append(line)
            finally:
                done.set()
                if process.poll() is None:
                    process.kill()
                process.wait()
                output.close()
            if exit_frame is None:
                errors.seek(0)
                result = subprocess.CompletedProcess(argv, process.returncode, "".join(other),
                                                     errors.read().decode("utf-8", "replace"))
                if fired.is_set():
                    self.log.entry("boat exec {} --attach {} printed nothing for {}, so jobgate checks the step and "
                                   "attaches again".format(self.id, pid, duration(silent)))
                    self.log.write(output_of(result))
                else:
                    self.keep(result)
        return exit_frame

    def status(self, step: str, pid: int) -> dict | None:
        result, objects = self.call(["exec", str(self.id), "--status", str(pid), "--json"], 60)
        failure = boat_failure(result, objects)
        if failure is not None:
            if failure.get("code") == "not_found" or failure.get("status") == 404:
                raise BoatDied(step, "the box is gone")
            return None
        return next((value for value in objects if "running" in value), None)

    @staticmethod
    def exit_code(step: str, info: dict) -> int:
        if info.get("oomKilled"):
            raise BoatDied(step, "the box ran out of memory and killed the {} step".format(step), step == "run")
        code = info.get("exitCode")
        if isinstance(code, int) and not info.get("signal"):
            return code
        raise BoatDied(step, "the {} step on the box ended by signal {}".format(step, info.get("signal")))

    def save(self, name: str, prefix: str) -> None:
        self.log.note("boat saves this box as snapshot {}, so later runs skip the setup".format(name))
        result, objects = self.call(["snapshot", str(self.id), name, "--json"], 120)
        failure = boat_failure(result, objects)
        if failure is not None:
            self.log.note("boat cannot save snapshot {}, so the next run sets up a new box again: {}".format(
                name, describe(failure)))
            return
        deadline = time.monotonic() + BOAT_SAVE_SECONDS
        while True:
            named = self.saved()
            entry = next((value for value in named if value.get("name") == name), {})
            if entry.get("status") == "ready":
                break
            if entry.get("status") == "failed":
                self.log.note("boat could not save snapshot {}: {}".format(name, describe(entry)))
                return
            if time.monotonic() >= deadline:
                self.log.note("boat still saves snapshot {} after {}, and the run goes on".format(
                    name, duration(BOAT_SAVE_SECONDS)))
                return
            time.sleep(BOAT_SAVE_POLL_SECONDS)
        for value in named:
            other = value.get("name")
            if isinstance(other, str) and other.startswith(prefix) and other != name:
                self.call(["snapshot", "rm", other, "--json"], 120)

    def saved(self) -> list[dict]:
        _, objects = self.call(["snapshots", "--json"], 120)
        for value in objects:
            if isinstance(value.get("named"), list):
                return [entry for entry in value["named"] if isinstance(entry, dict)]
        return []

    def extend(self) -> None:
        self.call(["extend", str(self.id), "--ttl", str(BOAT_TTL_SECONDS), "--json"], 60)

    def delete(self) -> None:
        for attempt in range(BOAT_DELETE_TRIES):
            if attempt:
                time.sleep(BOAT_PAUSE_SECONDS)
            result, objects = self.call(["delete", str(self.id), "--yes", "--json"], BOAT_DELETE_SECONDS,
                                        ("not_found",), new_session=True)
            failure = boat_failure(result, objects)
            if failure is None or failure.get("code") == "not_found":
                return
        self.log.note("cannot delete box {}, so boat stops it within {}: {}".format(
            self.id, duration(BOAT_TTL_SECONDS), describe(failure)))


@contextlib.contextmanager
def held_signals(message: str):
    held: list[int] = []

    def hold(number, frame) -> None:
        if not held:
            say(message)
        held.append(number)

    numbers = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = {number: signal.signal(number, hold) for number in numbers}
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        if held:
            signal.raise_signal(held[0])


class Heartbeat:
    def __init__(self, box: Box) -> None:
        self.done = threading.Event()
        self.box = box
        threading.Thread(target=self.beat, daemon=True).start()

    def beat(self) -> None:
        while not self.done.wait(BOAT_EXTEND_SECONDS):
            self.box.extend()

    def stop(self) -> None:
        self.done.set()


def boat_off_file() -> Path:
    return account_home() / ".cache/jobgate/boat-off.json"


def boat_off() -> dict | None:
    data = read_json(boat_off_file())
    until = data.get("until") if isinstance(data, dict) else None
    return data if isinstance(until, (int, float)) and until > time.time() else None


def turn_boat_off(unavailable: BoatUnavailable) -> None:
    with contextlib.suppress(OSError):
        boat_off_file().parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_json(boat_off_file(), {"until": time.time() + BOAT_OFF_SECONDS, "code": unavailable.code,
                                     "reason": unavailable.reason})


class BoatRun(NamedTuple):
    code: int
    result: dict
    started: float | None


def boat_attempt(cli: str, settings: BoatSettings, source: Source, plan: Plan, spec: dict, scratch: Path,
                 log: BoatLog, profile: Profile) -> BoatRun:
    box = Box(cli, log)
    heartbeat = None
    step = "start"
    try:
        from_snapshot = box.create(settings.type, plan.name)
        log.note("{} runs on boat: {} box {}, {}".format(
            profile.name, settings.type, box.id,
            "from snapshot " + plan.name if from_snapshot else "new, so the setup script runs first"))
        heartbeat = Heartbeat(box)
        step = "upload"
        basis = box.state().get("basis") if from_snapshot else None
        spec = dict(spec, bundle=make_bundle(source, settings, basis, scratch / "run.bundle"))
        upload = pack_upload(scratch, spec)
        log.note("boat copies {:.1f} MB to box {}".format(upload.stat().st_size / 1e6, box.id))
        box.upload(upload)
        step = "setup"
        code = box.phase("setup", BOX_PREPARE, Relay(log))
        if code == BOX_SETUP_FAILED:
            raise BoatDied("setup", "the setup script {} failed".format(settings.setup))
        if code == BOX_REFUSED:
            raise BoatUnavailable("box_refused", "jobgate on the box refused to run [{}]. The box printed why "
                                  "above".format(profile.name))
        if code != 0:
            raise BoatDied("setup", "the box could not check out the commit (exit {})".format(code))
        if not from_snapshot:
            box.save(plan.name, plan.prefix)
        step = "run"
        started = time.monotonic()
        relay = Relay(log)
        return BoatRun(box.phase("run", BOX_RUN, relay), relay.result(), started)
    except BoatDied as died:
        died.box = box.id
        raise
    except BoatUnavailable as unavailable:
        unavailable.box, unavailable.step = box.id, step
        raise
    except OSError as error:
        unavailable = BoatUnavailable("local_error", "jobgate on this machine failed: {}".format(error))
        unavailable.box, unavailable.step = box.id, step
        raise unavailable from error
    finally:
        with held_signals("jobgate stops after it deletes box {}".format(box.id) if box.id is not None else
                          "jobgate stops after it cleans up"):
            if heartbeat is not None:
                heartbeat.stop()
            if box.id is not None:
                box.delete()


def boat_run(config: Path, settings: BoatSettings, profile: Profile, max_workers: int | None,
             command: list[str]) -> int | Fallback:
    arrived = time.monotonic()
    off = boat_off()
    if off is not None:
        say("boat is off until {}, so {} runs here. Boat refused a run with: {}".format(
            time.strftime("%H:%M %Z", time.localtime(off["until"])), profile.name, off.get("reason")))
        return Fallback(str(off.get("code") or "off"))
    cli = shutil.which("boat")
    if cli is None:
        say("the boat CLI is not on PATH, so {} runs here".format(profile.name))
        return Fallback("no_cli")
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(errors="backslashreplace")
    queue = Queue.open()
    root = config.parent
    _, dirty = git_state(root)
    here = os.path.relpath(os.path.realpath(os.getcwd()), os.path.realpath(str(root)))
    cwd = "." if here == os.pardir or here.startswith(os.pardir + os.sep) else here
    scratch: Path | None = None
    try:
        try:
            scratch = Path(tempfile.mkdtemp(prefix="jobgate-boat-"))
            source = take_source(root, scratch, dirty)
            check_exclude(source, settings, root)
            plan = make_plan(source, settings)
            check_box_paths(source, settings, cwd)
        except OSError as error:
            say("boat cannot run {}, so it runs here: jobgate on this machine failed: {}".format(profile.name, error))
            return Fallback("local_error")
        except BoatUnavailable as unavailable:
            say("boat cannot run {}, so it runs here: {}".format(profile.name, unavailable.reason))
            return Fallback(unavailable.code)
        if source.commit != source.head:
            say("the box gets the uncommitted changes as a temporary commit, so the result counts as dirty")
        if source.left_out:
            say("left out {} that may hold secrets: {}".format(
                plural(len(source.left_out), "untracked file"), ", ".join(source.left_out)))
        spec = {"head": source.head, "commit": source.commit, "branch": source.branch,
                "patterns": settings.sparse_patterns(), "setup": settings.setup, "setup_key": plan.setup_key,
                "cwd": cwd, "path": repo_path_entries(root), "profile": profile.name,
                "max_workers": max_workers, "command": command, "object_format": source.object_format}
        for attempt in (1, 2):
            log = BoatLog()
            try:
                outcome = boat_attempt(cli, settings, source, plan, spec, scratch, log, profile)
            except BoatUnavailable as unavailable:
                if unavailable.box is not None:
                    log.note("BOAT RUN FAILED during {} on box {}: {}".format(
                        unavailable.step, unavailable.box, unavailable.reason))
                log.note("boat cannot run {}, so it runs here: {}".format(profile.name, unavailable.reason))
                log.note("boat log: {}".format(log.where))
                if unavailable.status == BOAT_PAYMENT_STATUS or unavailable.code in BOAT_PAYMENT_CODES:
                    turn_boat_off(unavailable)
                return Fallback(unavailable.code)
            except BoatDied as died:
                log.note("BOAT RUN FAILED during {} on box {}: {}".format(died.step, died.box or "none", died.reason))
                log.note("boat log: {}".format(log.where))
                if not died.out_of_memory:
                    log.note("{} retries once on a new box".format(profile.name) if attempt == 1 else
                             "{} runs here instead".format(profile.name))
                    continue
                log.note("jobgate does not run the command again, on a new box or here")
                log.note("to give it more memory, set a larger type in [boat], or a larger worker_mem in the profile")
                outcome = BoatRun(128 + signal.SIGKILL, {}, None)
            finally:
                log.close()
            fields = outcome.result
            report(queue, [
                ("time", datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
                ("profile", profile.name), ("where", "boat"), ("box", settings.type), ("repo", root),
                ("commit", source.head), ("dirty", dirty), ("workers", fields.get("workers", "unknown")),
                ("wait", "unknown" if outcome.started is None else "{}s".format(round(outcome.started - arrived))),
                ("peak", fields.get("peak", "unknown")),
                ("stated", fields.get("stated", "unknown")),
                *([("oom_kills", fields["oom_kills"])] if "oom_kills" in fields else []), ("exit", outcome.code),
            ])
            return outcome.code
        return Fallback("died")
    finally:
        if scratch is not None:
            shutil.rmtree(str(scratch), ignore_errors=True)


def box_git(repo: Path, *args: str) -> bool:
    result = subprocess.run(["git", "-C", str(repo), *args], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            errors="replace")
    if result.returncode != 0:
        say("git {} on the box exited with {}: {}".format(args[0], result.returncode,
                                                         (result.stderr or result.stdout).strip()[-2000:]))
    return result.returncode == 0


def box_prepare(spec: dict, run_dir: Path, repo: Path) -> int:
    bundle = run_dir / "run.bundle"
    steps: list[list[str]] = []
    if not (repo / ".git").is_dir():
        if subprocess.run(["git", "init", "-q", "--object-format=" + spec["object_format"], str(repo)],
                          stdin=subprocess.DEVNULL).returncode != 0:
            return 1
        steps += [["config", "core.repositoryformatversion", "1"], ["config", "extensions.partialClone", "jobgate"],
                  ["config", "remote.jobgate.url", str(bundle)], ["config", "remote.jobgate.promisor", "true"],
                  ["config", "remote.jobgate.partialclonefilter", "blob:none"]]
    steps.append(["sparse-checkout", "set", "--no-cone", *spec["patterns"]])
    if spec["bundle"]:
        steps.append(["fetch", "-q", str(bundle), "+{0}:{0}".format(RUN_REF)])
    steps.append(["checkout", "-q", "--force", *(["-B", spec["branch"]] if spec["branch"] else ["--detach"]),
                  spec["commit"]])
    for args in steps:
        if not box_git(repo, *args):
            return 1
    config = machine_config()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("memory_share = {}\n".format(BOX_MEMORY_SHARE))
    try:
        profile_config = find_config(repo / spec["cwd"])
        profile = load_profile(profile_config, read_toml(profile_config), spec["profile"])
        host = machine()
        if profile.memory(1) > host.share:
            raise Refusal("[{}] needs {} for 1 worker, more than the {} that this box gives commands; set a larger "
                          "type in [boat], or lower the profile's base_mem or worker_mem".format(
                              profile.name, size(profile.memory(1)), size(host.share)))
    except Refusal as refusal:
        say(str(refusal))
        return BOX_REFUSED
    state_path = Path.home() / ".jobgate/state.json"
    if read_json(state_path).get("setup_key") != spec["setup_key"]:
        say("the box runs the setup script {}".format(spec["setup"]))
        code = subprocess.run(["bash", spec["setup"]], cwd=str(repo), stdin=subprocess.DEVNULL,
                              stdout=sys.stderr).returncode
        if code != 0:
            say("the setup script {} exited with {}".format(spec["setup"], code))
            return BOX_SETUP_FAILED
        write_json(state_path, {"setup_key": spec["setup_key"], "basis": spec["head"]})
    return 0


def box_run(spec: dict, repo: Path) -> int:
    env = dict(os.environ, PATH=os.pathsep.join([str(repo / entry) for entry in spec["path"]] +
                                                [os.environ.get("PATH", os.defpath)]))
    argv = [sys.executable, str(Path(__file__).resolve()), "run", "--local",
            *(["--max-workers", str(spec["max_workers"])] if spec["max_workers"] else []),
            spec["profile"], "--", *spec["command"]]
    setpriv = shutil.which("setpriv")
    if setpriv is not None:
        # Some boat boxes start commands with CAP_DAC_READ_SEARCH in the ambient set, which lets them read files that
        # their permissions deny.
        argv = [setpriv, "--ambient-caps=-all", "--inh-caps=-all", "--", *argv]
    try:
        os.chdir(str(repo / spec["cwd"]))
        os.execve(argv[0], argv, env)
    except OSError as error:
        say("cannot start {} on the box: {}".format(spec["profile"], error.strerror or error))
    return CANNOT_EXECUTE


def box_main(step: str, spec_path: Path) -> int:
    spec = json.loads(spec_path.read_text())
    repo = Path.home() / "repo"
    if step == "prepare":
        return box_prepare(spec, spec_path.parent, repo)
    return box_run(spec, repo)


def run(profile_name: str, max_workers: int | None, command: list[str], local: bool = False) -> int:
    config = find_config(Path.cwd())
    data = read_toml(config)
    profile = load_profile(config, data, profile_name)
    settings = load_boat(config, data)
    limit = min(profile.max_workers, max_workers or profile.max_workers)
    outer = os.environ.get(RUN_ENV, "")
    outer_workers = os.environ.get(WORKERS_ENV, "")
    if outer_workers.isdigit() and int(outer_workers) >= 1 and run_is_live(outer):
        return nested(profile, min(limit, int(outer_workers)), command, outer)
    fallback = None
    if settings is not None and not local:
        outcome = boat_run(config, settings, profile, max_workers, command)
        if not isinstance(outcome, Fallback):
            return outcome
        fallback = outcome.code
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
        ("profile", profile.name), ("where", "local"), *([("fallback", fallback)] if fallback else []),
        ("repo", config.parent), ("commit", commit),
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
    if argv[:1] == ["_box"] and len(argv) == 3 and argv[1] in ("prepare", "run"):
        return box_main(argv[1], Path(argv[2]))
    command: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, command = argv[:split], argv[split + 1:]
    parser = Parser(prog="jobgate", description="Admit heavy commands so that concurrent runs fit the machine.")
    actions = parser.add_subparsers(dest="action", required=True)
    run_parser = actions.add_parser("run", usage="jobgate run [--max-workers N] [--local] PROFILE -- COMMAND [ARG...]",
                                    help="wait for room, then run the command with a worker count")
    run_parser.add_argument("--max-workers", type=positive, metavar="N",
                            help="use at most N workers, below the profile's max_workers")
    run_parser.add_argument("--local", action="store_true",
                            help="run on this machine even when jobgate.toml has [boat] settings")
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
        return run(args.profile, args.max_workers, command, args.local)
    except Refusal as refusal:
        say(str(refusal))
        return REFUSED
    except KeyboardInterrupt:
        return 128 + signal.SIGINT
    except Stopped as stopped:
        return 128 + stopped.number


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
