import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from test_run_job import SYSTEM_PYTHON, system_python_before_3_10

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/jobgate.py"
GIB = 2**30
CALM = "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
THRASHING = "some avg10=92.10 avg60=80.00 avg300=40.00 total=1\nfull avg10=35.40 avg60=30.00 avg300=12.00 total=1\n"
BUSY = "some avg10=45.00 avg60=30.00 avg300=12.00 total=1\nfull avg10=2.00 avg60=1.50 avg300=0.50 total=1\n"
HOLD = r'''
import os, sys, time
with open(sys.argv[1] + ".tmp", "w") as stream:
    stream.write(os.environ[sys.argv[3]])
os.replace(sys.argv[1] + ".tmp", sys.argv[1])
while not os.path.exists(sys.argv[2]):
    time.sleep(0.02)
'''
PRINT_WORKERS = "import os; print(os.environ['TEST_WORKERS'])"
NESTED = r'''
import subprocess, sys
for profile, variable in (("test", "TEST_WORKERS"), ("lint", "LINT_WORKERS")):
    output = subprocess.run([sys.executable, sys.argv[1], "run", profile, "--", sys.executable, "-c",
                             "import os; print(os.environ[%r])" % variable],
                            capture_output=True, text=True, timeout=30, check=True).stdout
    print(profile, output.strip())
'''


def wait_for(predicate, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting for {}".format(predicate))


class HeldRun:
    def __init__(self, test, name, profile, flags, variable):
        self.started = test.root / (name + ".started")
        self.release_file = test.root / (name + ".release")
        self.stderr_path = test.root / (name + ".stderr")
        with open(self.stderr_path, "w") as stderr:
            self.process = subprocess.Popen(
                [sys.executable, str(SCRIPT), "run", *flags, profile, "--", sys.executable, "-c", HOLD,
                 str(self.started), str(self.release_file), variable],
                cwd=str(test.repo), env=test.env, stdout=subprocess.DEVNULL, stderr=stderr)

    @property
    def pid(self):
        return self.process.pid

    def stderr(self):
        return self.stderr_path.read_text()

    def workers(self):
        wait_for(self.started.exists)
        return int(self.started.read_text())

    def wait_message(self, text):
        wait_for(lambda: text in self.stderr())
        return self.stderr()

    def release(self):
        self.release_file.touch()
        return self.process.wait(timeout=30)


class JobgateTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="jobgate test ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        systemctl = fake_bin / "systemctl"
        systemctl.write_text("#!/bin/sh\necho 'Failed to connect to bus: No such file or directory' >&2\nexit 1\n")
        systemctl.chmod(0o755)
        self.machine_file = self.root / "machine.json"
        self.set_machine(memory=64 * GIB, cores=8, psi=CALM)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("JOBGATE_", "XDG_", "DBUS_"))}
        self.env.update(
            HOME=str(self.root / "home"), XDG_CACHE_HOME=str(self.root / "cache"),
            XDG_CONFIG_HOME=str(self.root / "config"), XDG_RUNTIME_DIR=str(self.root / "runtime"),
            PATH=str(fake_bin) + os.pathsep + os.environ.get("PATH", ""), JOBGATE_MACHINE_FILE=str(self.machine_file))
        self.held = []
        self.addCleanup(self.stop_held)
        self.profiles(test=("1G", "1G", 4, "TEST_WORKERS"))

    def stop_held(self):
        for held in self.held:
            held.release_file.touch()
            try:
                held.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                held.process.kill()
                held.process.wait()

    def set_machine(self, **readings):
        temporary = self.machine_file.with_name("machine.json.tmp")
        temporary.write_text(json.dumps(readings))
        os.replace(str(temporary), str(self.machine_file))

    def profiles(self, **tables):
        text = ""
        for name, (base, worker, max_workers, variable) in tables.items():
            text += '[{}]\nbase_mem = "{}"\nworker_mem = "{}"\nmax_workers = {}\nworkers_env = "{}"\n\n'.format(
                name, base, worker, max_workers, variable)
        (self.repo / "jobgate.toml").write_text(text)

    def share(self, value):
        config = self.root / "config"
        config.mkdir(exist_ok=True)
        (config / "jobgate.toml").write_text("memory_share = {}\n".format(value))

    def gate(self, *arguments, env=None, timeout=60):
        return subprocess.run([sys.executable, str(SCRIPT), *arguments], cwd=str(self.repo), env=env or self.env,
                              capture_output=True, text=True, timeout=timeout)

    def workers_of(self, *flags, profile="test"):
        result = self.gate("run", *flags, profile, "--", sys.executable, "-c", PRINT_WORKERS)
        self.assertEqual(result.returncode, 0, result.stderr)
        return int(result.stdout)

    def hold(self, name, profile="test", flags=(), variable="TEST_WORKERS"):
        held = HeldRun(self, name, profile, flags, variable)
        self.held.append(held)
        return held


class AdmissionTests(JobgateTest):
    def test_workers_fill_the_machine_share_of_memory(self):
        self.set_machine(memory=8 * GIB, cores=16, psi=CALM)
        self.profiles(test=("1G", "1G", 8, "TEST_WORKERS"))
        self.assertEqual(self.workers_of(), 3)
        self.share(0.75)
        self.assertEqual(self.workers_of(), 5)

    def test_all_runs_together_get_at_most_one_worker_per_core(self):
        self.set_machine(memory=64 * GIB, cores=4, psi=CALM)
        self.profiles(test=("100M", "100M", 8, "TEST_WORKERS"))
        first = self.hold("first")
        self.assertEqual(first.workers(), 4)
        second = self.hold("second")
        self.assertIn("waits for a core", second.wait_message("waits"))
        self.assertFalse(second.started.exists())
        self.assertEqual(first.release(), 0)
        self.assertEqual(second.workers(), 4)
        self.assertEqual(second.release(), 0)

    def test_a_run_starts_with_the_workers_that_fit_and_keeps_them(self):
        self.profiles(test=("100M", "100M", 6, "TEST_WORKERS"))
        first = self.hold("first")
        self.assertEqual(first.workers(), 6)
        second = self.hold("second")
        self.assertEqual(second.workers(), 2)
        self.assertEqual(first.release(), 0)
        self.assertEqual(second.release(), 0)
        self.assertIn("workers=2", second.stderr())

    def test_runs_start_in_arrival_order(self):
        self.set_machine(memory=8 * GIB, cores=16, psi=CALM)
        self.profiles(big=("1G", "1G", 3, "BIG_WORKERS"), small=("512M", "512M", 1, "SMALL_WORKERS"))
        first = self.hold("first", profile="big", flags=("--max-workers", "2"), variable="BIG_WORKERS")
        self.assertEqual(first.workers(), 2)
        second = self.hold("second", profile="big", variable="BIG_WORKERS")
        self.assertIn("waits for memory: 1 worker needs 2.0G", second.wait_message("waits"))
        third = self.hold("third", profile="small", variable="SMALL_WORKERS")
        self.assertIn("waits behind 1 earlier run", third.wait_message("waits"))
        self.assertFalse(third.started.exists())
        self.assertEqual(first.release(), 0)
        self.assertEqual(second.workers(), 3)
        self.assertEqual(second.release(), 0)
        self.assertEqual(third.workers(), 1)
        self.assertEqual(third.release(), 0)

    def test_max_workers_flag_only_lowers_the_profile(self):
        self.assertEqual(self.workers_of("--max-workers", "3"), 3)
        self.assertEqual(self.workers_of("--max-workers", "20"), 4)

    def test_nested_call_runs_inside_the_outer_reservation(self):
        self.set_machine(memory=64 * GIB, cores=4, psi=CALM)
        self.profiles(test=("100M", "100M", 4, "TEST_WORKERS"), lint=("100M", "100M", 2, "LINT_WORKERS"))
        result = self.gate("run", "test", "--", sys.executable, "-c", NESTED, str(SCRIPT))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["test 4", "lint 2"])

    def test_nested_call_with_its_own_cache_folder_stays_inside(self):
        other_cache = self.root / "other cache"
        inner = ("import os, subprocess, sys\n"
                 "env = dict(os.environ, XDG_CACHE_HOME=sys.argv[2])\n"
                 "subprocess.run([sys.executable, sys.argv[1], 'run', 'test', '--', sys.executable, '-c', 'pass'],"
                 " env=env, check=True, timeout=30)\n")
        result = self.gate("run", "test", "--", sys.executable, "-c", inner, str(SCRIPT), str(other_cache))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len((self.root / "cache/jobgate/results.log").read_text().splitlines()), 1)
        self.assertFalse((other_cache / "jobgate/results.log").exists())

    def test_marker_of_an_ended_run_does_not_count_as_nesting(self):
        ended = self.gate("run", "test", "--", sys.executable, "-c", "import os; print(os.environ['JOBGATE_RUN'])")
        self.assertEqual(ended.returncode, 0, ended.stderr)
        env = dict(self.env, JOBGATE_RUN=ended.stdout.strip(), JOBGATE_WORKERS="1")
        result = self.gate("run", "test", "--", sys.executable, "-c", PRINT_WORKERS, env=env)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "4"), result.stderr)
        self.assertNotIn("inside run", result.stderr)

    def test_a_killed_run_frees_its_reservation(self):
        self.set_machine(memory=64 * GIB, cores=4, psi=CALM)
        first = self.hold("first")
        self.assertEqual(first.workers(), 4)
        second = self.hold("second")
        second.wait_message("waits for a core")
        os.kill(first.pid, signal.SIGKILL)
        first.process.wait(timeout=10)
        self.assertEqual(second.workers(), 4)
        self.assertEqual(second.release(), 0)

    def test_memory_pressure_holds_new_runs_until_it_eases(self):
        self.set_machine(memory=64 * GIB, cores=8, psi=THRASHING)
        held = self.hold("first")
        self.assertIn("full avg10=35.40%", held.wait_message("waits for memory pressure"))
        self.assertFalse(held.started.exists())
        self.set_machine(memory=64 * GIB, cores=8, psi=CALM)
        self.assertEqual(held.workers(), 4)
        self.assertEqual(held.release(), 0)

    def test_started_runs_keep_going_under_pressure(self):
        first = self.hold("first", flags=("--max-workers", "1"))
        self.assertEqual(first.workers(), 1)
        self.set_machine(memory=64 * GIB, cores=8, psi=THRASHING)
        second = self.hold("second", flags=("--max-workers", "1"))
        second.wait_message("waits for memory pressure")
        self.assertEqual(first.release(), 0)
        self.assertFalse(second.started.exists())
        self.set_machine(memory=64 * GIB, cores=8, psi=CALM)
        self.assertEqual(second.workers(), 1)
        self.assertEqual(second.release(), 0)

    def test_a_busy_machine_does_not_hold_runs(self):
        for reading in ({"psi": BUSY}, {"darwin_level": 2}):
            with self.subTest(reading=reading):
                self.set_machine(memory=64 * GIB, cores=8, **reading)
                result = self.gate("run", "test", "--", sys.executable, "-c", PRINT_WORKERS, timeout=20)
                self.assertEqual((result.returncode, result.stdout.strip()), (0, "4"), result.stderr)
                self.assertNotIn("waits", result.stderr)

    def test_critical_macos_pressure_holds_new_runs(self):
        self.set_machine(memory=64 * GIB, cores=8, darwin_level=4)
        held = self.hold("first")
        self.assertIn("critical", held.wait_message("waits for memory pressure"))
        self.set_machine(memory=64 * GIB, cores=8, darwin_level=1)
        self.assertEqual(held.workers(), 4)
        self.assertEqual(held.release(), 0)

    def test_status_lists_running_and_waiting_runs(self):
        self.set_machine(memory=64 * GIB, cores=4, psi=CALM)
        first = self.hold("first")
        self.assertEqual(first.workers(), 4)
        second = self.hold("second")
        second.wait_message("waits")
        result = self.gate("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        running = [line for line in result.stdout.splitlines() if line.startswith("running")]
        waiting = [line for line in result.stdout.splitlines() if line.startswith("waiting")]
        self.assertEqual(len(running), 1)
        self.assertIn("pid {}, 4 workers".format(first.pid), running[0])
        self.assertEqual(len(waiting), 1)
        self.assertIn("pid {}".format(second.pid), waiting[0])


class ResultTests(JobgateTest):
    def git(self, *arguments):
        return subprocess.run(["git", "-C", str(self.repo), *arguments], check=True, capture_output=True,
                              text=True, env=dict(self.env, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                                                  GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")).stdout

    def result_line(self, stderr):
        lines = [line[len("jobgate: "):] for line in stderr.splitlines() if line.startswith("jobgate: time=")]
        self.assertEqual(len(lines), 1, stderr)
        return lines[0], dict(field.split("=", 1) for field in shlex.split(lines[0]))

    def test_result_line_records_the_commit_and_a_dirty_worktree(self):
        self.git("init", "-q")
        (self.repo / "app.py").write_text("x = 1\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "initial")
        commit = self.git("rev-parse", "HEAD").strip()
        log = self.root / "cache/jobgate/results.log"

        clean = self.gate("run", "test", "--", sys.executable, "-c", PRINT_WORKERS)
        line, fields = self.result_line(clean.stderr)
        self.assertEqual({key: fields[key] for key in ("where", "commit", "dirty", "workers", "exit", "profile")},
                         {"where": "local", "commit": commit, "dirty": "no", "workers": "4", "exit": "0",
                          "profile": "test"})
        self.assertRegex(fields["wait"], r"^\d+s$")
        self.assertEqual(log.read_text().splitlines(), [line])

        (self.repo / "app.py").write_text("x = 2\n")
        dirty = self.gate("run", "test", "--", sys.executable, "-c", PRINT_WORKERS)
        self.assertEqual((dirty.returncode, dirty.stdout.strip()), (0, "4"), dirty.stderr)
        line, fields = self.result_line(dirty.stderr)
        self.assertEqual(fields["dirty"], "yes")
        self.assertEqual(log.read_text().splitlines()[-1], line)

    def test_a_subdirectory_uses_the_repository_root_profile(self):
        self.git("init", "-q")
        subdirectory = self.repo / "tests/unit"
        subdirectory.mkdir(parents=True)
        (subdirectory / "jobgate.toml").write_text("[unrelated]\n")
        result = subprocess.run([sys.executable, str(SCRIPT), "run", "test", "--", sys.executable, "-c", PRINT_WORKERS],
                                cwd=str(subdirectory), env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "4"), result.stderr)

    def test_peak_above_the_stated_memory_names_the_number_to_raise(self):
        self.profiles(test=("1M", "1M", 1, "TEST_WORKERS"))
        allocate = "import time; block = bytearray(96 << 20); block[::4096] = b'x' * len(block[::4096]); time.sleep(3)"
        result = self.gate("run", "test", "--", sys.executable, "-c", allocate)
        self.assertEqual(result.returncode, 0, result.stderr)
        _, fields = self.result_line(result.stderr)
        self.assertGreaterEqual(int(fields["peak"].rstrip("M")), 96)
        self.assertRegex(result.stderr, r'raise worker_mem to "\d+M" in .*jobgate\.toml')


class ExitCodeTests(JobgateTest):
    def test_passes_the_command_exit_code_through(self):
        for code, command in ((0, "pass"), (3, "raise SystemExit(3)"),
                              (128 + signal.SIGKILL, "import os, signal; os.kill(os.getpid(), signal.SIGKILL)")):
            with self.subTest(code=code):
                result = self.gate("run", "test", "--", sys.executable, "-c", command)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn("exit={}".format(code), result.stderr)

    def test_missing_command_exits_127(self):
        result = self.gate("run", "test", "--", str(self.root / "no-such-command"))
        self.assertEqual(result.returncode, 127, result.stderr)

    def test_sigterm_reaches_the_command(self):
        marker = self.root / "terminated"
        trap = ("import pathlib, signal, sys, time\n"
                "def stop(number, frame):\n"
                "    pathlib.Path(sys.argv[1]).write_text('TERM')\n"
                "    sys.exit(7)\n"
                "signal.signal(signal.SIGTERM, stop)\n"
                "pathlib.Path(sys.argv[2]).touch()\n"
                "time.sleep(60)\n")
        ready = self.root / "ready"
        process = subprocess.Popen([sys.executable, str(SCRIPT), "run", "test", "--", sys.executable, "-c", trap,
                                    str(marker), str(ready)], cwd=str(self.repo), env=self.env,
                                   stderr=subprocess.DEVNULL)
        wait_for(ready.exists)
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=30), 7)
        self.assertEqual(marker.read_text(), "TERM")

    def test_configuration_errors_exit_125_and_name_the_fix(self):
        self.set_machine(memory=4 * GIB, cores=8, psi=CALM)
        cases = [
            (("1G", "lots", 4, "TEST_WORKERS"), "worker_mem"),
            (("2G", "1G", 4, "TEST_WORKERS"), "memory_share"),
            (("1M", "1M", 0, "TEST_WORKERS"), "max_workers"),
        ]
        for profile, named in cases:
            with self.subTest(profile=profile):
                self.profiles(test=profile)
                result = self.gate("run", "test", "--", sys.executable, "-c", "raise SystemExit(9)")
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertIn(named, result.stderr)
        result = self.gate("run", "other", "--", sys.executable, "-c", "raise SystemExit(9)")
        self.assertEqual(result.returncode, 125)
        (self.repo / "jobgate.toml").unlink()
        result = self.gate("run", "test", "--", sys.executable, "-c", "raise SystemExit(9)")
        self.assertEqual(result.returncode, 125)
        self.assertIn("no jobgate.toml", result.stderr)

    def test_runs_under_the_system_python_before_3_10(self):
        if not system_python_before_3_10():
            self.skipTest("the system python is 3.10 or newer")
        result = subprocess.run([SYSTEM_PYTHON, str(SCRIPT), "run", "test", "--", SYSTEM_PYTHON, "-c", PRINT_WORKERS],
                                cwd=str(self.repo), env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "4"), result.stderr)


if __name__ == "__main__":
    unittest.main()
