"""Install and revert the Linux memory guard against files in a temporary directory.

A fake systemctl and findmnt stand in for the real ones. No test reads or writes
the real /etc, /sys, or /proc.
"""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parent.parent / "scripts/memory_guard.py"
spec = importlib.util.spec_from_file_location("memory_guard", SCRIPT)
memory_guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory_guard)

ROOT_AND_SWAP = (
    "# /etc/fstab: static file system information.\n"
    "UUID=0b2f6a64-4c1e-4f7a-9d35-6e8f2a1c7b90 /              ext4    defaults   0 1\n"
    "/swapfile                                 swap           swap    defaults   0 0\n"
)
TMPFS_LINE = "tmpfs                                     /tmp           tmpfs   defaults,noatime,mode=1777 0 0\n"
FSTAB = ROOT_AND_SWAP + TMPFS_LINE


class FakeSystemd:
    """Answers for tmp.mount as systemd does while /tmp is a mounted tmpfs.

    A mask wins, then a unit file in /etc/systemd/system, then the unit the fstab
    generator wrote at the last reload, then a packaged unit file. Without any of
    them, systemd still shows a loaded tmpfs unit for the live mount, with no
    unit file.
    """

    def __init__(self, fstab, packaged_tmp_mount):
        self.fstab = fstab
        self.packaged_tmp_mount = packaged_tmp_mount
        self.admin_tmp_mount = False
        self.masked = False
        self.generated = self.fstab_mounts_tmpfs()
        self.findmnt_status = 0
        self.failing = set()
        self.checked = None
        self.changes = []
        self.unsafe = []

    def fstab_mounts_tmpfs(self):
        for line in self.fstab.read_text().splitlines():
            fields = line.split()
            if len(fields) >= 3 and not line.startswith("#") and fields[2] == "tmpfs":
                if (re.sub("/+", "/", fields[1]).rstrip("/") or "/") == "/tmp":
                    return True
        return False

    def boot_mounts_tmpfs(self):
        return not self.masked and (self.fstab_mounts_tmpfs() or self.packaged_tmp_mount or self.admin_tmp_mount)

    def __call__(self, argv):
        if argv[0] == "findmnt":
            self.checked = Path(argv[-1]).read_text()
            return subprocess.CompletedProcess(argv, self.findmnt_status, "", "[E] bad line\n" * self.findmnt_status)
        command = argv[1]
        output = ""
        if command == "show":
            if self.masked:
                unit = ("masked", "", "/etc/systemd/system/tmp.mount", "")
            elif self.admin_tmp_mount:
                unit = ("loaded", "tmpfs", "/etc/systemd/system/tmp.mount", "")
            elif self.generated:
                unit = ("loaded", "tmpfs", "/run/systemd/generator/tmp.mount", str(self.fstab))
            elif self.packaged_tmp_mount:
                unit = ("loaded", "tmpfs", "/usr/lib/systemd/system/tmp.mount", "")
            else:
                unit = ("loaded", "tmpfs", "", "")
            output = "LoadState={}\nType={}\nFragmentPath={}\nSourcePath={}\n".format(*unit)
        elif command in self.failing:
            return subprocess.CompletedProcess(argv, 1, "", "Failed to {}\n".format(command))
        else:
            self.changes.append(command)
            self.masked = {"mask": True, "unmask": False}.get(command, self.masked)
            self.generated = self.fstab_mounts_tmpfs()
            if self.masked and self.fstab_mounts_tmpfs():
                self.unsafe.append(command)
        return subprocess.CompletedProcess(argv, 0, output, "")


class MemoryGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="astack memory guard ")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        lru_gen = root / "sys/kernel/mm/lru_gen"
        lru_gen.mkdir(parents=True)
        self.enabled = lru_gen / "enabled"
        self.enabled.write_text("0x0007\n")
        self.ttl = lru_gen / "min_ttl_ms"
        self.ttl.write_text("0\n")
        self.etc = root / "etc"
        self.etc.mkdir()
        self.fstab = self.etc / "fstab"
        self.fstab.write_text(FSTAB)
        self.backup = self.etc / "fstab.astack-backup"
        self.record = self.etc / "tmpfiles.d/astack-memory-guard.conf"
        mounts = root / "mounts"
        mounts.write_text("/dev/nvme0n1p2 / ext4 rw,relatime 0 0\ntmpfs /tmp tmpfs rw,noatime,inode64 0 0\n")
        self.systemd = FakeSystemd(self.fstab, packaged_tmp_mount=True)
        self.host = memory_guard.Host(lru_gen_enabled_file=self.enabled, min_ttl_ms_file=self.ttl,
                                      record_file=self.record, fstab_file=self.fstab, mounts_file=mounts,
                                      run=self.systemd)
        self.said = []

    def set_fstab(self, text):
        self.fstab.write_text(text)
        self.systemd.generated = self.systemd.fstab_mounts_tmpfs()

    def install(self, dry_run=False):
        memory_guard.install(self.host, dry_run=dry_run, say=self.said.append)

    def uninstall(self, dry_run=False):
        memory_guard.uninstall(self.host, dry_run=dry_run, say=self.said.append)

    def assert_untouched(self, fstab=FSTAB):
        self.assertEqual(self.fstab.read_text(), fstab)
        self.assertEqual(self.ttl.read_text().strip(), "0")
        self.assertEqual(sorted(path.name for path in self.etc.iterdir()), ["fstab"])
        self.assertEqual(self.systemd.changes, [])

    def run_main(self, *argv, platform="linux", uid=0):
        output = io.StringIO()
        with patch("sys.platform", platform), patch("os.geteuid", return_value=uid), \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            return memory_guard.main(list(argv), host=self.host), output.getvalue()

    def test_install_turns_on_thrashing_prevention_and_keeps_tmpfs_off_tmp_at_boot(self):
        self.install()

        self.assertEqual(self.ttl.read_text().strip(), "1000")
        self.assertIn("w {} - - - - 1000\n".format(self.ttl), self.record.read_text())
        self.assertFalse(self.systemd.boot_mounts_tmpfs())
        self.assertEqual(self.systemd.unsafe, [])
        lines = self.fstab.read_text().splitlines(keepends=True)
        self.assertEqual("".join(lines[:3]), ROOT_AND_SWAP)
        self.assertTrue(lines[3].startswith("#"), lines[3])
        self.assertEqual(self.systemd.checked, self.fstab.read_text(), "findmnt checks the file that gets installed")
        self.assertEqual(self.backup.read_text(), FSTAB)

    def test_install_masks_a_packaged_tmp_mount_when_fstab_does_not_mount_tmp(self):
        self.set_fstab(ROOT_AND_SWAP)
        self.install()

        self.assertFalse(self.systemd.boot_mounts_tmpfs())
        self.assertEqual(self.fstab.read_text(), ROOT_AND_SWAP)
        self.assertFalse(self.backup.exists())

    def test_install_masks_tmp_mount_only_once_a_unit_file_would_mount_it(self):
        self.systemd.packaged_tmp_mount = False
        self.install()
        self.assertNotIn("mask", self.systemd.changes)
        self.assertFalse(self.systemd.boot_mounts_tmpfs())

        self.systemd.packaged_tmp_mount = True
        self.install()
        self.assertIn("mask", self.systemd.changes)
        self.assertFalse(self.systemd.boot_mounts_tmpfs())

    def test_install_comments_out_a_mount_point_that_systemd_normalizes(self):
        fstab = ROOT_AND_SWAP + "tmpfs /tmp/ tmpfs defaults 0 0\n"
        self.set_fstab(fstab)
        self.install()

        self.assertFalse(self.systemd.boot_mounts_tmpfs())
        self.assertEqual(self.systemd.unsafe, [])
        self.uninstall()
        self.assertEqual(self.fstab.read_text(), fstab)

    def test_install_refuses_an_fstab_line_it_does_not_recognize(self):
        fstab = ROOT_AND_SWAP + "tmpfs /scratch tmpfs defaults 0 0\n"
        self.set_fstab(fstab)
        self.systemd.generated = True
        with self.assertRaisesRegex(memory_guard.GuardError, "does not recognize"):
            self.install()

        self.assert_untouched(fstab)

    def test_install_refuses_a_tmp_mount_unit_file_in_etc(self):
        self.systemd.admin_tmp_mount = True
        with self.assertRaisesRegex(memory_guard.GuardError, "/etc/systemd/system"):
            self.install()

        self.assert_untouched()

    def test_uninstall_restores_fstab_tmp_mount_and_the_previous_min_ttl_ms(self):
        self.ttl.write_text("250\n")
        self.install()
        self.uninstall()

        self.assertEqual(self.fstab.read_text(), FSTAB)
        self.assertEqual(self.ttl.read_text().strip(), "250")
        self.assertFalse(self.record.exists())
        self.assertFalse(self.systemd.masked)
        self.assertTrue(self.systemd.boot_mounts_tmpfs())
        self.assertEqual(self.systemd.unsafe, [])
        self.assertEqual(self.backup.read_text(), FSTAB, "uninstall keeps the pre-install backup")

    def test_a_mask_made_before_install_survives_uninstall(self):
        self.set_fstab(ROOT_AND_SWAP)
        self.systemd.masked = True
        self.install()
        self.assertEqual(self.ttl.read_text().strip(), "1000")
        self.uninstall()

        self.assertEqual(self.ttl.read_text().strip(), "0")
        self.assertTrue(self.systemd.masked)

    def test_uninstall_unmasks_rather_than_leave_a_mask_that_fstab_requires(self):
        self.systemd.masked = True
        self.install()
        self.assertFalse(self.systemd.fstab_mounts_tmpfs())
        self.uninstall()

        self.assertEqual(self.fstab.read_text(), FSTAB)
        self.assertFalse(self.systemd.masked)
        self.assertEqual(self.systemd.unsafe, [])

    def test_status_warns_about_a_mask_that_fstab_requires(self):
        self.assertNotIn("tmp.mount is masked", self.run_main("status", uid=1000)[1])

        self.systemd.masked = True
        code, output = self.run_main("status", uid=1000)
        self.assertEqual(code, 0)
        self.assertIn("tmp.mount is masked", output)

    def test_a_second_install_keeps_what_uninstall_restores(self):
        self.ttl.write_text("250\n")
        self.install()
        self.install()
        self.assertEqual(self.ttl.read_text().strip(), "1000")
        self.assertFalse(self.systemd.boot_mounts_tmpfs())
        self.uninstall()

        self.assertEqual(self.ttl.read_text().strip(), "250")
        self.assertEqual(self.fstab.read_text(), FSTAB)
        self.assertFalse(self.systemd.masked)

    def test_a_first_install_without_multi_gen_lru_does_not_lose_the_value_to_restore(self):
        self.enabled.write_text("0x0000\n")
        self.ttl.write_text("250\n")
        self.install()
        self.enabled.write_text("0x0007\n")
        self.install()
        self.assertEqual(self.ttl.read_text().strip(), "1000")
        self.uninstall()

        self.assertEqual(self.ttl.read_text().strip(), "250")

    def test_uninstall_reverts_an_install_that_failed_partway(self):
        self.ttl.write_text("250\n")
        self.systemd.failing = {"daemon-reload"}
        with self.assertRaisesRegex(memory_guard.GuardError, "daemon-reload"):
            self.install()
        self.systemd.failing = set()
        self.uninstall()

        self.assertEqual(self.fstab.read_text(), FSTAB)
        self.assertEqual(self.ttl.read_text().strip(), "250")
        self.assertFalse(self.record.exists())

    def test_an_fstab_that_findmnt_rejects_changes_nothing(self):
        self.systemd.findmnt_status = 1
        with self.assertRaisesRegex(memory_guard.GuardError, "bad line"):
            self.install()

        self.assert_untouched()
        self.assertTrue(self.systemd.boot_mounts_tmpfs())

    def test_install_dry_run_names_the_changes_and_makes_none(self):
        self.install(dry_run=True)

        self.assert_untouched()
        preview = "\n".join(self.said)
        for change in (str(self.fstab), str(self.record), str(self.ttl), "tmp.mount"):
            self.assertIn(change, preview)

    def test_uninstall_dry_run_names_the_changes_and_makes_none(self):
        self.install()
        installed = self.fstab.read_text(), self.systemd.changes[:]
        self.said = []
        self.uninstall(dry_run=True)

        self.assertEqual((self.fstab.read_text(), self.systemd.changes), installed)
        self.assertEqual(self.ttl.read_text().strip(), "1000")
        self.assertTrue(self.record.exists())
        preview = "\n".join(self.said)
        for change in ("UNMASK tmp.mount", str(self.fstab), str(self.record)):
            self.assertIn(change, preview)

    def test_without_multi_gen_lru_install_still_moves_tmp_to_disk(self):
        self.enabled.write_text("0x0000\n")
        self.install()

        self.assertEqual(self.ttl.read_text().strip(), "0")
        self.assertFalse(self.systemd.boot_mounts_tmpfs())

    def test_only_install_and_uninstall_need_root(self):
        self.assertEqual(self.run_main("install", uid=1000)[0], 1)
        self.assertEqual(self.run_main("install", "--dry-run", uid=1000)[0], 0)
        self.assertEqual(self.run_main("status", uid=1000)[0], 0)
        self.assert_untouched()

        self.assertEqual(self.run_main("install", uid=0)[0], 0)
        self.assertFalse(self.systemd.boot_mounts_tmpfs())
        self.assertIn("disk after the next reboot", self.run_main("status", uid=1000)[1])

    def test_install_changes_only_a_linux_machine(self):
        self.assertEqual(self.run_main("install", platform="darwin")[0], 0)
        self.assert_untouched()

        self.assertEqual(self.run_main("install", platform="linux")[0], 0)
        self.assertFalse(self.systemd.boot_mounts_tmpfs())


if __name__ == "__main__":
    unittest.main()
