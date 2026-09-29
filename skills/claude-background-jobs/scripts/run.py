#!/usr/bin/env python3
import argparse
import math
import os
import signal
import subprocess
import sys


class Cancelled(Exception):
    def __init__(self, signum):
        self.signum = signum


def cancel(signum, frame):
    raise Cancelled(signum)


def stop(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait()


def main():
    parser = argparse.ArgumentParser(
        description='Run a noninteractive job and stop its process group at a deadline.')
    parser.add_argument('--timeout', type=float, required=True, help='job deadline in seconds')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('a command is required after --')
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be positive and finite')

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, cancel)
    process = None
    try:
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as error:
            print(f'run.py: cannot start {command[0]}: {error}', file=sys.stderr)
            return 127
        try:
            code = process.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            stop(process)
            print(f'run.py: timed out after {args.timeout:g}s; stopped the job', file=sys.stderr)
            return 124
        return code if code >= 0 else 128 - code
    except Cancelled as error:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, signal.SIG_IGN)
        if process is not None:
            stop(process)
        return 128 + error.signum


if __name__ == '__main__':
    sys.exit(main())
