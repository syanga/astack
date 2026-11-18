"""Install and revert the Linux memory guard against files in a temporary directory.

A fake systemctl and findmnt stand in for the real ones. No test reads or writes
the real /etc, /sys, or /proc.
"""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import fcntl
import io
import os
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
ESCAPED_TMPFS_LINE = "tmpfs /\\164mp tmpfs defaults 0 0\n"
FSTAB = ROOT_AND_SWAP + TMPFS_LINE
TMP_IN_RAM = "/dev/nvme0n1p2 / ext4 rw,relatime 0 0\ntmpfs /tmp tmpfs rw,noatime,inode64 0 0\n"
TMP_ON_DISK = "/dev/nvme0n1p2 / ext4 rw,relatime 0 0\n"


class FakeSystemd:
    """Answers for tmp.mount as systemd does.

    `masked` and `runtime_masked` are the unit-file masks that `is-enabled`
    reports. `show` reads the manager's view, which a reload refreshes from the
    unit files and /etc/fstab. In that view, a mask wins, then a unit file in
    /etc/systemd/system, then the unit the fstab generator wrote, then a packaged
    unit file. Without any of them, systemd still shows a loaded tmpfs unit for a
    live mount, with no unit file. The generator decodes octal escapes and
    collapses slashes in a mount point, as systemd does. `unlocked` lists the
    changes made while nothing held `lock_path`.
    """

    def __init__(self, fstab, packaged_tmp_mount):
        self.fstab = fstab
        self.packaged_tmp_mount = packaged_tmp_mount
        self.admin_tmp_mount = False
        self.masked = False
        self.runtime_masked = False
        self.findmnt_status = 0
        self.fail_before_change = set()
        self.fail_reload_after = set()
        self.checked = None
        self.changes = []
        self.unsafe = []
        self.unlocked = []
        self.lock_path = None
        self.was_safe = True
        self.reload()

    def reload(self):
        self.generated = self.fstab_mounts_tmpfs()
        self.manager_masked = self.masked
        self.reloaded_fstab = self.fstab.read_text()

    def lock_is_free(self):
        with open(self.lock_path, "a") as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            fcntl.flock(probe, fcntl.LOCK_UN)
            return True

    def mask_by_hand(self):
        self.masked = True
        self.reload()
        self.was_safe = not self.fstab_mounts_tmpfs()

    def fstab_mounts_tmpfs(self):
        for line in self.fstab.read_text().splitlines():
            fields = line.split()
            if len(fields) >= 3 and not line.startswith("#") and fields[2] == "tmpfs":
                where = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), fields[1])
                if (re.sub("/+", "/", where).rstrip("/") or "/") == "/tmp":
                    return True
        return False

    def boot_mounts_tmpfs(self):
        return not self.masked and (self.fstab_mounts_tmpfs() or self.packaged_tmp_mount or self.admin_tmp_mount)

    def check_safety(self, command):
        unsafe = self.masked and self.fstab_mounts_tmpfs()
        if unsafe and self.was_safe:
            self.unsafe.append(command)
        self.was_safe = not unsafe

    def __call__(self, argv):
        if argv[0] == "findmnt":
            self.checked = Path(argv[-1]).read_text()
            return subprocess.CompletedProcess(argv, self.findmnt_status, "", "[E] bad line\n" * self.findmnt_status)
        command = argv[1]
        self.check_safety(command)
        if command in self.fail_before_change:
            return subprocess.CompletedProcess(argv, 1, "", "Failed to {}\n".format(command))
        if command == "is-enabled":
            state = "masked" if self.masked else "masked-runtime" if self.runtime_masked else "static"
            return subprocess.CompletedProcess(argv, 1 if state.startswith("masked") else 0, state + "\n", "")
        if command == "show" and "--property=NeedDaemonReload" in argv:
            pending = "yes" if self.fstab.read_text() != self.reloaded_fstab else "no"
            return subprocess.CompletedProcess(argv, 0, "NeedDaemonReload={}\n".format(pending), "")
        if command == "show":
            if self.manager_masked or self.runtime_masked:
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
            return subprocess.CompletedProcess(argv, 0, output, "")
        if self.lock_path is not None and self.lock_is_free():
            self.unlocked.append(command)
        self.changes.append(command)
        self.masked = {"mask": True, "unmask": False}.get(command, self.masked)
        if command not in self.fail_reload_after:
            self.reload()
        self.check_safety(command)
        if command in self.fail_reload_after:
            return subprocess.CompletedProcess(argv, 1, "", "Failed to reload daemon\n")
        return subprocess.CompletedProcess(argv, 0, "", "")


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
        self.mounts = root / "mounts"
        self.mounts.write_text(TMP_IN_RAM)
        self.lock = root / "memory-guard.lock"
        self.systemd = FakeSystemd(self.fstab, packaged_tmp_mount=True)
        self.systemd.lock_path = self.lock
        self.host = memory_guard.Host(lru_gen_enabled_file=self.enabled, min_ttl_ms_file=self.ttl,
                                      record_file=self.record, fstab_file=self.fstab, mounts_file=self.mounts,
                                      lock_file=self.lock, run=self.systemd)
        self.said = []

    def set_fstab(self, text):
        self.fstab.write_text(text)
        self.systemd.reload()

    def install(self, dry_run=False):
        memory_guard.install(self.host, dry_run=dry_run, say=self.said.append)

    def uninstall(self, dry_run=False):
        memory_guard.uninstall(self.host, dry_run=dry_run, say=self.said.append)

    def assert_untouched(self, fstab=FSTAB):
        self.assertEqual(self.fstab.read_text(), fstab)
        self.assertEqual(self.ttl.read_text().strip(), "0")
        self.assertEqual(sorted(path.name for path in self.etc.iterdir()), ["fstab"])
        self.assertEqual([change for change in self.systemd.changes if change != "daemon-reload"], [])

    def announced_reboot(self):
        return any("Reboot" in line for line in self.said)

    def run_main(self, *argv, platform="linux", uid=0):
        output, error = io.StringIO(), io.StringIO()
        with patch("sys.platform", platform), patch("os.geteuid", return_value=uid), \
                redirect_stdout(output), redirect_stderr(error):
            code = memory_guard.main(list(argv), host=self.host)
        return code, output.getvalue(), error.getvalue()

    def test_install_turns_on_thrashing_prevention_and_keeps_tmpfs_off_tmp_at_boot(self):
        self.install()

        self.assertEqual(self.ttl.read_text().strip(), "1000")
        self.assertIn("w {} - - - - 1000\n".format(self.ttl), self.record.read_text())
        self.assertFalse(self.systemd.boot_mounts_tmpfs())
        self.assertEqual(self.systemd.unsafe, [])
        self.assertEqual(self.systemd.unlocked, [])
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
        fstab = ROOT_AND_SWAP + ESCAPED_TMPFS_LINE
        self.set_fstab(fstab)
        with self.assertRaisesRegex(memory_guard.GuardError, "does not recognize"):
            self.install()

        self.assert_untouched(fstab)

    def test_the_mask_step_refuses_a_tmp_mount_that_still_comes_from_fstab(self):
        fstab = FSTAB + ESCAPED_TMPFS_LINE
        self.set_fstab(fstab)
        self.ttl.write_text("250\n")
        with self.assertRaisesRegex(memory_guard.GuardError, "still mounts /tmp"):
            self.install()
        self.assertFalse(self.systemd.masked)
        self.assertEqual(self.systemd.unsafe, [])

        self.uninstall()
        self.assertEqual(self.fstab.read_text(), fstab)
        self.assertEqual(self.ttl.read_text().strip(), "250")

    def test_install_refuses_a_runtime_mask_that_ends_at_reboot(self):
        self.set_fstab(ROOT_AND_SWAP)
        self.systemd.runtime_masked = True
        self.assertEqual(memory_guard.status(self.host).tmp_at_boot, "RAM")
        with self.assertRaisesRegex(memory_guard.GuardError, "runtime mask"):
            self.install()

        self.assert_untouched(ROOT_AND_SWAP)

    def test_install_and_uninstall_refuse_while_another_one_runs(self):
        with open(self.lock, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaisesRegex(memory_guard.GuardError, "another install or uninstall"):
                self.install()
            self.assert_untouched()

        self.install()
        installed = self.fstab.read_text()
        with open(self.lock, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaisesRegex(memory_guard.GuardError, "another install or uninstall"):
                self.uninstall()
        self.assertEqual(self.fstab.read_text(), installed)
        self.assertTrue(self.systemd.masked)

        self.uninstall()
        self.assertFalse(self.systemd.masked)

    def test_uninstall_changes_nothing_when_systemd_cannot_report_the_mask(self):
        self.install()
        installed = self.fstab.read_text()
        self.systemd.fail_before_change = {"is-enabled"}
        with self.assertRaisesRegex(memory_guard.GuardError, "is-enabled"):
            self.uninstall()

        self.assertEqual(self.fstab.read_text(), installed)
        self.assertTrue(self.systemd.masked)
        self.assertEqual(self.systemd.unsafe, [])

    def test_install_after_an_interrupted_install_reloads_systemd_and_finishes(self):
        self.fstab.write_text(ROOT_AND_SWAP + memory_guard.FSTAB_MARKER + TMPFS_LINE)
        with self.assertRaisesRegex(memory_guard.GuardError, "has not reloaded"):
            self.install(dry_run=True)
        self.install()

        self.assertTrue(self.systemd.masked)
        self.assertFalse(self.systemd.boot_mounts_tmpfs())

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
        self.assertEqual(self.systemd.unlocked, [])
        self.assertEqual(self.backup.read_text(), FSTAB, "uninstall keeps the pre-install backup")

    def test_uninstall_removes_a_mask_that_install_made_without_an_fstab_line(self):
        self.set_fstab(ROOT_AND_SWAP)
        self.install()
        self.install()
        self.assertTrue(self.systemd.masked)
        self.uninstall()

        self.assertFalse(self.systemd.masked)
        self.assertTrue(self.systemd.boot_mounts_tmpfs())

    def test_a_mask_made_before_install_survives_uninstall(self):
        self.set_fstab(ROOT_AND_SWAP)
        self.systemd.mask_by_hand()
        self.install()
        self.assertEqual(self.ttl.read_text().strip(), "1000")
        self.uninstall()

        self.assertEqual(self.ttl.read_text().strip(), "0")
        self.assertTrue(self.systemd.masked)

    def test_a_mask_made_after_a_failed_mask_step_survives_uninstall(self):
        self.set_fstab(ROOT_AND_SWAP)
        self.systemd.fail_before_change = {"mask"}
        with self.assertRaisesRegex(memory_guard.GuardError, "mask"):
            self.install()
        self.systemd.fail_before_change = set()
        self.systemd.mask_by_hand()
        self.uninstall()

        self.assertTrue(self.systemd.masked)
        self.assertEqual(self.ttl.read_text().strip(), "0")
        self.assertFalse(self.record.exists())

    @unittest.skipIf(os.geteuid() == 0, "root can write a read-only file")
    def test_a_mask_made_after_install_failed_before_its_mask_step_survives_uninstall(self):
        self.set_fstab(ROOT_AND_SWAP)
        self.ttl.chmod(0o444)
        with self.assertRaises(PermissionError):
            self.install()
        self.ttl.chmod(0o644)
        self.assertFalse(self.systemd.masked)
        self.systemd.mask_by_hand()
        self.uninstall()

        self.assertTrue(self.systemd.masked)
        self.assertFalse(self.record.exists())

    def test_uninstall_removes_a_mask_that_systemctl_made_before_its_reload_failed(self):
        self.set_fstab(ROOT_AND_SWAP)
        self.systemd.fail_reload_after = {"mask"}
        with self.assertRaisesRegex(memory_guard.GuardError, "mask"):
            self.install()
        self.assertTrue(self.systemd.masked)
        self.assertFalse(self.systemd.manager_masked)
        self.systemd.fail_reload_after = set()
        self.uninstall()

        self.assertFalse(self.systemd.masked)

    def test_a_mask_step_that_finds_no_unit_file_records_no_mask(self):
        self.systemd.packaged_tmp_mount = False
        self.install()
        self.set_fstab(ROOT_AND_SWAP)
        self.systemd.mask_by_hand()
        self.uninstall()

        self.assertTrue(self.systemd.masked)
        self.assertFalse(self.record.exists())

    def test_uninstall_unmasks_rather_than_leave_a_mask_that_fstab_requires(self):
        self.systemd.mask_by_hand()
        self.install()
        self.assertFalse(self.systemd.fstab_mounts_tmpfs())
        self.uninstall()

        self.assertEqual(self.fstab.read_text(), FSTAB)
        self.assertFalse(self.systemd.masked)
        self.assertEqual(self.systemd.unsafe, [])

    def test_status_warns_only_about_a_mask_that_fstab_requires(self):
        self.set_fstab(ROOT_AND_SWAP)
        self.install()
        self.assertTrue(self.systemd.masked)
        self.assertNotIn("tmp.mount is masked", self.run_main("status", uid=1000)[1])

        self.set_fstab(FSTAB)
        code, output, _ = self.run_main("status", uid=1000)
        self.assertEqual(code, 0)
        self.assertIn("tmp.mount is masked", output)

    def test_status_shows_where_tmp_will_be_after_a_reboot(self):
        self.set_fstab(ROOT_AND_SWAP)
        before = self.run_main("status", uid=1000)[1]
        self.assertNotIn("after the next reboot", before)
        self.assertNotIn("set at boot by astack", before)
        self.install()

        after = self.run_main("status", uid=1000)[1]
        self.assertIn("disk after the next reboot", after)
        self.assertIn("set at boot by astack", after)

    def test_status_claims_no_boot_setting_that_install_did_not_make(self):
        self.enabled.write_text("0x0000\n")
        self.install()
        self.enabled.write_text("0x0007\n")

        output = self.run_main("status", uid=1000)[1]
        self.assertIn("min_ttl_ms 0", output)
        self.assertNotIn("set at boot by astack", output)

    def test_status_reports_an_unreadable_record_as_an_error(self):
        self.assertEqual(self.run_main("status", uid=1000)[0], 0)
        self.record.parent.mkdir(parents=True)
        self.record.write_text(memory_guard.RECORD_MARKER + '{"unexpected": 1}\n')

        code, _, error = self.run_main("status", uid=1000)
        self.assertEqual(code, 1)
        self.assertIn(str(self.record), error)

    def test_install_and_uninstall_say_when_a_reboot_is_needed(self):
        self.install()
        self.assertTrue(self.announced_reboot())
        self.said = []
        self.uninstall()
        self.assertFalse(self.announced_reboot(), "/tmp is still in RAM, so it stays there")

        self.install()
        self.mounts.write_text(TMP_ON_DISK)
        self.said = []
        self.uninstall()
        self.assertTrue(self.announced_reboot())

        self.said = []
        self.install()
        self.assertFalse(self.announced_reboot())

    def test_uninstall_announces_no_reboot_when_it_leaves_tmp_where_it_is(self):
        self.mounts.write_text(TMP_ON_DISK)
        self.set_fstab(ROOT_AND_SWAP)
        self.systemd.packaged_tmp_mount = False
        self.install()
        self.said = []
        self.uninstall()

        self.assertIn("REMOVE {}".format(self.record), self.said)
        self.assertFalse(self.announced_reboot())

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
        self.systemd.fail_before_change = {"mask"}
        with self.assertRaisesRegex(memory_guard.GuardError, "mask"):
            self.install()
        self.systemd.fail_before_change = set()
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
        self.assertFalse(any(line.startswith("w ") for line in self.record.read_text().splitlines()))
        self.assertFalse(self.systemd.boot_mounts_tmpfs())

    def test_only_install_and_uninstall_need_root(self):
        for command in ("install", "uninstall"):
            code, _, error = self.run_main(command, uid=1000)
            self.assertEqual(code, 1, command)
            self.assertIn("sudo", error)
        for argv in (("install", "--dry-run"), ("uninstall", "--dry-run"), ("status",)):
            self.assertEqual(self.run_main(*argv, uid=1000)[0], 0, argv)
        self.assert_untouched()

        self.assertEqual(self.run_main("install", uid=0)[0], 0)
        self.assertFalse(self.systemd.boot_mounts_tmpfs())

    def test_install_changes_only_a_linux_machine(self):
        self.assertEqual(self.run_main("install", platform="darwin")[0], 0)
        self.assert_untouched()

        self.assertEqual(self.run_main("install", platform="linux")[0], 0)
        self.assertFalse(self.systemd.boot_mounts_tmpfs())


if __name__ == "__main__":
    unittest.main()
