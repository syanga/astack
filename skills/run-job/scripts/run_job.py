#!/usr/bin/env python3
import argparse
import contextlib
from dataclasses import dataclass
import errno
import math
import os
import signal
import subprocess
import sys
import time
from typing import Optional, Union


SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
POLL_SECONDS = 0.05
GRACE_SECONDS = 5


@dataclass(frozen=True)
class JobSpec:
    command: tuple[str, ...]
    timeout: Optional[float]


@dataclass(frozen=True)
class Exited:
    returncode: int


@dataclass(frozen=True)
class TimedOut:
    seconds: float


@dataclass(frozen=True)
class Cancelled:
    signum: int


@dataclass(frozen=True)
class LaunchFailed:
    errno: int
    message: str


Outcome = Union[Exited, TimedOut, Cancelled, LaunchFailed]


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    cleanup_ok: bool


class Latch:
    def __init__(self):
        self.signum = None

    def __call__(self, signum, frame):
        if self.signum is None:
            self.signum = signum


@contextlib.contextmanager
def latching_signals():
    active = signal.getsignal(signal.SIGINT)
    if isinstance(active, Latch):
        yield active
        return
    latch = Latch()
    previous = {signum: signal.signal(signum, latch) for signum in SIGNALS}
    try:
        yield latch
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def seconds(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError('must be a finite number of seconds greater than 0')
    return value


def add_arguments(parser: argparse.ArgumentParser) -> None:
    limit = parser.add_mutually_exclusive_group(required=True)
    limit.add_argument('--timeout', type=seconds, metavar='SECONDS',
                       help='stop the process group after this many seconds')
    limit.add_argument('--no-timeout', action='store_true', help='run a long-lived process without a deadline')
    parser.add_argument('command', nargs='+', metavar='COMMAND', help='command and arguments, after --')


def spec_from(args: argparse.Namespace) -> JobSpec:
    return JobSpec(tuple(args.command), None if args.no_timeout else args.timeout)


def send(pgid, signum):
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signum)


def live_members(pgid: int) -> int:
    listing = subprocess.run(['ps', '-A', '-o', 'pid=,pgid=,stat='], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=5, check=True).stdout
    rows = (line.split() for line in listing.splitlines())
    return sum(1 for row in rows if len(row) >= 3 and row[1] == str(pgid) and not row[2].startswith('Z'))


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    try:
        return live_members(pgid) > 0
    except (OSError, subprocess.SubprocessError):
        return True


def wait_empty(process):
    deadline = time.monotonic() + GRACE_SECONDS
    while True:
        process.poll()
        if not group_alive(process.pid):
            process.wait()
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_SECONDS)


def stop_group(process, outcome):
    if isinstance(outcome, Exited):
        if not group_alive(process.pid):
            return True
        print('run-job: stopped leftover processes', file=sys.stderr)
    send(process.pid, outcome.signum if isinstance(outcome, Cancelled) else signal.SIGTERM)
    if wait_empty(process):
        return True
    send(process.pid, signal.SIGKILL)
    if wait_empty(process):
        return True
    print('run-job: could not stop process group {}'.format(process.pid), file=sys.stderr)
    return False


def supervise(spec: JobSpec, stdout=None, stderr=None, on_start=None) -> Result:
    with latching_signals() as latch:
        try:
            process = subprocess.Popen(spec.command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       start_new_session=True)
        except OSError as error:
            return Result(LaunchFailed(error.errno, error.strerror), True)
        deadline = None if spec.timeout is None else time.monotonic() + spec.timeout
        outcome = None
        try:
            if on_start is not None:
                on_start(process)
            while outcome is None:
                if latch.signum is not None:
                    outcome = Cancelled(latch.signum)
                elif process.poll() is not None:
                    outcome = Exited(process.returncode)
                elif deadline is not None and time.monotonic() >= deadline:
                    outcome = TimedOut(spec.timeout)
                else:
                    time.sleep(POLL_SECONDS)
        finally:
            cleanup_ok = stop_group(process, outcome)
        return Result(outcome, cleanup_ok)


def exit_code(spec, result):
    outcome = result.outcome
    if isinstance(outcome, Exited):
        code = outcome.returncode if outcome.returncode >= 0 else 128 - outcome.returncode
    elif isinstance(outcome, TimedOut):
        stopped = '; stopped the process group' if result.cleanup_ok else ''
        print('run-job: deadline {:g}s reached{}'.format(outcome.seconds, stopped), file=sys.stderr)
        code = 124
    elif isinstance(outcome, Cancelled):
        code = 128 + outcome.signum
    elif isinstance(outcome, LaunchFailed):
        print('run-job: cannot start {}: {}'.format(spec.command[0], outcome.message), file=sys.stderr)
        code = 127 if outcome.errno == errno.ENOENT else 126
    return code if result.cleanup_ok else 125


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog='run_job.py', allow_abbrev=False,
        description='Run a command in its own process group and stop the group at a deadline, '
                    'on SIGINT, SIGTERM, or SIGHUP, or when the command exits.')
    add_arguments(parser)
    spec = spec_from(parser.parse_args(argv))
    return exit_code(spec, supervise(spec))


if __name__ == '__main__':
    sys.exit(main())
