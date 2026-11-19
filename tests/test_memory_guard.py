from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_run_job import SYSTEM_PYTHON, system_python_before_3_10

SCRIPT = Path(__file__).resolve().parent.parent / "scripts/memory_guard.py"
spec = importlib.util.spec_from_file_location("memory_guard", SCRIPT)
memory_guard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = memory_guard
spec.loader.exec_module(memory_guard)
TMPFILES = shutil.which("systemd-tmpfiles")


class MemoryGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="astack memory guard ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        lru_gen = self.root / "sys/kernel/mm/lru_gen"
        lru_gen.mkdir(parents=True)
        self.enabled = lru_gen / "enabled"
        self.enabled.write_text("0x0007\n")
        self.ttl = lru_gen / "min_ttl_ms"
        self.ttl.write_text("0\n")
        self.host = memory_guard.Host(self.root)
        self.boot_file = self.host.boot_file

    def run_main(self, *argv, platform="linux", uid=0):
        output, error = io.StringIO(), io.StringIO()
        with patch("sys.platform", platform), patch("os.geteuid", return_value=uid), \
                redirect_stdout(output), redirect_stderr(error):
            code = memory_guard.main(list(argv), host=self.host)
        return code, output.getvalue(), error.getvalue()

    def ttl_now(self):
        return self.ttl.read_text().strip()

    def ttl_after_reboot(self):
        if TMPFILES is None:
            self.skipTest("needs systemd-tmpfiles")
        self.ttl.write_text("0\n")
        subprocess.run([TMPFILES, "--root={}".format(self.root), "--boot", "--create",
                        "--prefix=" + memory_guard.MIN_TTL_MS_PATH], check=True, capture_output=True, timeout=30)
        return self.ttl_now()

    def write_rule(self, directory, name, value):
        rule = self.root / directory / name
        rule.parent.mkdir(parents=True, exist_ok=True)
        rule.write_text("w- {} - - - - {}\n".format(memory_guard.MIN_TTL_MS_PATH, value))
        return rule

    def has_preview(self, output, verb, path, value=None):
        return any(line.startswith(verb + " ") and str(path) in line and value in (None, line.split()[-1])
                   for line in output.splitlines())

    def test_install_sets_min_ttl_ms_now_and_at_boot(self):
        self.assertEqual(self.run_main("install")[0], 0)

        self.assertEqual(self.ttl_now(), "1000")
        self.assertEqual(self.ttl_after_reboot(), "1000")

    @unittest.skipIf(TMPFILES is None, "needs systemd-tmpfiles")
    def test_install_refuses_when_another_file_sets_min_ttl_ms_at_boot(self):
        self.ttl.write_text("250\n")
        for directory, name in (("usr/lib/tmpfiles.d", "00-distro.conf"), ("etc/tmpfiles.d", "mglru.conf")):
            with self.subTest(name=name):
                rule = self.write_rule(directory, name, 2000)
                code, _, error = self.run_main("install")
                rule.unlink()
                self.assertEqual(code, 1)
                self.assertIn("2000 in {}".format(rule), error)
                self.assertEqual(self.ttl_now(), "250")
                self.assertFalse(self.boot_file.exists())

        self.assertEqual(self.run_main("install")[0], 0)
        self.assertEqual(self.ttl_after_reboot(), "1000")

    def test_uninstall_restores_the_previous_min_ttl_ms_and_deletes_the_boot_file(self):
        self.ttl.write_text("250\n")
        self.run_main("install")
        self.assertEqual(self.run_main("uninstall")[0], 0)

        self.assertEqual(self.ttl_now(), "250")
        self.assertFalse(self.boot_file.exists())

    def test_a_second_install_keeps_the_value_uninstall_restores(self):
        self.ttl.write_text("250\n")
        self.run_main("install")
        self.assertEqual(self.run_main("install")[0], 0)
        self.assertEqual(self.ttl_now(), "1000")
        self.run_main("uninstall")

        self.assertEqual(self.ttl_now(), "250")

    def test_install_refuses_without_multi_gen_lru(self):
        self.enabled.write_text("0x0000\n")
        code, _, error = self.run_main("install")
        self.assertEqual(code, 1)
        self.assertIn("multi-gen LRU", error)
        self.assertEqual(self.ttl_now(), "0")
        self.assertFalse(self.boot_file.exists())

        self.ttl.unlink()
        self.enabled.unlink()
        self.assertEqual(self.run_main("install")[0], 1)
        self.assertFalse(self.boot_file.exists())

        self.enabled.write_text("0x0007\n")
        self.ttl.write_text("0\n")
        self.assertEqual(self.run_main("install")[0], 0)
        self.assertEqual(self.ttl_now(), "1000")

    def test_uninstall_after_the_kernel_lost_multi_gen_lru_removes_the_boot_setting(self):
        self.run_main("install")
        self.ttl.unlink()
        self.enabled.unlink()

        self.assertEqual(self.run_main("uninstall")[0], 0)
        self.assertFalse(self.boot_file.exists())
        self.assertFalse(self.ttl.exists(), "sysfs cannot create min_ttl_ms, so uninstall must not write it")

    def test_uninstall_without_an_install_changes_nothing(self):
        self.ttl.write_text("250\n")

        self.assertEqual(self.run_main("uninstall")[0], 0)
        self.assertEqual(self.ttl_now(), "250")
        self.assertFalse(self.boot_file.exists())

        self.run_main("install")
        self.ttl.write_text("500\n")
        self.assertEqual(self.run_main("uninstall")[0], 0)
        self.assertEqual(self.ttl_now(), "250")
        self.assertFalse(self.boot_file.exists())

    @unittest.skipIf(os.geteuid() == 0, "root can write a read-only file")
    def test_uninstall_reverts_an_install_that_failed_partway(self):
        self.ttl.write_text("250\n")
        self.ttl.chmod(0o444)
        self.assertEqual(self.run_main("install")[0], 1)
        self.ttl.chmod(0o644)
        self.ttl.write_text("500\n")
        self.run_main("uninstall")

        self.assertEqual(self.ttl_now(), "250")
        self.assertFalse(self.boot_file.exists())

    def test_dry_runs_name_the_changes_and_make_none(self):
        code, output, _ = self.run_main("install", "--dry-run", uid=1000)
        self.assertEqual(code, 0)
        self.assertTrue(self.has_preview(output, "SET", self.ttl, "1000"), output)
        self.assertTrue(self.has_preview(output, "WRITE", self.boot_file), output)
        self.assertEqual(self.ttl_now(), "0")
        self.assertFalse(self.boot_file.exists())

        self.run_main("install")
        code, output, _ = self.run_main("uninstall", "--dry-run", uid=1000)
        self.assertEqual(code, 0)
        self.assertTrue(self.has_preview(output, "SET", self.ttl, "0"), output)
        self.assertTrue(self.has_preview(output, "REMOVE", self.boot_file), output)
        self.assertEqual(self.ttl_now(), "1000")
        self.assertTrue(self.boot_file.exists())

    def test_status_reports_the_setting_now(self):
        before = self.run_main("status", uid=1000)[1]
        self.assertIn("off: min_ttl_ms 0", before)
        self.assertNotIn("at boot", before)
        self.run_main("install")
        self.assertIn("on: min_ttl_ms 1000", self.run_main("status", uid=1000)[1])

        self.enabled.write_text("0x0000\n")
        self.assertIn("unavailable", self.run_main("status", uid=1000)[1])

    @unittest.skipIf(TMPFILES is None, "needs systemd-tmpfiles")
    def test_status_names_the_file_whose_rule_sets_min_ttl_ms_at_boot(self):
        rule = self.write_rule("etc/tmpfiles.d", "mglru.conf", 300)
        self.assertIn("set to 300 at boot by {}".format(rule), self.run_main("status", uid=1000)[1])
        rule.unlink()

        self.run_main("install")
        self.assertIn("set to 1000 at boot by astack", self.run_main("status", uid=1000)[1])

        rule = self.write_rule("usr/lib/tmpfiles.d", "00-distro.conf", 2000)
        output = self.run_main("status", uid=1000)[1]
        self.assertIn("set to 2000 at boot by {}".format(rule), output)
        self.assertNotIn("by astack", output)
        self.assertEqual(self.ttl_after_reboot(), "2000")

    def test_status_reports_a_boot_file_without_the_previous_value_as_an_error(self):
        self.boot_file.parent.mkdir(parents=True)
        self.boot_file.write_text("w {} - - - - 1000\n".format(memory_guard.MIN_TTL_MS_PATH))

        code, _, error = self.run_main("status", uid=1000)
        self.assertEqual(code, 1)
        self.assertIn(str(self.boot_file), error)

    def test_only_install_and_uninstall_need_root(self):
        for command in ("install", "uninstall"):
            code, _, error = self.run_main(command, uid=1000)
            self.assertEqual(code, 1, command)
            self.assertIn("sudo", error)
        self.assertEqual(self.run_main("status", uid=1000)[0], 0)
        self.assertEqual(self.ttl_now(), "0")

        self.assertEqual(self.run_main("install", uid=0)[0], 0)
        self.assertEqual(self.ttl_now(), "1000")

    def test_install_changes_only_a_linux_machine(self):
        self.assertEqual(self.run_main("install", platform="darwin")[0], 0)
        self.assertEqual(self.ttl_now(), "0")
        self.assertFalse(self.boot_file.exists())

        self.assertEqual(self.run_main("install", platform="linux")[0], 0)
        self.assertEqual(self.ttl_now(), "1000")

    def test_runs_under_the_system_python_before_3_10(self):
        if not system_python_before_3_10():
            self.skipTest("needs {} older than 3.10".format(SYSTEM_PYTHON))
        result = subprocess.run([SYSTEM_PYTHON, str(SCRIPT), "--help"], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("{status,install,uninstall}", result.stdout)


if __name__ == "__main__":
    unittest.main()
