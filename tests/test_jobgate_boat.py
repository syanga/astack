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
import threading
import time
import unittest

from test_jobgate import CALM, GIB, SCRIPT, JobgateTest, wait_for

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

    def start_a_held_run(self):
        started = self.root / "box started"
        hold = "import pathlib, sys, time; pathlib.Path(sys.argv[1]).touch(); time.sleep(60)"
        process = subprocess.Popen([sys.executable, str(SCRIPT), "run", "test", "--", sys.executable, "-c", hold,
                                    str(started)], cwd=str(self.repo), env=self.env, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.addCleanup(process.stderr.close)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        wait_for(started.exists)
        return process

    def run_in(self, start):
        return subprocess.run([sys.executable, str(SCRIPT), "run", "test", "--", sys.executable, "-c", REPORT],
                              cwd=str(self.repo / start), env=self.env, capture_output=True, text=True, timeout=60)

    def report_of(self, stdout):
        return dict(line.split(" ", 1) for line in stdout.splitlines() if " " in line)

    def on_box(self, report):
        return report["cwd"].startswith(os.path.realpath(str(self.boat_dir)))

    def boat_logs(self):
        return sorted((Path(self.env["JOBGATE_HOME"]) / ".cache/jobgate/boat").glob("*.log"))

    def printed_logs(self, stderr):
        return [Path(path) for path in re.findall(r"^jobgate: boat log: (.+)$", stderr, re.MULTILINE)]

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

    def test_a_run_without_a_snapshot_logs_boat_failures_but_not_the_snapshot_miss(self):
        self.plan(new=[{"code": "no_ready_machine", "status": 503, "error": "no machine is ready"}])
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--from", self.calls("new")[0])
        logs = self.printed_logs(result.stderr)
        self.assertEqual(len(logs), 1, result.stderr)
        text = logs[0].read_text()
        self.assertIn('"code": "no_ready_machine"', text)
        self.assertNotIn("No saved snapshot", text)

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
                 ('setup = "boat-setup.sh"\nexclude = ["../outside"]\n', "exclude"),
                 ('setup = "boat-setup.sh"\nsetup_inputs = ["a\\u0000b"]\n', "setup_inputs"),
                 ('setup = "boat-setup.sh"\nexclude = ["boat-setup.sh"]\n', "covers boat-setup.sh"),
                 ('setup = "boat-setup.sh"\nsetup_inputs = ["deps/lock"]\nexclude = ["deps"]\n', "covers deps/lock"),
                 ('setup = "boat-setup.sh"\nexclude = ["jobgate.toml"]\n', "covers jobgate.toml")]
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

    def assert_a_copy_keeps_the_run_here(self, private, attributes=""):
        (self.repo / ".gitignore").write_text("private\n")
        (self.repo / ".gitattributes").write_text(attributes)
        self.boat_settings('setup = "boat-setup.sh"\nexclude = ["private"]\n')
        self.commit("ignore private")
        (private / "people.csv").write_bytes(b"name,email\r\nAda,ada@example.com\r\n")
        shutil.copyfile(str(private / "people.csv"), str(self.repo / "copy.csv"))
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / "ran-here").exists())
        self.assertIn("copy.csv", result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "excluded_elsewhere")
        self.assertEqual(self.calls("new"), [])

    def test_a_copy_of_an_ignored_file_under_an_excluded_path_keeps_the_run_here(self):
        (self.repo / "private").mkdir()
        self.assert_a_copy_keeps_the_run_here(self.repo / "private")

    def test_a_copy_that_git_stores_with_other_line_endings_keeps_the_run_here(self):
        (self.repo / "private").mkdir()
        self.assert_a_copy_keeps_the_run_here(self.repo / "private", "* text=auto\n")

    def test_a_copy_of_an_ignored_excluded_file_keeps_the_run_here(self):
        (self.repo / ".gitignore").write_text("secret.csv\n")
        self.boat_settings('setup = "boat-setup.sh"\nexclude = ["secret.csv"]\n')
        self.commit("ignore the secret")
        (self.repo / "secret.csv").write_text("name,email\nAda,ada@example.com\n")
        shutil.copyfile(str(self.repo / "secret.csv"), str(self.repo / "copy.csv"))
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("copy.csv", result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "excluded_elsewhere")
        self.assertEqual(self.calls("new"), [])

    def test_a_symlink_loop_under_an_excluded_path_does_not_stop_the_run(self):
        (self.repo / ".gitignore").write_text("private\n")
        self.boat_settings('setup = "boat-setup.sh"\nexclude = ["private"]\n')
        self.commit("ignore private")
        (self.repo / "private").mkdir()
        (self.repo / "private/people.csv").write_text("name,email\nAda,ada@example.com\n")
        for name in ("here", "again"):
            (self.repo / "private" / name).symlink_to(".")
        (self.repo / "private/itself").symlink_to("itself")
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["where"], "boat", result.stderr)

    def assert_an_unreadable_path_keeps_the_run_here(self, excluded, locked, mode):
        top = excluded.split("/")[0]
        (self.repo / ".gitignore").write_text(top + "\n")
        self.boat_settings('setup = "boat-setup.sh"\nexclude = ["{}"]\n'.format(excluded))
        self.commit("ignore " + top)
        (self.repo / excluded / "raw").mkdir(parents=True)
        private = self.repo / excluded / "raw/people.csv"
        private.write_text("name,email\nAda,ada@example.com\n")
        shutil.copyfile(str(private), str(self.repo / "copy.csv"))
        (self.repo / locked).chmod(mode)
        self.addCleanup((self.repo / locked).chmod, 0o755)
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / "ran-here").exists())
        self.assertIn("Permission denied", result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "local_error")
        self.assertEqual(self.calls("new"), [])

    @unittest.skipIf(os.geteuid() == 0, "root reads directories without permission")
    def test_a_directory_that_jobgate_cannot_list_under_an_excluded_path_keeps_the_run_here(self):
        self.assert_an_unreadable_path_keeps_the_run_here("private", "private/raw", 0o311)

    @unittest.skipIf(os.geteuid() == 0, "root reads directories without permission")
    def test_a_directory_that_jobgate_cannot_enter_under_an_excluded_path_keeps_the_run_here(self):
        self.assert_an_unreadable_path_keeps_the_run_here("private", "private/raw", 0o644)

    @unittest.skipIf(os.geteuid() == 0, "root reads directories without permission")
    def test_an_excluded_path_inside_a_directory_that_jobgate_cannot_enter_keeps_the_run_here(self):
        self.assert_an_unreadable_path_keeps_the_run_here("sealed/private", "sealed", 0o600)

    def test_an_excluded_path_that_does_not_exist_does_not_stop_the_run(self):
        self.boat_settings('setup = "boat-setup.sh"\nexclude = ["absent"]\n')
        self.commit("exclude a path that is not there")
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["where"], "boat", result.stderr)

    def test_private_content_that_a_later_commit_stores_with_lf_endings_keeps_the_run_here(self):
        (self.repo / ".gitattributes").write_text("*.txt text\nprivate.txt -text\n")
        (self.repo / "private.txt").write_bytes(b"name,email\r\nAda,ada@example.com\r\n")
        self.boat_settings('setup = "boat-setup.sh"\nexclude = ["private.txt"]\n')
        self.commit("add private data")
        shutil.copyfile(str(self.repo / "private.txt"), str(self.repo / "copy.txt"))
        self.git("rm", "-q", "private.txt")
        self.commit("move the data to a text path")
        self.assertNotEqual(self.git("rev-parse", "HEAD:copy.txt"), self.git("rev-parse", "HEAD~1:private.txt"))
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("copy.txt", result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "excluded_elsewhere")
        self.assertEqual(self.calls("new"), [])

    def test_private_content_that_a_later_commit_stores_with_crlf_endings_keeps_the_run_here(self):
        (self.repo / ".gitattributes").write_text("copy.txt -text\n")
        (self.repo / "private.txt").write_bytes(b"name,email\nAda,ada@example.com\n")
        self.boat_settings('setup = "boat-setup.sh"\nexclude = ["private.txt"]\n')
        self.commit("add private data")
        (self.repo / "copy.txt").write_bytes(b"name,email\r\nAda,ada@example.com\r\n")
        self.git("rm", "-q", "private.txt")
        self.commit("keep a checked-out copy with CRLF endings")
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("copy.txt", result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "excluded_elsewhere")
        self.assertEqual(self.calls("new"), [])

    def test_a_submodule_under_an_excluded_path_does_not_stop_the_check(self):
        (self.repo / "private/sub").mkdir(parents=True)
        (self.repo / "private/people.csv").write_text("name,email\nAda,ada@example.com\n")
        self.boat_settings('setup = "boat-setup.sh"\nexclude = ["private"]\n')
        self.git("update-index", "--add", "--cacheinfo", "160000,{},private/sub".format("ab" * 20))
        self.commit("add a submodule under private")
        self.assertIn("160000 commit", self.git("ls-tree", "HEAD", "private/sub"))
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["where"], "boat", result.stderr)
        shutil.copyfile(str(self.repo / "private/people.csv"), str(self.repo / "copy.csv"))
        result = self.run_here(REPORT)
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "excluded_elsewhere", result.stderr)

    def test_an_old_git_with_exclude_runs_the_command_here_before_a_box_starts(self):
        real = shutil.which("git", path=self.env["PATH"])
        (self.fake_bin / "git").write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo "git version 2.34.1"; exit 0; fi\n'
                                           'exec "{}" "$@"\n'.format(real))
        (self.fake_bin / "git").chmod(0o755)
        self.boat_settings('setup = "boat-setup.sh"\nexclude = ["private"]\n')
        self.commit("exclude private")
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("exclude needs Git 2.36 or newer, which can filter a bundle. This machine has git version 2.34.1",
                      result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "git")
        self.assertEqual(self.calls("new"), [])

    def test_git_variables_from_the_caller_leave_the_repository_unchanged(self):
        git_dir = self.repo / ".git"
        (self.repo / "private").mkdir()
        (self.repo / "private/people.csv").write_text("name,email\nAda,ada@example.com\n")
        self.boat_settings('setup = "boat-setup.sh"\nexclude = ["private"]\n')
        self.commit("add private data")
        (self.repo / "app.py").write_text("version = 3\n")

        def files():
            return {str(path.relative_to(git_dir)): path.read_bytes() for path in git_dir.rglob("*") if path.is_file()}

        before = files()
        variables = {"GIT_DIR": str(git_dir), "GIT_COMMON_DIR": str(git_dir),
                     "GIT_OBJECT_DIRECTORY": str(git_dir / "objects")}
        result = self.gate("run", "test", "--", sys.executable, "-c", "print(open('app.py').read().strip())",
                           env=dict(self.env, **variables))
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "version = 3"), result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["where"], "boat", result.stderr)
        self.assertEqual(files(), before)

    def test_an_excluded_file_name_with_a_newline_is_checked(self):
        (self.repo / "private").mkdir()
        (self.repo / "private/a\nb.csv").write_text("name,email\nAda,ada@example.com\n")
        self.boat_settings('setup = "boat-setup.sh"\nexclude = ["private"]\n')
        self.commit("add private data with an odd name")
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["where"], "boat", result.stderr)
        shutil.copyfile(str(self.repo / "private/a\nb.csv"), str(self.repo / "copy.csv"))
        result = self.run_here(REPORT)
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "excluded_elsewhere", result.stderr)

    def test_a_sha256_repository_runs_on_the_box(self):
        shutil.rmtree(str(self.repo / ".git"))
        self.git("init", "-q", "--object-format=sha256")
        self.commit("first in sha256")
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.report_of(result.stdout)
        self.assertTrue(self.on_box(report), result.stderr)
        self.assertEqual(report["commits"], "1")

    def test_a_copy_of_a_file_behind_symlinks_under_an_excluded_path_keeps_the_run_here(self):
        outside, nested = self.root / "outside", self.root / "nested"
        outside.mkdir()
        nested.mkdir()
        (self.repo / "private").symlink_to(outside)
        (outside / "data").symlink_to(nested)
        self.assert_a_copy_keeps_the_run_here(nested)

    def test_a_dirty_run_adds_no_file_to_a_split_index_repository(self):
        self.git("config", "core.splitIndex", "true")
        self.git("config", "splitIndex.maxPercentChange", "0")
        self.git("update-index", "--split-index")
        (self.repo / "app.py").write_text("version = 3\n")
        before = sorted(os.listdir(str(self.repo / ".git")))
        result = self.run_here("print(open('app.py').read().strip())")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "version = 3"), result.stderr)
        self.assertEqual(sorted(os.listdir(str(self.repo / ".git"))), before)

    def test_an_edit_that_keeps_the_size_and_time_that_the_index_records_reaches_the_box(self):
        self.git("config", "core.trustctime", "false")
        app = self.repo / "app.py"
        recorded = os.stat(str(app))
        app.write_text("version = 3\n")
        for path in (app, self.repo / ".git/index"):
            os.utime(str(path), ns=(recorded.st_atime_ns, recorded.st_mtime_ns))
        time.sleep(1.1)
        result = self.run_here("print(open('app.py').read().strip())")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "version = 3"), result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["where"], "boat")

    def test_untracked_files_reach_the_box_when_git_status_hides_them(self):
        self.git("config", "status.showUntrackedFiles", "no")
        (self.repo / "new_test.py").write_text("found = True\n")
        result = self.run_here("import os; print(os.path.exists('new_test.py'))")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "True"), result.stderr)
        fields = self.result_fields(result.stderr)
        self.assertEqual((fields["where"], fields["dirty"]), ("boat", "yes"))

    def test_an_untracked_file_with_a_carriage_return_in_its_name_reaches_the_box(self):
        (self.repo / "a\rb.txt").write_text("odd name\n")
        result = self.run_here("import os; print(os.path.exists('a' + chr(13) + 'b.txt'))")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "True"), result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["where"], "boat")

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
        (self.root / "home").mkdir(exist_ok=True)
        (self.root / "home/.gitconfig").write_text("[log]\n\tshowRoot = false\n")
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

    def test_a_change_behind_a_symlinked_setup_input_runs_the_setup_again(self):
        (self.repo / "boat-setup.sh").write_text('od -An -N8 -tx1 /dev/urandom | tr -d " \\n" >> "$HOME/setup-runs"\n'
                                                 'echo >> "$HOME/setup-runs"\n')
        self.boat_settings('setup = "boat-setup.sh"\nsetup_inputs = ["requirements.txt"]\n')
        (self.repo / "deps").mkdir()
        (self.repo / "deps/pinned.txt").write_text("pytest==8\n")
        (self.repo / "requirements.txt").symlink_to("deps/pinned.txt")
        self.commit("pin through a link")
        first = self.run_here(SETUP_RUNS)
        (self.repo / "deps/pinned.txt").write_text("pytest==9\n")
        self.commit("bump behind the link")
        second = self.run_here(SETUP_RUNS)
        self.assertEqual([len(result.stdout.split()) for result in (first, second)], [1, 1], second.stderr)
        self.assertNotEqual(second.stdout, first.stdout)


class FallbackTests(BoatTest):
    def test_runs_here_when_boat_cannot_start_a_box(self):
        cases = [("unauthorized", 401, "not signed in, or your session expired"),
                 ("trial_machine_class_not_allowed", 403, "Large sandboxes are not available during the free trial"),
                 ("rate_limited", 429, "A start limit is full"),
                 ("no_ready_machine", 503, "no machine is ready")]
        for code, status, message in cases:
            with self.subTest(code=code):
                (self.repo / "ran-here").unlink(missing_ok=True)
                self.plan(new=[{"code": code, "status": status, "error": message}])
                result = self.run_here(REPORT)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((self.repo / "ran-here").exists())
                fields = self.result_fields(result.stderr)
                self.assertEqual((fields["where"], fields["fallback"]), ("local", code))
                self.assertIn(message, result.stderr)
                logs = self.printed_logs(result.stderr)
                self.assertEqual(len(logs), 1, result.stderr)
                self.assertIn('"code": "{}"'.format(code), logs[0].read_text())

    def test_a_boat_cli_that_cannot_start_leaves_the_run_here_and_logs_why(self):
        (self.fake_bin / "boat").write_text("#!/nonexistent/interpreter\n")
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / "ran-here").exists())
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "boat_failed")
        logs = self.printed_logs(result.stderr)
        self.assertEqual(len(logs), 1, result.stderr)
        self.assertRegex(logs[0].read_text(), r"boat new --type default .* exited with 127:\ncannot run ")

    def test_a_box_that_boat_creates_but_cannot_start_is_reported_and_replaced(self):
        boot_failure = {"code": "boot_failed", "status": 500, "error": "the box did not boot", "after_created": True,
                        "detail": "kernel panic in the box image"}
        for failures, where, fallback in ((1, "boat", None), (2, "local", "died")):
            with self.subTest(failures=failures):
                self.plan(new=[boot_failure] * failures)
                result = self.run_here(REPORT)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr.count("BOAT RUN FAILED during start on box bx_"), failures, result.stderr)
                self.assertIn("could not start it: the box did not boot", result.stderr)
                log = self.printed_logs(result.stderr)[0].read_text()
                self.assertIn("kernel panic in the box image", log)
                fields = self.result_fields(result.stderr)
                self.assertEqual((fields["where"], fields.get("fallback")), (where, fallback))
                self.assertEqual(self.boxes_left(), [])

    def test_a_refusal_after_boat_creates_the_box_runs_the_command_here(self):
        cases = [{"code": "no_ready_machine", "status": 503, "error": "no machine is ready"},
                 {"code": "billing_required", "status": 402, "error": "The wallet is empty."}]
        for refusal in cases:
            with self.subTest(code=refusal["code"]):
                (self.repo / "ran-here").unlink(missing_ok=True)
                self.plan(new=[dict(refusal, after_created=True)])
                result = self.run_here(REPORT)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((self.repo / "ran-here").exists())
                self.assertEqual(self.result_fields(result.stderr)["fallback"], refusal["code"])
                self.assertNotIn("retries once", result.stderr)
                self.assertEqual(self.boxes_left(), [])

    def test_a_refusal_after_a_snapshot_box_is_deleted_is_not_reported_as_a_failed_box(self):
        self.plan(miss_after_created=[True], new=[{"code": "rate_limited", "status": 429, "error": "A start limit is full"}])
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "rate_limited")
        self.assertNotIn("BOAT RUN FAILED", result.stderr)
        self.assertEqual(self.boxes_left(), [])

    def test_a_box_that_boat_creates_before_it_reports_a_missing_snapshot_is_deleted(self):
        self.plan(miss_after_created=[True])
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["where"], "boat")
        self.assertEqual(self.boxes_left(), [])
        self.assertEqual(len(self.calls("delete")), 2)

    def test_a_command_result_that_boat_reported_is_kept_when_the_box_disappears(self):
        self.plan(drop=["run", "run"], status_gone=[False, True])
        result = self.run_here(REPORT, "7")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertNotIn("BOAT RUN FAILED", result.stderr)
        self.assertRegex(result.stderr, r"box bx_\w+ disappeared after the run step ended, so jobgate may have missed")
        self.assertEqual(len(self.printed_logs(result.stderr)), 1, result.stderr)

    def test_the_disappearance_note_follows_the_command_s_last_output(self):
        self.plan(drop_after_output=["run", "run"], status_gone=[False, True])
        result = self.run_here("import sys; sys.stderr.write('working'); sys.exit(7)")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertRegex(result.stderr, r"working\njobgate: box bx_\w+ disappeared after the run step ended")
        self.assertEqual(len([argv for argv in self.calls("new") if "--from" not in argv]), 1)
        self.assertEqual(self.result_fields(result.stderr)["exit"], "7")

    def test_error_output_without_a_newline_reaches_this_machine_while_the_command_runs(self):
        command = "import sys, time; sys.stderr.write('working' + chr(13)); sys.stderr.flush(); time.sleep(30)"
        process = subprocess.Popen([sys.executable, str(SCRIPT), "run", "test", "--", sys.executable, "-c", command],
                                   cwd=str(self.repo), env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   start_new_session=True)
        self.addCleanup(process.stderr.close)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        seen = []
        threading.Thread(target=lambda: seen.extend(iter(lambda: os.read(process.stderr.fileno(), 4096), b"")),
                         daemon=True).start()
        wait_for(lambda: b"working\r" in b"".join(seen))
        self.assertIsNone(process.poll())
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=60)

    def test_the_box_result_is_read_after_error_output_without_a_newline(self):
        result = self.run_here("import sys; sys.stderr.write('progress without a newline')")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("progress without a newline", result.stderr)
        self.assertEqual(result.stderr.count("jobgate: time="), 1, result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["workers"], "4")

    def test_a_kill_that_boat_says_was_not_out_of_memory_is_retried(self):
        self.plan(oom=["run"], status_not_out_of_memory=[True])
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("retries once on a new box", result.stderr)
        self.assertTrue(self.on_box(self.report_of(result.stdout)), result.stdout)

    def test_the_command_on_the_box_cannot_read_the_result_token(self):
        show = ("import os, pathlib; print(repr(dict(os.environ))); "
                "print(*(path.read_text() for path in pathlib.Path.home().rglob('*.json')))")
        result = self.run_here(show)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["workers"], "4")
        token = re.search(r"jobgate-result (\w+) ", self.boat_logs()[-1].read_text()).group(1)
        self.assertNotIn(token, result.stdout)

    def test_the_box_result_is_read_when_boat_sends_it_in_small_frames(self):
        self.plan(frame_bytes=3)
        result = self.run_here("import sys; sys.stderr.write('progress' + chr(10) + 'tail')")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("progress\ntail\n", result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["workers"], "4")

    def test_error_output_that_quotes_a_result_line_reaches_this_machine_whole(self):
        quoted = "AssertionError: see jobgate: time=1 exit=0 and jobgate-result 00 time=2 in the log"
        result = self.run_here("import sys; sys.stderr.write({!r} + chr(10))".format(quoted))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(quoted + "\n", result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["workers"], "4")

    def test_a_kill_whose_status_omits_the_memory_flag_is_not_retried(self):
        self.plan(oom=["run"], status_without_memory_flag=[True])
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 128 + signal.SIGKILL, result.stderr)
        self.assertFalse((self.repo / "ran-here").exists())
        self.assertIn("boat killed the run step with SIGKILL and did not say why", result.stderr)
        self.assertNotIn("retries once", result.stderr)

    def test_the_upload_carries_only_the_runtime_files_of_jobgate(self):
        copy = self.root / "jobgate copy"
        shutil.copytree(str(SCRIPT.parent), str(copy), ignore=shutil.ignore_patterns("__pycache__"))
        for name in (".env", ".env.py", "local_credentials.py", "tomllib/notes.py"):
            (copy / "_vendor" / name).write_text("TOKEN = 'canary'\n")
        result = subprocess.run([sys.executable, str(copy / SCRIPT.name), "run", "test", "--", sys.executable, "-c",
                                 REPORT], cwd=str(self.repo), env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        uploads = sorted((self.boat_dir / "uploads").glob("*.tgz"))
        with tarfile.open(str(uploads[0])) as archive:
            vendored = [member.name for member in archive.getmembers()
                        if member.name.startswith("_vendor/") and not member.isdir()]
        self.assertEqual(sorted(vendored), ["_vendor/tomllib/__init__.py", "_vendor/tomllib/_parser.py",
                                            "_vendor/tomllib/_re.py", "_vendor/tomllib/_types.py"])

    @unittest.skipIf(os.geteuid() == 0, "root reads files without read permission")
    @unittest.skipIf(sys.version_info < (3, 11), "before 3.11 jobgate imports the vendored parser it needs to read")
    def test_a_file_error_on_this_machine_after_the_box_starts_is_reported_and_left_to_this_machine(self):
        copy = self.root / "jobgate copy"
        shutil.copytree(str(SCRIPT.parent), str(copy), ignore=shutil.ignore_patterns("__pycache__"))
        unreadable = copy / "_vendor/tomllib/_types.py"
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
        off_file.write_text("[]")
        fourth = self.run_here(REPORT)
        self.assertEqual(self.result_fields(fourth.stderr)["where"], "boat", fourth.stderr)

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

    def test_a_command_that_starts_in_a_directory_the_box_would_lack_runs_here_without_a_box(self):
        (self.repo / ".gitignore").write_text("build/\n")
        for name in ("private/people.csv", "src/secret/key.txt"):
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(name + "\n")
        self.boat_settings('setup = "boat-setup.sh"\nexclude = ["private", "src/secret"]\n')
        for name in ("build", "vendored/lib"):
            (self.repo / name).mkdir(parents=True)
        head = self.git("rev-parse", "HEAD").strip()
        self.git("update-index", "--add", "--cacheinfo", "160000,{},vendored/lib".format(head))
        self.commit("add private data and a submodule")
        self.assertIn("160000 commit", self.git("ls-tree", "HEAD", "vendored/lib"))
        for start in ("build", "private", "src", "vendored"):
            with self.subTest(start=start):
                result = self.run_in(start)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((self.repo / start / "ran-here").exists())
                (self.repo / start / "ran-here").unlink()
                self.assertIn("the box would not get {}, which the run needs".format(start), result.stderr)
                self.assertEqual(self.result_fields(result.stderr)["fallback"], "missing_on_box")
                self.assertEqual(self.calls("new"), [])

    def test_a_command_that_starts_next_to_an_excluded_directory_runs_on_the_box(self):
        for name in ("mixed/secret/key.txt", "mixed/kept.txt"):
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(name + "\n")
        self.boat_settings('setup = "boat-setup.sh"\nexclude = ["mixed/secret"]\n')
        self.commit("add a private directory next to kept files")
        result = self.run_in("mixed")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.report_of(result.stdout)
        self.assertTrue(self.on_box(report), report)
        self.assertTrue(report["cwd"].endswith(os.sep + "mixed"), report)

    def test_a_jobgate_toml_that_git_ignores_keeps_the_run_here_without_a_box(self):
        self.git("rm", "-q", "--cached", "jobgate.toml")
        (self.repo / ".gitignore").write_text("jobgate.toml\n")
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / "ran-here").exists())
        self.assertIn("the box would not get jobgate.toml", result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["fallback"], "missing_on_box")
        self.assertEqual(self.calls("new"), [])

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
        logs = self.printed_logs(result.stderr)
        self.assertEqual(len(logs), 2, result.stderr)
        for log in logs:
            text = log.read_text()
            self.assertIn("BOAT RUN FAILED during run", text)
            self.assertIn("the connection to the box closed", text)
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
        self.assertIn("cannot install the toolchain", self.printed_logs(result.stderr)[0].read_text())
        self.assertEqual(self.boxes_left(), [])

    def test_a_box_that_stops_sending_output_is_checked_and_its_output_printed_once(self):
        self.set_machine(memory=64 * GIB, cores=8, psi=CALM, boat_silent_seconds=1)
        self.plan(stall=["run"])
        result = self.run_here("import time; print('first', flush=True); time.sleep(1.5); print('second')")
        self.assertEqual((result.returncode, result.stdout), (0, "first\nsecond\n"), result.stderr)
        self.assertEqual(result.stderr.count("test starts with"), 1, result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["where"], "boat")
        log = self.boat_logs()[-1].read_text()
        self.assertRegex(log, r"printed nothing for 1s, so jobgate checks the step and attaches again\n.*\"attached\"")
        self.assertNotIn("exited with", log)

    def test_a_box_that_keeps_printing_is_not_checked_as_silent(self):
        self.set_machine(memory=64 * GIB, cores=8, psi=CALM, boat_silent_seconds=1)
        result = self.run_here("import time\nfor n in range(5):\n    print(n, flush=True)\n    time.sleep(0.4)")
        self.assertEqual((result.returncode, result.stdout), (0, "0\n1\n2\n3\n4\n"), result.stderr)
        self.assertEqual(len([argv for argv in self.calls("exec") if "--attach" in argv]), 2)

    def test_replayed_output_that_is_not_utf8_is_never_cut(self):
        self.set_machine(memory=64 * GIB, cores=8, psi=CALM, boat_silent_seconds=1)
        self.plan(stall=["run"])
        result = self.run_here("import sys, time; sys.stdout.buffer.write(b'\\xfffirst\\n'); sys.stdout.flush(); "
                               "time.sleep(1.5); print('second')")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[-2:], ["\ufffdfirst", "second"], result.stdout)
        self.assertEqual(set(lines), {"\ufffdfirst", "second"}, result.stdout)

    def test_output_that_is_not_utf8_reaches_this_machine_whole(self):
        result = self.run_here("import sys; sys.stdout.buffer.write(b'abc\\xffdef\\n')")
        self.assertEqual((result.returncode, result.stdout), (0, "abc\ufffddef\n"), result.stderr)

    def test_an_out_of_memory_kill_is_not_retried_when_the_box_answers_late(self):
        self.plan(oom=["run"], status_fail=[True])
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 128 + signal.SIGKILL, result.stderr)
        self.assertFalse((self.repo / "ran-here").exists())
        self.assertRegex(result.stderr, r"BOAT RUN FAILED during run on box bx_\w+: .*ran out of memory")
        self.assertNotIn("retries once", result.stderr)

    def test_a_kill_that_boat_cannot_explain_is_not_retried(self):
        self.plan(oom=["run"], status_gone=[True])
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 128 + signal.SIGKILL, result.stderr)
        self.assertFalse((self.repo / "ran-here").exists())
        self.assertRegex(result.stderr, r"BOAT RUN FAILED during run on box bx_\w+: boat killed the run step with SIGKILL "
                                        r"and did not say why \(the box is gone\)")
        self.assertNotIn("retries once", result.stderr)

    def test_a_kill_followed_by_lost_contact_is_not_retried(self):
        self.set_machine(memory=64 * GIB, cores=8, psi=CALM, boat_lost_seconds=1)
        self.plan(oom=["run"], status_fail=[True] * 10)
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 128 + signal.SIGKILL, result.stderr)
        self.assertFalse((self.repo / "ran-here").exists())
        self.assertIn("did not say why (lost contact with the box for 1s)", result.stderr)

    def test_a_kill_that_boat_cannot_explain_during_setup_is_retried(self):
        self.plan(oom=["setup"], status_gone=[True])
        result = self.run_here(REPORT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("retries once on a new box", result.stderr)
        self.assertEqual(self.result_fields(result.stderr)["where"], "boat")

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

    def test_a_second_stop_while_jobgate_deletes_the_box_still_deletes_it(self):
        self.plan(hold_delete=[2])
        process = self.start_a_held_run()
        os.killpg(process.pid, signal.SIGINT)
        wait_for((self.boat_dir / "delete-held").exists)
        for _ in range(3):
            os.killpg(process.pid, signal.SIGINT)
            time.sleep(0.2)
        _, stderr = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 128 + signal.SIGINT, stderr)
        self.assertIn("jobgate stops after it deletes box", stderr)
        self.assertEqual(len(self.calls("delete")), 1)
        self.assertEqual(self.boxes_left(), [])

    def test_a_box_that_jobgate_cannot_delete_after_a_stop_is_named(self):
        self.plan(delete_fail=[True, True, True])
        process = self.start_a_held_run()
        os.killpg(process.pid, signal.SIGINT)
        wait_for(lambda: len(self.calls("delete")) >= 1)
        os.killpg(process.pid, signal.SIGINT)
        _, stderr = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 128 + signal.SIGINT, stderr)
        self.assertIn("jobgate stops after it deletes box", stderr)
        self.assertRegex(stderr, r"cannot delete box bx_\w+, so boat stops it within 1h 00m: boat could not reach")
        self.assertEqual(len(self.calls("delete")), 3)

    def test_stopping_jobgate_deletes_the_box(self):
        process = self.start_a_held_run()
        os.killpg(process.pid, signal.SIGINT)
        _, stderr = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 128 + signal.SIGINT, stderr)
        self.assertEqual(self.boxes_left(), [])


if __name__ == "__main__":
    unittest.main()
