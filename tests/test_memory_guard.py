"""Install and revert the Linux memory guard against files in a temporary directory.

No test reads or writes the real /etc, /sys, or /proc, and no test runs systemctl or findmnt.
"""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

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
        mounts = root / "mounts"
        mounts.write_text("/dev/nvme0n1p2 / ext4 rw,relatime 0 0\ntmpfs /tmp tmpfs rw,noatime,inode64 0 0\n")
        (root / "tmp").mkdir()
        self.host = memory_guard.Host(lru_gen=lru_gen, tmpfiles=self.etc / "tmpfiles.d/astack-memory-guard.conf",
                                      fstab=self.fstab, mounts=mounts, tmp=root / "tmp", run=self.command)
        self.commands = []
        self.checked = None
        self.findmnt_status = 0
        self.said = []

    def command(self, argv):
        self.commands.append(list(argv))
        if argv[0] == "findmnt":
            self.checked = Path(argv[-1]).read_text()
            return subprocess.CompletedProcess(argv, self.findmnt_status, "", "[E] bad line\n" * self.findmnt_status)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def install(self, dry_run=False):
        memory_guard.install(self.host, dry_run=dry_run, say=self.said.append)

    def uninstall(self):
        memory_guard.uninstall(self.host, dry_run=False, say=self.said.append)

    def test_install_turns_on_the_guard_now_and_at_boot_and_moves_tmp_to_disk_at_reboot(self):
        self.install()

        self.assertEqual(self.ttl.read_text().strip(), "1000")
        self.assertIn("w {} - - - - 1000\n".format(self.ttl), self.host.tmpfiles.read_text())
        after = memory_guard.status(self.host)
        self.assertEqual((after.guard.min_ttl_ms, after.guard.at_boot), (1000, True))
        self.assertEqual((after.tmp.now, after.tmp.at_boot), ("RAM", "disk"))
        lines = self.fstab.read_text().splitlines(keepends=True)
        self.assertEqual("".join(lines[:3]), ROOT_AND_SWAP)
        self.assertTrue(lines[3].startswith("#"), lines[3])
        self.assertEqual(self.checked, self.fstab.read_text(), "findmnt checks the file that gets installed")
        self.assertEqual((self.etc / "fstab.astack-backup").read_text(), FSTAB)
        self.assertIn(["systemctl", "daemon-reload"], self.commands)

    def test_uninstall_restores_fstab_and_the_previous_min_ttl_ms(self):
        self.ttl.write_text("250\n")
        self.install()
        self.uninstall()

        self.assertEqual(self.fstab.read_text(), FSTAB)
        self.assertEqual(self.ttl.read_text().strip(), "250")
        self.assertFalse(self.host.tmpfiles.exists())
        self.assertEqual(memory_guard.status(self.host).tmp.at_boot, "RAM")

    def test_a_second_install_keeps_what_uninstall_restores(self):
        self.ttl.write_text("250\n")
        self.install()
        self.install()
        self.uninstall()

        self.assertEqual(self.ttl.read_text().strip(), "250")
        self.assertEqual(self.fstab.read_text(), FSTAB)

    def test_an_fstab_that_findmnt_rejects_is_not_installed(self):
        self.findmnt_status = 1
        with self.assertRaisesRegex(ValueError, "bad line"):
            self.install()

        self.assertEqual(self.fstab.read_text(), FSTAB)
        self.assertEqual(memory_guard.status(self.host).tmp.at_boot, "RAM")
        self.assertEqual([path.name for path in self.etc.iterdir() if path.name.startswith(".")], [])
        self.assertNotIn(["systemctl", "daemon-reload"], self.commands)

    def test_dry_run_names_the_changes_and_makes_none(self):
        self.install(dry_run=True)

        self.assertEqual(self.fstab.read_text(), FSTAB)
        self.assertEqual(self.ttl.read_text().strip(), "0")
        self.assertFalse(self.host.tmpfiles.exists())
        self.assertEqual(self.commands, [])
        preview = "\n".join(self.said)
        for path in (self.fstab, self.host.tmpfiles, self.ttl):
            self.assertIn(str(path), preview)

    def test_without_multi_gen_lru_install_still_moves_tmp_to_disk(self):
        self.enabled.write_text("0x0000\n")
        self.install()

        after = memory_guard.status(self.host)
        self.assertFalse(after.guard.available)
        self.assertEqual(self.ttl.read_text().strip(), "0")
        self.assertFalse(self.host.tmpfiles.exists())
        self.assertEqual(after.tmp.at_boot, "disk")

    def test_a_tmpfs_mounted_from_outside_fstab_is_left_alone(self):
        self.fstab.write_text(ROOT_AND_SWAP)
        self.install()

        self.assertEqual(self.fstab.read_text(), ROOT_AND_SWAP)
        self.assertIsNone(memory_guard.status(self.host).tmp.at_boot)
        self.assertNotIn(["systemctl", "daemon-reload"], self.commands)
        self.assertEqual(self.ttl.read_text().strip(), "1000")


if __name__ == "__main__":
    unittest.main()
