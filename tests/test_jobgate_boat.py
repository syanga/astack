import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest

from test_jobgate import SCRIPT, JobgateTest, wait_for

FAKE_BOAT = Path(__file__).resolve().parent / "fake_boat.py"
PROFILE = '[test]\nbase_mem = "1G"\nworker_mem = "1G"\nmax_workers = 4\nworkers_env = "TEST_WORKERS"\n'
IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com"}
REPORT = r'''
import os, pathlib, subprocess, sys
pathlib.Path("ran-here").write_text("yes")
print("cwd", os.path.realpath(os.getcwd()))
print("commits", subprocess.run(["git", "rev-list", "--count", "HEAD"], capture_output=True, text=True).stdout.strip())
print("workers", os.environ["TEST_WORKERS"])
sys.exit(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
'''
SETUP_RUNS = "import os; print(open(os.path.expanduser('~/setup-runs')).read().strip())"


class BoatTest(JobgateTest):
    def setUp(self):
        super().setUp()
        self.boat_dir = self.root / "boat"
        self.fake_bin = Path(self.env["PATH"].split(os.pathsep)[0])
        (self.fake_bin / "boat").write_text('#!/bin/sh\nexec "{}" "{}" "$@"\n'.format(sys.executable, FAKE_BOAT))
        (self.fake_bin / "boat").chmod(0o755)
        self.env.update(FAKE_BOAT_DIR=str(self.boat_dir), FAKE_BOAT_BOX_PATH=self.env["PATH"])
        self.git("init", "-q")
        (self.repo / "boat-setup.sh").write_text('echo setup >> "$HOME/setup-runs"\n')
        (self.repo / "app.py").write_text("version = 1\n")
        self.boat_settings('setup = "boat-setup.sh"\n')
        self.commit("first")
        (self.repo / "app.py").write_text("version = 2\n")
        self.commit("second")

    def git(self, *arguments):
        return subprocess.run(["git", "-C", str(self.repo), *arguments], check=True, capture_output=True, text=True,
                              env=dict(self.env, **IDENTITY)).stdout

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def boat_settings(self, table):
        (self.repo / "jobgate.toml").write_text(PROFILE + "\n[boat]\n" + table)

    def plan(self, **queues):
        self.boat_dir.mkdir(exist_ok=True)
        (self.boat_dir / "plan.json").write_text(json.dumps(queues))

    def calls(self, command):
        path = self.boat_dir / "calls.jsonl"
        lines = path.read_text().splitlines() if path.exists() else []
        return [argv for argv in map(json.loads, lines) if argv[0] == command]

    def boxes_left(self):
        boxes = self.boat_dir / "boxes"
        return sorted(path.name for path in boxes.iterdir()) if boxes.is_dir() else []

    def saved_snapshots(self):
        saved = self.boat_dir / "saved"
        return sorted(path.name for path in saved.iterdir()) if saved.is_dir() else []

    def run_here(self, *command):
        return self.gate("run", "test", "--", sys.executable, "-c", *command)

    def report_of(self, stdout):
        return dict(line.split(" ", 1) for line in stdout.splitlines() if " " in line)

    def on_box(self, report):
        return report["cwd"].startswith(os.path.realpath(str(self.boat_dir)))

    def result_fields(self, stderr):
        lines = [line[len("jobgate: "):] for line in stderr.splitlines() if line.startswith("jobgate: time=")]
        self.assertEqual(len(lines), 1, stderr)
        return dict(field.split("=", 1) for field in shlex.split(lines[0]))

    def uploaded_blobs(self):
        blobs = set()
        for upload in sorted((self.boat_dir / "uploads").iterdir()):
            with tarfile.open(str(upload)) as archive:
                if "run.bundle" not in archive.getnames():
                    continue
                data = archive.extractfile("run.bundle").read()
            store = Path(tempfile.mkdtemp(dir=str(self.root)))
            subprocess.run(["git", "init", "-q", "--bare", str(store)], check=True, env=self.env)
            subprocess.run(["git", "-C", str(store), "index-pack", "--stdin"], input=data[data.index(b"\n\n") + 2:],
                           check=True, capture_output=True, env=self.env)
            listing = subprocess.run(["git", "-C", str(store), "cat-file", "--batch-all-objects",
                                      "--batch-check=%(objectname) %(objecttype)"],
                                     check=True, capture_output=True, text=True, env=self.env).stdout
            blobs |= {line.split()[0] for line in listing.splitlines() if line.endswith(" blob")}
        return blobs


class RoutingTests(BoatTest):
    def test_a_repository_with_boat_settings_runs_the_command_on_a_box_once(self):
        result = self.run_here(REPORT, "3")
        self.assertEqual(result.returncode, 3, result.stderr)
        report = self.report_of(result.stdout)
        self.assertTrue(self.on_box(report), report)
        self.assertEqual((report["commits"], report["workers"]), ("2", "4"))
        self.assertFalse((self.repo / "ran-here").exists())
        self.assertEqual(result.stderr.count("runs on boat"), 1, result.stderr)
        fields = self.result_fields(result.stderr)
        self.assertEqual({key: fields[key] for key in ("where", "box", "dirty", "workers", "exit")},
                         {"where": "boat", "box": "default", "dirty": "no", "workers": "4", "exit": "3"})
        self.assertEqual(self.boxes_left(), [])
        for argv in self.calls("new"):
            self.assertIn("--no-env", argv)
            self.assertFalse({"-e", "--env"} & set(argv), argv)

    def test_local_flag_keeps_the_command_here(self):
        result = self.gate("run", "--local", "test", "--", sys.executable, "-c", REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / "ran-here").exists())
        self.assertEqual(self.result_fields(result.stderr)["where"], "local")
        self.assertEqual(self.calls("new"), [])

    def test_the_box_finds_tools_on_path_inside_the_repository_and_starts_in_the_same_subdirectory(self):
        tools = self.repo / "tools/bin"
        tools.mkdir(parents=True)
        (tools / "greet").write_text('#!/bin/sh\necho "hello from $(basename "$(pwd)")"\n')
        (tools / "greet").chmod(0o755)
        (self.repo / "sub").mkdir()
        (self.repo / "sub/keep").write_text("")
        self.commit("add a tool")
        result = subprocess.run([sys.executable, str(SCRIPT), "run", "test", "--", "greet"], cwd=str(self.repo / "sub"),
                                env=dict(self.env, PATH=str(tools) + os.pathsep + self.env["PATH"]),
                                capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "hello from sub"), result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["where"], "boat")

    def test_invalid_boat_settings_exit_125_and_name_the_key(self):
        cases = [('setup = "/etc/setup.sh"\n', "setup"), ('setup = "boat-setup.sh"\ntype = "huge"\n', "type"),
                 ('setup = "boat-setup.sh"\ncolor = "red"\n', "color"), ('type = "large"\n', "setup"),
                 ('setup = "boat-setup.sh"\nexclude = "private"\n', "exclude"),
                 ('setup = "boat-setup.sh"\nexclude = ["../outside"]\n', "exclude")]
        for table, named in cases:
            with self.subTest(table=table):
                self.boat_settings(table)
                result = self.run_here("raise SystemExit(9)")
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertIn(named, result.stderr)
        result = self.gate("run", "boat", "--", sys.executable, "-c", "raise SystemExit(9)")
        self.assertEqual(result.returncode, 125, result.stderr)
        self.assertEqual(self.calls("new"), [])


class SourceTests(BoatTest):
    def test_uncommitted_changes_reach_the_box_and_mark_the_result_dirty(self):
        (self.repo / "app.py").write_text("version = 3\n")
        (self.repo / "new_module.py").write_text("fresh = True\n")
        (self.repo / ".env").write_text("TOKEN=secret\n")
        (self.repo / ".envrc").write_text("export TOKEN=secret\n")
        (self.repo / "notes.txt~").write_text("backup\n")
        before = [self.git(*arguments) for arguments in (("rev-parse", "HEAD"), ("status", "--porcelain"),
                                                          ("diff", "--cached"))]
        later = os.stat(str(self.repo / "boat-setup.sh")).st_mtime + 10
        os.utime(str(self.repo / "boat-setup.sh"), (later, later))
        index = (self.repo / ".git/index").read_bytes()
        show = ("import os; print(open('app.py').read().strip()); "
                "print(*(os.path.exists(name) for name in ('new_module.py', '.env', '.envrc', 'notes.txt~')))")
        result = self.run_here(show)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["version = 3", "True False False False"])
        self.assertIn("left out 3 untracked files that may hold secrets: .env, .envrc, notes.txt~", result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["dirty"], "yes")
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        self.assertEqual([self.git(*arguments) for arguments in (("rev-parse", "HEAD"), ("status", "--porcelain"),
                                                                  ("diff", "--cached"))], before)

    def test_excluded_paths_never_leave_this_machine(self):
        (self.repo / "private").mkdir()
        (self.repo / "private/survey.csv").write_text("name,email\nAda,ada@example.com\n")
        (self.repo / "private/.gitkeep").write_text("")
        (self.repo / "empty.txt").write_text("")
        self.boat_settings('setup = "boat-setup.sh"\nexclude = ["private"]\n')
        self.commit("add private data")
        committed = self.git("rev-parse", "HEAD:private/survey.csv").strip()
        (self.repo / "private/survey.csv").write_text("name,email\nAda,ada@example.com\nBob,bob@example.com\n")
        (self.repo / "private/new.csv").write_text("name,email\nCy,cy@example.com\n")
        private = {committed, *(self.git("hash-object", "private/" + name).strip() for name in ("survey.csv", "new.csv"))}
        show = ("import os; print(os.path.exists('private/survey.csv'), os.path.exists('private/new.csv'), "
                "open('app.py').read().strip())")
        result = self.run_here(show)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "False False version = 2"), result.stderr)
        uploaded = self.uploaded_blobs()
        self.assertIn(self.git("rev-parse", "HEAD:app.py").strip(), uploaded)
        self.assertEqual(uploaded & private, set())

    def test_excluded_contents_that_also_live_at_another_path_keep_the_run_here(self):
        (self.repo / "data").mkdir()
        (self.repo / "data/people.csv").write_text("name,email\nAda,ada@example.com\n")
        self.commit("add data")
        self.git("mv", "data", "private")
        self.boat_settings('setup = "boat-setup.sh"\nexclude = ["private"]\n')
        self.commit("move the data into private")
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / "ran-here").exists())
        self.assertIn("data/people.csv", result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "excluded_elsewhere")
        self.assertEqual(self.calls("new"), [])

    def test_an_uncommitted_copy_of_excluded_content_keeps_the_run_here(self):
        (self.repo / "private").mkdir()
        (self.repo / "private/people.csv").write_text("name,email\nAda,ada@example.com\n")
        self.boat_settings('setup = "boat-setup.sh"\nexclude = ["private"]\n')
        self.commit("add private data")
        (self.repo / "fixtures").mkdir()
        shutil.copyfile(str(self.repo / "private/people.csv"), str(self.repo / "fixtures/copy.csv"))
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / "ran-here").exists())
        self.assertIn("fixtures/copy.csv", result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "excluded_elsewhere")
        self.assertEqual(self.calls("new"), [])

    def test_the_exclude_check_reads_the_root_commit_when_git_log_hides_it(self):
        self.git("checkout", "-q", "--orphan", "fresh")
        for name in ("data", "private"):
            (self.repo / name).mkdir()
            (self.repo / name / "people.csv").write_text("name,email\nAda,ada@example.com\n")
        self.boat_settings('setup = "boat-setup.sh"\nexclude = ["private"]\n')
        self.commit("root with a copy of the data")
        self.git("rm", "-q", "data/people.csv")
        self.commit("drop the copy")
        self.env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="log.showRoot", GIT_CONFIG_VALUE_0="false")
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("data/people.csv", result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "excluded_elsewhere")
        self.assertEqual(self.calls("new"), [])

    def test_a_saved_snapshot_skips_the_setup_until_a_setup_input_changes(self):
        (self.repo / "boat-setup.sh").write_text('od -An -N8 -tx1 /dev/urandom | tr -d " \\n" >> "$HOME/setup-runs"\n'
                                                 'echo >> "$HOME/setup-runs"\n')
        self.boat_settings('setup = "boat-setup.sh"\nsetup_inputs = ["requirements.txt"]\n')
        (self.repo / "requirements.txt").write_text("pytest==8\n")
        self.commit("pin")
        first, second = self.run_here(SETUP_RUNS), self.run_here(SETUP_RUNS)
        self.assertEqual(len(first.stdout.split()), 1, first.stderr)
        self.assertEqual(second.stdout, first.stdout, second.stderr)
        saved = self.saved_snapshots()
        self.assertEqual(len(saved), 1)
        (self.repo / "requirements.txt").write_text("pytest==9\n")
        self.commit("bump")
        third = self.run_here(SETUP_RUNS)
        self.assertEqual((third.returncode, len(third.stdout.split())), (0, 1), third.stderr)
        self.assertNotEqual(third.stdout, second.stdout)
        self.assertEqual(len(self.saved_snapshots()), 1)
        self.assertNotEqual(self.saved_snapshots(), saved)


class FallbackTests(BoatTest):
    def test_runs_here_when_boat_cannot_start_a_box(self):
        cases = [("unauthorized", 401, "not signed in, or your session expired"),
                 ("trial_machine_class_not_allowed", 403, "Large sandboxes are not available during the free trial"),
                 ("rate_limited", 429, "A start limit is full"),
                 ("no_ready_machine", 503, "no machine is ready")]
        for code, status, message in cases:
            with self.subTest(code=code):
                (self.repo / "ran-here").unlink() if (self.repo / "ran-here").exists() else None
                self.plan(new=[{"code": code, "status": status, "error": message}])
                result = self.run_here(REPORT)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((self.repo / "ran-here").exists())
                fields = self.result_fields(result.stderr)
                self.assertEqual((fields["where"], fields["fallback"]), ("local", code))
                self.assertIn(message, result.stderr)

    def test_a_box_that_boat_creates_but_cannot_start_is_reported_deleted_and_left_to_this_machine(self):
        self.plan(new=[{"code": "boot_failed", "status": 500, "error": "the box did not boot", "after_created": True,
                        "detail": "kernel panic in the box image"}])
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / "ran-here").exists())
        self.assertRegex(result.stderr, r"BOAT RUN FAILED during start on box bx_\w+: the box did not boot")
        logs = re.findall(r"^jobgate: boat log: (.+)$", result.stderr, re.MULTILINE)
        log = Path(logs[0]).read_text()
        self.assertIn("the box did not boot", log)
        self.assertIn("kernel panic in the box image", log)
        fields = self.result_fields(result.stderr)
        self.assertEqual((fields["where"], fields["fallback"]), ("local", "boot_failed"))
        self.assertEqual(self.boxes_left(), [])

    @unittest.skipIf(os.geteuid() == 0, "root reads files without read permission")
    def test_a_file_error_on_this_machine_after_the_box_starts_is_reported_and_left_to_this_machine(self):
        copy = self.root / "jobgate copy"
        shutil.copytree(str(SCRIPT.parent), str(copy), ignore=shutil.ignore_patterns("__pycache__"))
        unreadable = copy / "_vendor" / "unreadable"
        unreadable.write_text("")
        unreadable.chmod(0)
        self.addCleanup(unreadable.chmod, 0o644)
        result = subprocess.run([sys.executable, str(copy / SCRIPT.name), "run", "test", "--", sys.executable, "-c",
                                 REPORT], cwd=str(self.repo), env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / "ran-here").exists())
        self.assertRegex(result.stderr, r"BOAT RUN FAILED during upload on box bx_\w+: jobgate on this machine failed")
        self.assertRegex(result.stderr, r"(?m)^jobgate: boat log: ")
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "local_error")
        self.assertEqual(len(self.calls("delete")), 1)
        self.assertEqual(self.boxes_left(), [])

    def test_runs_here_without_the_boat_cli(self):
        (self.fake_bin / "boat").unlink()
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / "ran-here").exists())
        fields = self.result_fields(result.stderr)
        self.assertEqual((fields["where"], fields["fallback"]), ("local", "no_cli"))

    def test_a_payment_error_turns_boat_off_for_an_hour(self):
        self.plan(new=[{"code": "billing_required", "status": 402,
                        "error": "The included time is used and the wallet is empty."}])
        first = self.run_here(REPORT)
        starts = len(self.calls("new"))
        second = self.run_here(REPORT)
        self.assertEqual([self.result_fields(result.stderr)["fallback"] for result in (first, second)],
                         ["billing_required", "billing_required"])
        self.assertIn("boat is off until", second.stderr)
        self.assertEqual(len(self.calls("new")), starts)
        off_file = Path(self.env["JOBGATE_HOME"]) / ".cache/jobgate/boat-off.json"
        off = json.loads(off_file.read_text())
        self.assertAlmostEqual(off["until"] - time.time(), 3600, delta=120)
        off_file.write_text(json.dumps(dict(off, until=time.time() - 1)))
        third = self.run_here(REPORT)
        self.assertEqual(self.result_fields(third.stderr)["where"], "boat", third.stderr)

    def test_a_profile_too_big_for_the_box_is_reported_and_left_to_this_machine(self):
        box_machine = self.root / "box-machine.json"
        box_machine.write_text(json.dumps({"memory": 2 * 1024 ** 3, "cores": 2}))
        self.env["FAKE_BOAT_BOX_MACHINE"] = str(box_machine)
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / "ran-here").exists())
        self.assertRegex(result.stderr, r"needs 2\.0G for 1 worker, more than the 1\.8G that this box gives commands"
                                        r"[\s\S]*BOAT RUN FAILED during setup on box bx_\w+: jobgate on the box refused")
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "box_refused")
        self.assertEqual(len(self.calls("delete")), 1)
        self.assertEqual(self.boxes_left(), [])

    def test_a_command_that_starts_in_a_directory_the_box_lacks_is_reported_and_left_to_this_machine(self):
        (self.repo / ".gitignore").write_text("build/\n")
        self.commit("ignore build")
        (self.repo / "build").mkdir()
        result = subprocess.run([sys.executable, str(SCRIPT), "run", "test", "--", sys.executable, "-c", REPORT],
                                cwd=str(self.repo / "build"), env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / "build/ran-here").exists())
        self.assertRegex(result.stderr, r"the box has no build, where the command starts"
                                        r"[\s\S]*BOAT RUN FAILED during setup on box bx_\w+")
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "box_refused")
        self.assertEqual(self.boxes_left(), [])

    def test_the_setup_script_output_stays_out_of_the_command_output(self):
        (self.repo / "boat-setup.sh").write_text('echo installing the tools\n')
        self.commit("noisy setup")
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.on_box(self.report_of(result.stdout)), result.stdout)
        self.assertNotIn("installing the tools", result.stdout)
        self.assertIn("installing the tools", result.stderr)

    def test_a_file_error_on_this_machine_before_the_box_starts_leaves_the_run_to_this_machine(self):
        template = self.root / "git-template"
        (template / "objects/info/alternates").mkdir(parents=True)
        self.env["GIT_TEMPLATE_DIR"] = str(template)
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / "ran-here").exists())
        self.assertIn("jobgate on this machine failed", result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "local_error")
        self.assertEqual(self.calls("new"), [])

    def test_a_box_that_dies_once_is_replaced_by_a_new_box(self):
        self.plan(vanish=["setup"])
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.on_box(self.report_of(result.stdout)), result.stdout)
        self.assertEqual(result.stderr.count("BOAT RUN FAILED during setup"), 1, result.stderr)
        self.assertIn("retries once on a new box", result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["where"], "boat")
        self.assertEqual(self.boxes_left(), [])

    def test_a_box_that_dies_twice_leaves_the_run_to_this_machine_with_a_log(self):
        self.plan(vanish=["run", "run"])
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / "ran-here").exists())
        self.assertEqual(result.stderr.count("BOAT RUN FAILED during run"), 2, result.stderr)
        logs = re.findall(r"^jobgate: boat log: (.+)$", result.stderr, re.MULTILINE)
        self.assertEqual(len(logs), 2, result.stderr)
        for log in logs:
            self.assertIn("BOAT RUN FAILED during run", Path(log).read_text())
        fields = self.result_fields(result.stderr)
        self.assertEqual((fields["where"], fields["fallback"]), ("local", "died"))
        self.assertEqual(self.boxes_left(), [])

    def test_a_failing_setup_script_is_reported_with_its_output_and_left_to_this_machine(self):
        (self.repo / "boat-setup.sh").write_text("echo cannot install the toolchain >&2\nexit 7\n")
        self.commit("break the setup")
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / "ran-here").exists())
        self.assertEqual(result.stderr.count("BOAT RUN FAILED during setup"), 2, result.stderr)
        self.assertIn("cannot install the toolchain", result.stderr)
        logs = re.findall(r"^jobgate: boat log: (.+)$", result.stderr, re.MULTILINE)
        self.assertIn("cannot install the toolchain", Path(logs[0]).read_text())
        self.assertEqual(self.boxes_left(), [])

    def test_a_command_that_runs_out_of_memory_on_the_box_is_reported_and_not_run_again(self):
        self.plan(oom=["run"])
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 128 + signal.SIGKILL, result.stderr)
        self.assertFalse((self.repo / "ran-here").exists())
        self.assertRegex(result.stderr, r"BOAT RUN FAILED during run on box bx_\w+: .*ran out of memory")
        self.assertEqual(len(self.calls("delete")), 1)
        fields = self.result_fields(result.stderr)
        self.assertEqual((fields["where"], fields["exit"]), ("boat", str(128 + signal.SIGKILL)))
        self.assertEqual(self.boxes_left(), [])

    def test_stopping_jobgate_while_boat_starts_the_box_deletes_the_box(self):
        self.plan(hold_new=[60])
        process = subprocess.Popen([sys.executable, str(SCRIPT), "run", "test", "--", sys.executable, "-c", "pass"],
                                   cwd=str(self.repo), env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        wait_for((self.boat_dir / "new-held").exists)
        os.killpg(process.pid, signal.SIGINT)
        _, stderr = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 128 + signal.SIGINT, stderr)
        self.assertEqual(self.boxes_left(), [])

    def test_stopping_jobgate_deletes_the_box(self):
        started = self.root / "box started"
        hold = "import pathlib, sys, time; pathlib.Path(sys.argv[1]).touch(); time.sleep(60)"
        process = subprocess.Popen([sys.executable, str(SCRIPT), "run", "test", "--", sys.executable, "-c", hold,
                                    str(started)], cwd=str(self.repo), env=self.env, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        wait_for(started.exists)
        os.killpg(process.pid, signal.SIGINT)
        _, stderr = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 128 + signal.SIGINT, stderr)
        self.assertEqual(self.boxes_left(), [])


if __name__ == "__main__":
    unittest.main()
