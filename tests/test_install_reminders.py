from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

from test_claude_router_install import ComponentFixture
from test_install import InstallerFixture

SOURCE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOURCE / "scripts"))
import astack_reminders
import claude_router
import memory_guard

ROUTER_README = "README.md#install-the-optional-claude-router"
LINGER = 'sudo loginctl enable-linger "$USER"'
SETUP = "claude-router setup -account NAME"
KILLED_AT_BINARY_RENAME = r'''
import os, runpy, sys
rename = os.replace
def replace(source, destination, **options):
    if os.path.basename(str(destination)) == "claude-router":
        os._exit(137)
    rename(source, destination, **options)
os.replace = replace
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
'''
PRINT_REMINDERS = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import astack_reminders, memory_guard
root = Path(sys.argv[2])
host = astack_reminders.Host(guard=memory_guard.Host(root), linger_dir=root / "linger")
print("\n".join(astack_reminders.pending(json.loads(sys.argv[3]), host)))
'''


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
        self.guard = memory_guard.Host(self.root)
        linger_dir = self.root / "var/lib/systemd/linger"
        self.linger = linger_dir / pwd.getpwuid(os.getuid()).pw_name
        self.host = astack_reminders.Host(guard=self.guard, linger_dir=linger_dir)
        self.router_state = self.root / "router state"
        self.router_state.mkdir()

    def pending(self, state=None):
        return "\n".join(astack_reminders.pending(state or {"targets": {}}, self.host))

    def guard_command(self, output):
        commands = [shlex.split(line) for line in output.splitlines() if "memory_guard.py" in line]
        return commands[0] if commands else None

    def setup_command(self, output):
        commands = [line.strip() for line in output.splitlines() if line.strip().startswith("claude-router setup")]
        return commands[0] if commands else None

    def installed_router(self, manager):
        layout = {"data": str(self.root / "router data"), "state": str(self.router_state), "manager": manager,
                  "service": "dev.astack.claude-router", "descriptor": None}
        return {"targets": {}, "components": {"claude-router": {"layout": layout}}}

    def activate(self):
        claude_router.write_journal(self.router_state, {"phase": "active", "settings": str(self.root / "settings.json")})

    def test_guard_reminder_gives_the_install_command_until_the_guard_is_installed(self):
        command = self.guard_command(self.pending())
        self.assertEqual(command, ["sudo", "python3", str(SOURCE / "scripts/memory_guard.py"), "install"])

        with redirect_stdout(io.StringIO()):
            memory_guard.install(self.guard, dry_run=False)

        self.assertIsNone(self.guard_command(self.pending()))

    def test_guard_reminder_stays_until_the_guard_is_on_both_now_and_at_boot(self):
        self.ttl.write_text("{}\n".format(memory_guard.MIN_TTL_MS))
        self.assertIsNotNone(self.guard_command(self.pending()))

        with redirect_stdout(io.StringIO()):
            memory_guard.install(self.guard, dry_run=False)
        self.assertIsNone(self.guard_command(self.pending()))

        self.ttl.write_text("2000\n")
        self.assertIsNone(self.guard_command(self.pending()))

        self.ttl.write_text("0\n")
        self.assertIsNotNone(self.guard_command(self.pending()))

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

    def test_installed_systemd_router_names_linger_then_setup_until_each_is_done(self):
        state = self.installed_router("systemd")
        output = self.pending(state)
        self.assertLess(output.index(LINGER), output.index(SETUP))

        self.linger.parent.mkdir(parents=True)
        self.linger.touch()
        output = self.pending(state)
        self.assertNotIn(LINGER, output)
        self.assertIn(SETUP, output)

        self.activate()
        self.assertNotIn("claude-router", self.pending(state))

    def test_only_a_systemd_router_names_linger(self):
        self.assertIn(LINGER, self.pending(self.installed_router("systemd")))

        output = self.pending(self.installed_router("launchd"))
        self.assertIn(SETUP, output)
        self.assertNotIn(LINGER, output)

    def test_a_router_install_with_pending_writes_names_neither_linger_nor_setup(self):
        state = self.installed_router("systemd")
        record = state["components"]["claude-router"]
        record["pending"] = {str(self.root / "router data/bin/claude-router"): "sha256 of the unfinished write"}
        output = self.pending(state)
        self.assertIn("--component claude-router", output)
        self.assertNotIn(LINGER, output)
        self.assertNotIn(SETUP, output)

        record["pending"] = {}
        output = self.pending(state)
        self.assertNotIn("--component claude-router", output)
        self.assertIn(LINGER, output)
        self.assertIn(SETUP, output)

    def test_setup_reminder_reuses_the_accounts_that_config_json_lists(self):
        state = self.installed_router("launchd")
        config = self.router_state / "config.json"
        for text in ("{}", json.dumps({"accounts": []})):
            config.write_text(text)
            self.assertEqual(self.setup_command(self.pending(state)), SETUP)

        config.write_text(json.dumps({"accounts": [{"id": "work", "capacity": 1}]}))
        self.assertEqual(self.setup_command(self.pending(state)), "claude-router setup")

    def test_a_machine_with_every_step_done_gets_no_reminders(self):
        state = self.installed_router("systemd")
        self.assertNotEqual(self.pending(state), "")

        with redirect_stdout(io.StringIO()):
            memory_guard.install(self.guard, dry_run=False)
        self.linger.parent.mkdir(parents=True)
        self.linger.touch()
        self.activate()

        self.assertEqual(astack_reminders.pending(state, self.host), [])

    def test_a_reminder_that_cannot_read_its_state_leaves_the_others(self):
        self.guard.boot_file.parent.mkdir(parents=True)
        self.guard.boot_file.write_text("w {} - - - - 1000\n".format(self.ttl))
        (self.router_state / "config.json").write_text("[]")
        output = self.pending(self.installed_router("systemd"))

        self.assertIsNone(self.guard_command(output))
        self.assertIsNone(self.setup_command(output))
        self.assertIn(LINGER, output)

    def test_a_linger_check_that_cannot_read_the_layout_leaves_the_setup_reminder(self):
        output = self.pending({"targets": {}, "components": {"claude-router": {"layout": {"state": str(self.router_state)}}}})

        self.assertNotIn(LINGER, output)
        self.assertEqual(self.setup_command(output), SETUP)

    def test_a_malformed_router_record_leaves_the_guard_reminder(self):
        for components in (None, {"claude-router": {"layout": "x"}}):
            output = self.pending({"targets": {}, "components": components})
            self.assertIsNotNone(self.guard_command(output), components)

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

    def test_the_guard_command_names_this_checkout_even_when_its_path_has_spaces(self):
        host_files = self.root / "host files"
        lru_gen = host_files / "sys/kernel/mm/lru_gen"
        lru_gen.mkdir(parents=True)
        (lru_gen / "enabled").write_text("0x0007\n")
        (lru_gen / "min_ttl_ms").write_text("0\n")
        result = subprocess.run([sys.executable, "-c", PRINT_REMINDERS, str(self.repo / "scripts"), str(host_files),
                                 json.dumps({"targets": {}})], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

        commands = [shlex.split(line) for line in result.stdout.splitlines() if "memory_guard.py" in line]
        script = (self.repo / "scripts/memory_guard.py").resolve()
        self.assertEqual(commands, [["sudo", "python3", str(script), "install"]])


class InterruptedRouterInstallTests(ComponentFixture):
    def test_an_interrupted_router_install_names_the_install_again_instead_of_setup(self):
        killed = subprocess.run(
            [sys.executable, "-c", KILLED_AT_BINARY_RENAME, str(self.repo / "scripts/manage.py"),
             "install", "--home", str(self.home), "--component", "claude-router"],
            cwd=str(self.root), capture_output=True, text=True, env=self.env)
        self.assertEqual(killed.returncode, 137, killed.stderr)
        self.assertFalse((self.data / "bin/claude-router").exists())

        output = self.run_installer("--target", "claude", env=self.env).stdout
        commands = [shlex.split(line) for line in output.splitlines() if "--component" in line]
        self.assertEqual(commands, [[str((self.repo / "install.sh").resolve()), "--component", "claude-router"]])
        self.assertNotIn("claude-router setup", output)

        self.component()
        output = self.run_installer("--target", "claude", env=self.env).stdout
        self.assertIn(SETUP, output)
        self.assertNotIn("--component", output)


if __name__ == "__main__":
    unittest.main()
