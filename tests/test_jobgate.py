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


SYSTEMD_RUN = r"""#!{python}
import os, re, sys
version = {version}
arguments = sys.argv[1:]
split = arguments.index("--")
options, command = arguments[:split], arguments[split + 1:]
if version < 254 and any(option.startswith("--expand-environment") for option in options):
    sys.exit("systemd-run: unrecognized option '--expand-environment=no'")
if version < 253 and "OOMPolicy=continue" in options:
    sys.exit("Unknown assignment: OOMPolicy=continue")
if version >= 258 and "--expand-environment=no" not in options:
    command = [re.sub(r"\$\{{(\w+)\}}|\$(\w+)", lambda match: os.environ.get(match.group(1) or match.group(2), ""), argument)
               for argument in command]
os.execvp(command[0], command)
"""


RECORDING_RUN = r"""#!{python}
import os, sys
arguments = sys.argv[1:]
split = arguments.index("--")
with open(os.environ["SYSTEMD_LOG"], "a") as log:
    log.write("run " + " ".join(argument for argument in arguments[:split] if argument.startswith("--slice=")) + "\n")
os.execvp(arguments[split + 1], arguments[split + 1:])
"""
RECORDING_SYSTEMCTL = """[ "$1" = --version ] && echo "systemd 259"
echo "$*" >> "$SYSTEMD_LOG"
exit 0
"""


def cgroup_has_memory_controller():
    try:
        with open("/proc/self/cgroup") as stream:
            path = next((line[3:].strip() for line in stream if line.startswith("0::")), None)
    except OSError:
        return False
    return path is not None and os.path.exists("/sys/fs/cgroup" + path + "/memory.max")


def wait_for(predicate, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting for {}".format(predicate))


class HeldRun:
    def __init__(self, test, name, profile, flags, variable, env):
        self.started = test.root / (name + ".started")
        self.release_file = test.root / (name + ".release")
        self.stderr_path = test.root / (name + ".stderr")
        with open(self.stderr_path, "w") as stderr:
            self.process = subprocess.Popen(
                [sys.executable, str(SCRIPT), "run", *flags, profile, "--", sys.executable, "-c", HOLD,
                 str(self.started), str(self.release_file), variable],
                cwd=str(test.repo), env=env or test.env, stdout=subprocess.DEVNULL, stderr=stderr)

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
            HOME=str(self.root / "home"), XDG_CACHE_HOME=str(self.root / "xdg cache"),
            XDG_CONFIG_HOME=str(self.root / "xdg config"), XDG_RUNTIME_DIR=str(self.root / "runtime"),
            PATH=str(fake_bin) + os.pathsep + os.environ.get("PATH", ""), JOBGATE_MACHINE_FILE=str(self.machine_file),
            GIT_CEILING_DIRECTORIES=str(self.root), GIT_CONFIG_NOSYSTEM="1")
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
        config = self.root / "home/.config"
        config.mkdir(parents=True, exist_ok=True)
        (config / "jobgate.toml").write_text("memory_share = {}\n".format(value))

    def gate(self, *arguments, env=None, timeout=60):
        return subprocess.run([sys.executable, str(SCRIPT), *arguments], cwd=str(self.repo), env=env or self.env,
                              capture_output=True, text=True, timeout=timeout)

    def workers_of(self, *flags, profile="test"):
        result = self.gate("run", *flags, profile, "--", sys.executable, "-c", PRINT_WORKERS)
        self.assertEqual(result.returncode, 0, result.stderr)
        return int(result.stdout)

    def systemd_session(self, name, systemctl_body, systemd_run):
        session_bin = self.root / name
        session_bin.mkdir()
        (session_bin / "systemctl").write_text("#!/bin/sh\n" + systemctl_body)
        (session_bin / "systemd-run").write_text(systemd_run)
        for tool in ("systemctl", "systemd-run"):
            (session_bin / tool).chmod(0o755)
        return dict(self.env, PATH=str(session_bin) + os.pathsep + self.env["PATH"])

    def hold(self, name, profile="test", flags=(), variable="TEST_WORKERS", env=None):
        held = HeldRun(self, name, profile, flags, variable, env)
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

    def test_a_waiting_run_exits_125_when_the_share_drops_below_its_need(self):
        self.set_machine(memory=8 * GIB, cores=16, psi=CALM)
        self.profiles(test=("1G", "1G", 3, "TEST_WORKERS"))
        first = self.hold("first")
        self.assertEqual(first.workers(), 3)
        second = self.hold("second")
        second.wait_message("waits for memory")
        self.share(0.2)
        self.assertEqual(second.process.wait(timeout=30), 125)
        self.assertIn("[test] needs 2.0G for 1 worker, more than this machine's 1.6G share", second.stderr())
        self.assertFalse(second.started.exists())
        self.assertEqual(first.release(), 0)

    def test_an_invalid_share_file_keeps_waiting_runs_on_the_last_valid_share(self):
        self.set_machine(memory=8 * GIB, cores=16, psi=CALM)
        self.profiles(test=("1G", "1G", 3, "TEST_WORKERS"))
        self.share(0.5)
        first = self.hold("first")
        self.assertEqual(first.workers(), 3)
        second = self.hold("second")
        second.wait_message("waits for memory")
        self.share(2)
        second.wait_message("keeping memory_share 0.5")
        time.sleep(2.5)
        self.assertIsNone(second.process.poll())
        self.assertEqual(second.stderr().count("keeping memory_share"), 1)
        arriving = self.gate("run", "test", "--", sys.executable, "-c", "pass")
        self.assertEqual(arriving.returncode, 125, arriving.stderr)
        self.assertEqual(first.release(), 0)
        self.assertEqual(second.workers(), 3)
        self.assertEqual(second.release(), 0)

    def test_max_workers_flag_only_lowers_the_profile(self):
        self.assertEqual(self.workers_of("--max-workers", "3"), 3)
        self.assertEqual(self.workers_of("--max-workers", "20"), 4)

    def test_nested_call_runs_inside_the_outer_reservation(self):
        self.set_machine(memory=64 * GIB, cores=4, psi=CALM)
        self.profiles(test=("100M", "100M", 4, "TEST_WORKERS"), lint=("100M", "100M", 2, "LINT_WORKERS"))
        result = self.gate("run", "test", "--", sys.executable, "-c", NESTED, str(SCRIPT))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["test 4", "lint 2"])

    def test_nested_call_with_its_own_home_stays_inside(self):
        other_home = self.root / "other home"
        inner = ("import os, subprocess, sys\n"
                 "env = dict(os.environ, HOME=sys.argv[2])\n"
                 "subprocess.run([sys.executable, sys.argv[1], 'run', 'test', '--', sys.executable, '-c', 'pass'],"
                 " env=env, check=True, timeout=30)\n")
        result = self.gate("run", "test", "--", sys.executable, "-c", inner, str(SCRIPT), str(other_home))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len((self.root / "home/.cache/jobgate/results.log").read_text().splitlines()), 1)
        self.assertFalse((other_home / ".cache/jobgate").exists())

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

    def test_the_next_run_removes_scope_files_that_a_killed_run_left(self):
        runs = self.root / "home/.cache/jobgate/runs"
        runs.mkdir(parents=True)
        (runs / "7-0a1b2c3d.scope").write_text('{"cgroup": "/gone", "peak": 1}')
        (runs / "8-0a1b2c3e.scope.tmp").write_text('{"cgroup": "/gone"')
        result = self.gate("run", "test", "--", sys.executable, "-c", "pass")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(runs.iterdir()), [])

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
        log = self.root / "home/.cache/jobgate/results.log"

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

    def test_invalid_utf8_in_a_config_exits_125_without_a_traceback(self):
        text = (self.repo / "jobgate.toml").read_bytes()
        for target in ("repository", "machine"):
            with self.subTest(file=target):
                (self.repo / "jobgate.toml").write_bytes(text + (b"# \xff\n" if target == "repository" else b""))
                machine = self.root / "home/.config/jobgate.toml"
                machine.parent.mkdir(parents=True, exist_ok=True)
                machine.write_bytes(b"memory_share = 0.5\n" + (b"# \xff\n" if target == "machine" else b""))
                result = self.gate("run", "test", "--", sys.executable, "-c", "pass")
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(len(result.stderr.strip().splitlines()), 1, result.stderr)

    def test_paths_that_are_not_utf8_still_run_and_report(self):
        unspaced = tempfile.TemporaryDirectory(prefix="jobgate-")
        self.addCleanup(unspaced.cleanup)
        checkout = Path(unspaced.name) / os.fsdecode(b"checkout\xff")
        try:
            checkout.mkdir()
            (checkout / os.fsdecode(b"notes\xfe")).write_text("x\n")
        except OSError:
            self.skipTest("this file system rejects names that are not UTF-8")
        (checkout / "jobgate.toml").write_text((self.repo / "jobgate.toml").read_text())
        subprocess.run(["git", "init", "-q", str(checkout)], check=True, env=self.env)
        subprocess.run(["git", "-C", str(checkout), "config", "core.quotePath", "false"], check=True, env=self.env)
        show_status = "import subprocess, sys; sys.exit(subprocess.run([sys.executable, {!r}, 'status']).returncode)".format(
            str(SCRIPT))
        result = subprocess.run([sys.executable, str(SCRIPT), "run", "test", "--", sys.executable, "-c", show_status],
                                cwd=str(checkout), env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("running   test", result.stdout)
        _, fields = self.result_line(result.stderr)
        self.assertEqual(fields["dirty"], "yes")
        self.assertEqual(len((self.root / "home/.cache/jobgate/results.log").read_text().splitlines()), 1)

    def test_a_git_root_whose_name_ends_in_a_space_loads_its_profile(self):
        spaced = self.root / "checkout "
        spaced.mkdir()
        (spaced / "jobgate.toml").write_text((self.repo / "jobgate.toml").read_text())
        subprocess.run(["git", "init", "-q", str(spaced)], check=True, env=self.env)
        result = subprocess.run([sys.executable, str(SCRIPT), "run", "test", "--", sys.executable, "-c", PRINT_WORKERS],
                                cwd=str(spaced), env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "4"), result.stderr)

    def test_a_checkout_path_with_braces_loads_its_profile(self):
        braced = self.root / "project {copy}"
        braced.mkdir()
        (braced / "jobgate.toml").write_text((self.repo / "jobgate.toml").read_text())
        result = subprocess.run([sys.executable, str(SCRIPT), "run", "test", "--", sys.executable, "-c", PRINT_WORKERS],
                                cwd=str(braced), env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "4"), result.stderr)

    def test_a_subdirectory_uses_the_repository_root_profile(self):
        self.git("init", "-q")
        subdirectory = self.repo / "tests/unit"
        subdirectory.mkdir(parents=True)
        (subdirectory / "jobgate.toml").write_text("[unrelated]\n")
        result = subprocess.run([sys.executable, str(SCRIPT), "run", "test", "--", sys.executable, "-c", PRINT_WORKERS],
                                cwd=str(subdirectory), env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "4"), result.stderr)

    @unittest.skipUnless(sys.platform == "linux", "systemd scopes exist only on Linux")
    def test_result_line_counts_oom_kills_in_the_scope(self):
        env = self.systemd_session("systemd 259", '[ "$1" = --version ] && echo "systemd 259"\nexit 0\n',
                                   SYSTEMD_RUN.format(python=sys.executable, version=259))
        cgroup = self.root / "scope cgroup"
        cgroup.mkdir()
        (cgroup / "memory.max").write_text("max\n")
        (cgroup / "memory.peak").write_text(str(100 * 2**20))
        for killed in (0, 2):
            with self.subTest(oom_kill=killed):
                (cgroup / "memory.events").write_text("low 0\nhigh 0\nmax 3\noom 1\noom_kill {}\n".format(killed))
                self.set_machine(memory=64 * GIB, cores=8, psi=CALM, cgroup=str(cgroup))
                result = self.gate("run", "test", "--", sys.executable, "-c", "raise SystemExit(137)", env=env)
                self.assertEqual(result.returncode, 137, result.stderr)
                _, fields = self.result_line(result.stderr)
                self.assertEqual(fields["peak"], "100M")
                self.assertEqual(fields.get("oom_kills"), str(killed) if killed else None)

    def test_peak_above_the_stated_memory_names_the_number_to_raise(self):
        self.profiles(test=("1M", "1M", 1, "TEST_WORKERS"))
        allocate = "import time; block = bytearray(96 << 20); block[::4096] = b'x' * len(block[::4096]); time.sleep(3)"
        result = self.gate("run", "test", "--", sys.executable, "-c", allocate)
        self.assertEqual(result.returncode, 0, result.stderr)
        _, fields = self.result_line(result.stderr)
        peak = int(fields["peak"].rstrip("M"))
        self.assertGreaterEqual(peak, 96)
        self.assertIn('raise worker_mem to "{}M" in {}'.format(peak - 1, self.repo.resolve() / "jobgate.toml"), result.stderr)


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

    def test_command_that_cannot_run_exits_126(self):
        script = self.root / "not-executable"
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o644)
        for command in (str(script), str(self.root)):
            with self.subTest(command=command):
                result = self.gate("run", "test", "--", command)
                self.assertEqual(result.returncode, 126, result.stderr)
                self.assertIn("exit=126", result.stderr)

    @unittest.skipUnless(sys.platform == "linux", "the kernel memory cap exists only on Linux")
    def test_linux_without_a_systemd_session_says_the_cap_is_off(self):
        result = self.gate("run", "test", "--", sys.executable, "-c", PRINT_WORKERS)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "4"), result.stderr)
        self.assertEqual(result.stderr.count("the kernel memory cap is off"), 1, result.stderr)

    @unittest.skipUnless(sys.platform == "linux" and cgroup_has_memory_controller(),
                         "needs Linux and a cgroup with the memory controller for the scope helper")
    def test_scope_passes_dollar_arguments_unchanged_on_each_systemd(self):
        for version in (252, 259):
            with self.subTest(systemd=version):
                env = self.systemd_session("systemd {}".format(version),
                                           '[ "$1" = --version ] && echo "systemd {}"\nexit 0\n'.format(version),
                                           SYSTEMD_RUN.format(python=sys.executable, version=version))
                result = self.gate("run", "test", "--", sys.executable, "-c", "import sys; print(sys.argv[1:])",
                                   "${HOME}", "$HOME", env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "['${HOME}', '$HOME']")
                self.assertNotIn("kernel memory cap is off", result.stderr)

    @unittest.skipUnless(sys.platform == "linux", "systemd slices exist only on Linux")
    def test_each_home_caps_its_own_slice(self):
        log = self.root / "systemd.log"
        env = self.systemd_session("recording systemd", RECORDING_SYSTEMCTL, RECORDING_RUN.format(python=sys.executable))
        env["SYSTEMD_LOG"] = str(log)
        self.set_machine(memory=8 * GIB, cores=8, psi=CALM)
        slices = []
        for name, memory_share in (("first home", 0.5), ("second home", 0.25)):
            (self.root / name / ".config").mkdir(parents=True)
            (self.root / name / ".config/jobgate.toml").write_text("memory_share = {}\n".format(memory_share))
            result = self.gate("run", "test", "--", sys.executable, "-c", "pass", env=dict(env, HOME=str(self.root / name)))
            self.assertEqual(result.returncode, 0, result.stderr)
            slices.append([line.split("--slice=")[1] for line in log.read_text().splitlines() if line.startswith("run ")][-1])
        units = {}
        for line in log.read_text().splitlines():
            words = line.split()
            if words[:3] == ["--user", "set-property", "--runtime"]:
                units.setdefault(words[3], {}).update(word.split("=", 1) for word in words[4:])
        self.assertEqual(len(set(slices)), 2)
        self.assertEqual([units[unit].get("MemoryMax") for unit in slices], [str(4 * GIB), str(2 * GIB)])
        self.assertEqual(units["jobgate.slice"].get("CPUWeight"), "30")
        self.assertNotIn("MemoryMax", units["jobgate.slice"])

    @unittest.skipUnless(sys.platform == "linux", "systemd slices exist only on Linux")
    def test_raising_the_share_starts_a_waiting_run_and_raises_the_cap(self):
        log = self.root / "systemd.log"
        recording = self.systemd_session("recording systemd", RECORDING_SYSTEMCTL,
                                         RECORDING_RUN.format(python=sys.executable))
        recording["SYSTEMD_LOG"] = str(log)
        self.set_machine(memory=8 * GIB, cores=16, psi=CALM)
        self.profiles(test=("1G", "1G", 3, "TEST_WORKERS"))
        first = self.hold("first")
        self.assertEqual(first.workers(), 3)
        second = self.hold("second", env=recording)
        self.assertIn("waits for memory", second.wait_message("waits"))
        self.share(0.75)
        self.assertEqual(second.workers(), 1)
        caps = [line.rsplit("MemoryMax=", 1)[1] for line in log.read_text().splitlines() if "MemoryMax=" in line]
        self.assertEqual(caps, [str(6 * GIB)])
        self.assertEqual(first.release(), 0)
        self.assertEqual(second.release(), 0)

    @unittest.skipUnless(sys.platform == "linux", "systemd slices exist only on Linux")
    def test_a_home_that_is_not_utf8_gets_a_slice_and_a_status(self):
        home = self.root / os.fsdecode(b"home\xff")
        try:
            home.mkdir()
        except OSError:
            self.skipTest("this file system rejects names that are not UTF-8")
        env = self.systemd_session("recording systemd", RECORDING_SYSTEMCTL, RECORDING_RUN.format(python=sys.executable))
        env.update(HOME=str(home), SYSTEMD_LOG=str(self.root / "systemd.log"))
        run = self.gate("run", "test", "--", sys.executable, "-c", "pass", env=env)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn("Traceback", run.stderr)
        status = self.gate("status", env=env)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertRegex(status.stdout, r"cap +jobgate-[0-9a-f]{12}\.slice ")

    @unittest.skipUnless(sys.platform == "linux", "systemd scopes exist only on Linux")
    def test_ctrl_c_while_the_scope_starts_skips_the_command(self):
        starting = self.root / "scope starting"
        env = self.systemd_session("session bin", "exit 0\n",
                                   '#!/bin/sh\ntouch "{}"\nexec sleep 30\n'.format(starting))
        ran = self.root / "command ran"
        touch = [sys.executable, "-c", "import pathlib, sys; pathlib.Path(sys.argv[1]).touch()", str(ran)]
        self.assertEqual(self.gate("run", "test", "--", *touch).returncode, 0)
        self.assertTrue(ran.exists())
        ran.unlink()
        process = subprocess.Popen([sys.executable, str(SCRIPT), "run", "test", "--", *touch],
                                   cwd=str(self.repo), env=env, stderr=subprocess.PIPE, text=True,
                                   start_new_session=True)
        wait_for(starting.exists)
        os.killpg(process.pid, signal.SIGINT)
        _, stderr = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 128 + signal.SIGINT, stderr)
        self.assertFalse(ran.exists())

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
            (("1M", "1M", 1, "JOBGATE_RUN"), "workers_env"),
            (("1M", "1M", 1, "JOBGATE_WORKERS"), "workers_env"),
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

    def test_a_home_that_jobgate_cannot_write_exits_125_with_one_line(self):
        if os.geteuid() == 0:
            self.skipTest("root can write a read-only directory")
        home = self.root / "read-only home"
        home.mkdir(mode=0o555)
        self.addCleanup(home.chmod, 0o755)
        env = dict(self.env, HOME=str(home))
        for arguments in (("run", "test", "--", sys.executable, "-c", "pass"), ("status",)):
            with self.subTest(command=arguments[0]):
                result = self.gate(*arguments, env=env)
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertEqual(len(result.stderr.strip().splitlines()), 1, result.stderr)
                self.assertIn("cannot create {}".format(home / ".cache"), result.stderr)

    def test_sigterm_or_sighup_while_waiting_exits_128_plus_n(self):
        self.set_machine(memory=64 * GIB, cores=4, psi=CALM)
        first = self.hold("first")
        self.assertEqual(first.workers(), 4)
        for number in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=number):
                waiting = self.hold("waiting {}".format(int(number)))
                waiting.wait_message("waits for a core")
                waiting.process.send_signal(number)
                self.assertEqual(waiting.process.wait(timeout=30), 128 + number)
                self.assertFalse(waiting.started.exists())
        self.assertEqual(first.release(), 0)

    def test_runs_under_the_system_python_before_3_10(self):
        if not system_python_before_3_10():
            self.skipTest("the system python is 3.10 or newer")
        result = subprocess.run([SYSTEM_PYTHON, str(SCRIPT), "run", "test", "--", SYSTEM_PYTHON, "-c", PRINT_WORKERS],
                                cwd=str(self.repo), env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "4"), result.stderr)


if __name__ == "__main__":
    unittest.main()
