"""Reminders for the setup steps that ./install.sh cannot take, against temporary host files."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import re
import shlex
import sys
import tempfile
import unittest

from test_install import InstallerFixture

SOURCE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOURCE / "scripts"))
import astack_reminders
import memory_guard

ROUTER_README = "README.md#install-the-optional-claude-router"
LINGER = 'sudo loginctl enable-linger "$USER"'
SETUP = "claude-router setup -account NAME"


class ReminderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="astack reminders ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        lru_gen = self.root / "sys/kernel/mm/lru_gen"
        lru_gen.mkdir(parents=True)
        self.enabled = lru_gen / "enabled"
        self.enabled.write_text("0x0007\n")
        self.ttl = lru_gen / "min_ttl_ms"
        self.ttl.write_text("0\n")
        self.guard = memory_guard.Host(lru_gen_enabled_file=self.enabled, min_ttl_ms_file=self.ttl,
                                       boot_file=self.root / "etc/tmpfiles.d/astack-memory-guard.conf")
        self.linger = self.root / "var/lib/systemd/linger/someone"
        self.host = astack_reminders.Host(guard=self.guard, linger_file=self.linger)
        self.router_state = self.root / "router state"
        self.router_state.mkdir()

    def pending(self, state=None):
        return "\n".join(astack_reminders.pending(state or {"targets": {}}, self.host))

    def guard_command(self, output):
        commands = [shlex.split(line) for line in output.splitlines() if "memory_guard.py" in line]
        return commands[0] if commands else None

    def installed_router(self, manager):
        layout = {"data": str(self.root / "router data"), "state": str(self.router_state), "manager": manager,
                  "service": "dev.astack.claude-router", "descriptor": None}
        return {"targets": {}, "components": {"claude-router": {"layout": layout}}}

    def activate(self, state):
        settings = str(self.root / "settings.json")
        (self.router_state / "activation.json").write_text(json.dumps({"phase": "active", "settings": settings}))
        state["settings"] = {"claude-router": {settings: {"format": "json", "keys": {}}}}

    def test_guard_reminder_gives_the_install_command_until_the_guard_is_installed(self):
        command = self.guard_command(self.pending())
        self.assertEqual(command[:2] + command[3:], ["sudo", "python3", "install"])
        self.assertTrue(Path(command[2]).samefile(SOURCE / "scripts/memory_guard.py"))

        with redirect_stdout(io.StringIO()):
            memory_guard.install(self.guard, dry_run=False)

        self.assertIsNone(self.guard_command(self.pending()))

    def test_no_guard_reminder_where_the_kernel_has_no_multi_gen_lru(self):
        self.assertIsNotNone(self.guard_command(self.pending()))

        self.enabled.write_text("0x0000\n")
        self.assertIsNone(self.guard_command(self.pending()))

        self.enabled.unlink()
        self.ttl.unlink()
        self.assertIsNone(self.guard_command(self.pending()))

    def test_router_that_is_not_installed_gets_one_line_that_calls_it_optional(self):
        lines = [line for line in self.pending().splitlines() if "claude-router" in line]

        self.assertEqual(len(lines), 1)
        self.assertIn("optional", lines[0].lower())
        self.assertIn(ROUTER_README, lines[0])

    def test_installed_systemd_router_names_linger_and_setup_until_each_is_done(self):
        state = self.installed_router("systemd")
        output = self.pending(state)
        self.assertIn(LINGER, output)
        self.assertIn(SETUP, output)

        self.linger.parent.mkdir(parents=True)
        self.linger.touch()
        output = self.pending(state)
        self.assertNotIn(LINGER, output)
        self.assertIn(SETUP, output)

        self.activate(state)
        self.assertNotIn("claude-router", self.pending(state))

    def test_installed_launchd_router_names_setup_but_never_linger(self):
        state = self.installed_router("launchd")
        output = self.pending(state)

        self.assertIn(SETUP, output)
        self.assertNotIn(LINGER, output)

    def test_a_reminder_that_cannot_read_its_state_leaves_the_others(self):
        self.guard.boot_file.parent.mkdir(parents=True)
        self.guard.boot_file.write_text("w {} - - - - 1000\n".format(self.ttl))
        output = self.pending()

        self.assertIsNone(self.guard_command(output))
        self.assertIn(ROUTER_README, output)

    def test_every_readme_link_names_a_readme_section(self):
        output = self.pending() + "\n" + self.pending(self.installed_router("systemd"))
        headings = re.findall(r"^#+ (.+)$", (SOURCE / "README.md").read_text(encoding="utf-8"), re.MULTILINE)
        anchors = {re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-") for heading in headings}
        linked = set(re.findall(r"README\.md#([\w-]+)", output))

        self.assertEqual(len(linked), 2)
        self.assertLessEqual(linked, anchors)


class InstallerReminderTests(InstallerFixture):
    def test_install_and_preview_print_reminders_after_the_result_and_uninstall_prints_none(self):
        for arguments in (("--dry-run",), ()):
            lines = self.run_installer("--target", "claude", *arguments).stdout.splitlines()
            done = next(i for i, line in enumerate(lines) if line.endswith("complete for claude."))
            self.assertIn(ROUTER_README, "\n".join(lines[done + 1:]))

        result = self.run_installer("--target", "claude", command="uninstall")
        self.assertIn("Uninstall complete for claude.", result.stdout)
        self.assertNotIn("README.md#", result.stdout)


if __name__ == "__main__":
    unittest.main()
