from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
from pathlib import Path
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


def install_guard_as_earlier_versions_did(root, before):
    boot_file = root / "etc/tmpfiles.d/astack-memory-guard.conf"
    boot_file.parent.mkdir(parents=True, exist_ok=True)
    boot_file.write_text(
        "# Written by astack scripts/memory_guard.py. Its uninstall restores min_ttl_ms and deletes this file.\n"
        "# min_ttl_ms before install: {}\n"
        "w /sys/kernel/mm/lru_gen/min_ttl_ms - - - - 1000\n".format(before))
    ttl = root / "sys/kernel/mm/lru_gen/min_ttl_ms"
    if ttl.exists():
        ttl.write_text("1000\n")


class MemoryGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="astack memory guard ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        lru_gen = self.root / "sys/kernel/mm/lru_gen"
        lru_gen.mkdir(parents=True)
        (lru_gen / "enabled").write_text("0x0007\n")
        self.ttl = lru_gen / "min_ttl_ms"
        self.ttl.write_text("0\n")
        self.host = memory_guard.Host(self.root)
        self.boot_file = self.root / "etc/tmpfiles.d/astack-memory-guard.conf"

    def run_main(self, *argv, uid=0):
        output, error = io.StringIO(), io.StringIO()
        with patch("os.geteuid", return_value=uid), redirect_stdout(output), redirect_stderr(error):
            try:
                code = memory_guard.main(list(argv), host=self.host)
            except SystemExit as exit:
                code = exit.code
        return code, output.getvalue(), error.getvalue()

    def ttl_now(self):
        return self.ttl.read_text().strip()

    def has_preview(self, output, verb, path, value=None):
        return any(line.startswith(verb + " ") and str(path) in line and value in (None, line.split()[-1])
                   for line in output.splitlines())

    def test_install_leaves_min_ttl_ms_off_and_writes_no_boot_rule(self):
        code, _, _ = self.run_main("install")

        self.assertNotEqual(code, 0)
        self.assertEqual(self.ttl_now(), "0")
        self.assertFalse(self.boot_file.exists())

    def test_uninstall_restores_the_previous_min_ttl_ms_and_deletes_the_boot_file(self):
        install_guard_as_earlier_versions_did(self.root, before=250)

        self.assertEqual(self.run_main("uninstall")[0], 0)
        self.assertEqual(self.ttl_now(), "250")
        self.assertFalse(self.boot_file.exists())

    def test_uninstall_after_the_kernel_lost_multi_gen_lru_removes_the_boot_setting(self):
        install_guard_as_earlier_versions_did(self.root, before=0)
        self.ttl.unlink()

        self.assertEqual(self.run_main("uninstall")[0], 0)
        self.assertFalse(self.boot_file.exists())
        self.assertFalse(self.ttl.exists(), "sysfs cannot create min_ttl_ms, so uninstall must not write it")

    def test_uninstall_without_the_guard_changes_nothing_and_needs_no_root(self):
        self.ttl.write_text("250\n")

        code, output, _ = self.run_main("uninstall", uid=1000)
        self.assertEqual(code, 0)
        self.assertIn("Nothing to uninstall", output)
        self.assertEqual(self.ttl_now(), "250")
        self.assertFalse(self.boot_file.exists())

    def test_uninstall_of_an_installed_guard_needs_root(self):
        install_guard_as_earlier_versions_did(self.root, before=0)

        code, _, error = self.run_main("uninstall", uid=1000)
        self.assertEqual(code, 1)
        self.assertIn("sudo", error)
        self.assertEqual(self.ttl_now(), "1000")
        self.assertTrue(self.boot_file.exists())

        self.assertEqual(self.run_main("uninstall", uid=0)[0], 0)
        self.assertEqual(self.ttl_now(), "0")

    def test_dry_run_names_the_changes_and_makes_none(self):
        install_guard_as_earlier_versions_did(self.root, before=250)
        installed = self.boot_file.read_bytes()

        code, output, _ = self.run_main("uninstall", "--dry-run", uid=1000)
        self.assertEqual(code, 0)
        self.assertTrue(self.has_preview(output, "SET", self.ttl, "250"), output)
        self.assertTrue(self.has_preview(output, "REMOVE", self.boot_file), output)
        self.assertEqual(self.ttl_now(), "1000")
        self.assertEqual(self.boot_file.read_bytes(), installed)

    def test_uninstall_keeps_a_boot_file_that_does_not_record_the_previous_value(self):
        self.boot_file.parent.mkdir(parents=True)
        self.boot_file.write_text("w /sys/kernel/mm/lru_gen/min_ttl_ms - - - - 1000\n")
        self.ttl.write_text("1000\n")

        code, _, error = self.run_main("uninstall")
        self.assertEqual(code, 1)
        self.assertIn(str(self.boot_file), error)
        self.assertTrue(self.boot_file.exists())
        self.assertEqual(self.ttl_now(), "1000")

    def test_runs_under_the_system_python_before_3_10(self):
        if not system_python_before_3_10():
            self.skipTest("needs {} older than 3.10".format(SYSTEM_PYTHON))
        result = subprocess.run([SYSTEM_PYTHON, str(SCRIPT), "--help"], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("{uninstall}", result.stdout)


if __name__ == "__main__":
    unittest.main()
