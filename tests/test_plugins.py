"""Plugin installation through fake agent CLIs; no real CLI or network is used."""

import json
import os
import subprocess
import sys
import unittest

from test_install import InstallerFixture

FAKE = r'''
import json
import os
from pathlib import Path
import sys

NAME = Path(sys.argv[0]).name
CONFIG = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}[NAME]
if Path(os.environ["HOME"]).resolve() != Path(EXPECTED_HOME).resolve() or \
        (os.environ.get("FAKE_EXPECT_NO_CONFIG") and CONFIG in os.environ):
    sys.exit("fake {} saw HOME={} and {}={}".format(NAME, os.environ["HOME"], CONFIG, os.environ.get(CONFIG)))
argv = sys.argv[1:]
home = Path(os.environ.get(CONFIG) or os.environ["HOME"])
home.mkdir(parents=True, exist_ok=True)
with (home / ".fake-{}.log".format(NAME)).open("a") as log:
    log.write(" ".join(argv) + "\n")
if os.environ.get("FAKE_FAIL") and " ".join(argv).startswith(os.environ["FAKE_FAIL"]):
    sys.exit("fake failure in " + " ".join(argv))
path = home / ".fake-{}.json".format(NAME)
state = json.loads(path.read_text()) if path.exists() else {"marketplaces": [], "plugins": []}
project = state.pop("project", [])
markets, plugins = state["marketplaces"], state["plugins"]
words = {"claude": {"refresh": "update", "install": "install", "remove": "uninstall"},
         "codex": {"refresh": "upgrade", "install": "add", "remove": "remove"}}[NAME]
command, argument = " ".join(argv[:-1]), argv[-1]


def require(condition):
    if not condition:
        sys.exit("fake {} rejected {}".format(NAME, " ".join(argv)))


if argv == ["plugin", "marketplace", "list", "--json"]:
    names = [{"name": name} for name in markets]
    print(json.dumps(names if NAME == "claude" else {"marketplaces": names}))
elif argv == ["plugin", "list", "--json"]:
    if NAME == "claude":
        print(json.dumps([{"id": plugin, "scope": "user"} for plugin in plugins]
                         + [{"id": plugin, "scope": "project"} for plugin in project + ["project-only@elsewhere"]]))
    else:
        print(json.dumps({"installed": [{"pluginId": plugin, "installed": True} for plugin in plugins]
                          + [{"pluginId": "available@elsewhere", "installed": False}]}))
elif command == "plugin marketplace add":
    markets.append(argument.rsplit("/", 1)[-1])
elif command == "plugin marketplace " + words["refresh"]:
    require(argument in markets)
elif command == "plugin marketplace remove":
    require(argument in markets)
    markets.remove(argument)
elif command == "plugin " + words["install"]:
    require(argument.partition("@")[2] in markets and argument not in plugins)
    plugins.append(argument)
elif NAME == "claude" and command == "plugin update":
    require(argument in plugins)
elif command == "plugin " + words["remove"]:
    require(argument in plugins)
    plugins.remove(argument)
else:
    sys.exit("fake {} does not know {}".format(NAME, " ".join(argv)))
path.write_text(json.dumps(dict(state, project=project) if project else state))
'''


class PluginTests(InstallerFixture):
    def setUp(self):
        super().setUp()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        for name in ("claude", "codex"):
            self.add_cli(name)
        self.write_source({"demo@test-market": "example/test-market"})

    def add_cli(self, name):
        script = self.bin / name
        script.write_text("#!{}\nEXPECTED_HOME = {!r}\n{}".format(sys.executable, str(self.home), FAKE))
        script.chmod(0o755)

    def write_source(self, plugins):
        (self.repo / "settings/plugins.json").write_text(json.dumps({
            "marketplaces": {plugin.partition("@")[2]: source for plugin, source in plugins.items()},
            "plugins": list(plugins),
        }))

    def env(self, **values):
        return dict(os.environ, PATH=os.pathsep.join([str(self.bin), "/usr/bin", "/bin"]), FAKE_EXPECT_NO_CONFIG="1",
                    CLAUDE_CONFIG_DIR=str(self.root / "outside"), CODEX_HOME=str(self.root / "outside"), **values)

    def install(self, *arguments, command="install", success=True, **values):
        return self.run_installer(*arguments, command=command, success=success, env=self.env(**values))

    def cli_state(self, name):
        return json.loads((self.home / ".fake-{}.json".format(name)).read_text())

    def seed(self, name, marketplaces, plugins):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / ".fake-{}.json".format(name)).write_text(
            json.dumps({"marketplaces": marketplaces, "plugins": plugins}))

    def take_calls(self, name):
        log = self.home / ".fake-{}.log".format(name)
        calls = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return calls

    def test_install_adds_then_refreshes_without_reinstalling(self):
        self.install()
        for name, install in (("claude", "install"), ("codex", "add")):
            self.assertEqual(self.cli_state(name), {"marketplaces": ["test-market"], "plugins": ["demo@test-market"]})
            self.assertEqual(self.take_calls(name), [
                "plugin marketplace list --json",
                "plugin list --json",
                "plugin marketplace add example/test-market",
                "plugin marketplace " + ("update" if name == "claude" else "upgrade") + " test-market",
                "plugin {} demo@test-market".format(install),
            ])
        result = self.install()
        self.assertIn("UPDATE PLUGIN demo@test-market (claude)", result.stdout)
        self.assertEqual(self.take_calls("claude"), [
            "plugin marketplace list --json",
            "plugin list --json",
            "plugin marketplace update test-market",
            "plugin update demo@test-market",
        ])
        self.assertEqual(self.take_calls("codex"), [
            "plugin marketplace list --json",
            "plugin list --json",
            "plugin marketplace upgrade test-market",
        ])

    def test_uninstall_removes_only_what_astack_added(self):
        self.write_source({"demo@test-market": "example/test-market", "extra@other-market": "example/other-market"})
        for name in ("claude", "codex"):
            self.seed(name, ["test-market"], ["demo@test-market"])
        self.install()
        self.assertEqual(self.cli_state("codex"), {
            "marketplaces": ["test-market", "other-market"],
            "plugins": ["demo@test-market", "extra@other-market"],
        })
        self.install(command="uninstall")
        for name in ("claude", "codex"):
            self.assertEqual(self.cli_state(name), {"marketplaces": ["test-market"], "plugins": ["demo@test-market"]})
        self.assertNotIn("plugins", self.manifest())

    def test_uninstall_keeps_a_marketplace_other_plugins_use(self):
        self.install()
        state = self.cli_state("claude")
        self.seed("claude", state["marketplaces"], state["plugins"] + ["own@test-market"])
        result = self.install(command="uninstall")
        self.assertIn("KEEP MARKETPLACE test-market (claude)", result.stdout)
        self.assertEqual(self.cli_state("claude"), {"marketplaces": ["test-market"], "plugins": ["own@test-market"]})
        self.assertNotIn("plugins", self.manifest())

    def assert_project_plugin_keeps_marketplace(self, project_plugin):
        self.install()
        state = self.cli_state("claude")
        (self.home / ".fake-claude.json").write_text(json.dumps(dict(state, project=[project_plugin])))
        result = self.install(command="uninstall")
        self.assertIn("KEEP MARKETPLACE test-market (claude)", result.stdout)
        self.assertEqual(self.cli_state("claude"), {"marketplaces": ["test-market"], "plugins": [],
                                                    "project": [project_plugin]})

    def test_uninstall_keeps_a_marketplace_a_project_plugin_uses(self):
        self.assert_project_plugin_keeps_marketplace("own@test-market")

    def test_uninstall_keeps_a_marketplace_when_the_plugin_is_also_project_scoped(self):
        self.assert_project_plugin_keeps_marketplace("demo@test-market")

    def test_uninstall_refuses_a_moved_config_directory(self):
        first, second = self.root / "config one", self.root / "config two"
        env = dict(os.environ, PATH=os.pathsep.join([str(self.bin), "/usr/bin", "/bin"]), HOME=str(self.home),
                   CODEX_HOME=str(self.root / "codex"), XDG_STATE_HOME=str(self.root / "state"))

        def run(command, config):
            return subprocess.run([str(self.repo / (command + ".sh"))], cwd=str(self.root), capture_output=True,
                                  text=True, env=dict(env, CLAUDE_CONFIG_DIR=str(config)))

        self.assertEqual(run("install", first).returncode, 0)
        moved = run("uninstall", second)
        self.assertNotEqual(moved.returncode, 0)
        self.assertIn("set CLAUDE_CONFIG_DIR back", moved.stderr)
        self.assertTrue((first / "CLAUDE.md").exists())
        self.assertEqual(json.loads((first / ".fake-claude.json").read_text())["plugins"], ["demo@test-market"])
        self.assertEqual(run("uninstall", first).returncode, 0)
        self.assertEqual(json.loads((first / ".fake-claude.json").read_text())["plugins"], [])

    def test_missing_cli_is_skipped(self):
        (self.bin / "codex").unlink()
        result = self.install()
        self.assertIn("SKIP codex plugins: codex not found", result.stdout)
        self.assertEqual(self.cli_state("claude")["plugins"], ["demo@test-market"])
        self.assertTrue((self.home / ".codex/AGENTS.md").exists())

    def test_uninstall_keeps_ownership_while_cli_is_missing(self):
        self.install()
        (self.bin / "codex").unlink()
        result = self.install(command="uninstall")
        self.assertIn("SKIP codex plugins: codex not found", result.stdout)
        self.add_cli("codex")
        self.install(command="uninstall")
        self.assertEqual(self.cli_state("codex"), {"marketplaces": [], "plugins": []})

    def test_uninstall_after_manual_removal_clears_ownership(self):
        self.install()
        self.seed("claude", [], [])
        self.install(command="uninstall")
        self.assertFalse([call for call in self.take_calls("claude") if "remove" in call or "uninstall" in call])
        self.assertNotIn("plugins", self.manifest())

    def test_failing_command_stops_install_and_rerun_recovers(self):
        result = self.install(success=False, FAKE_FAIL="plugin install")
        self.assertIn("claude plugin install demo@test-market failed", result.stderr)
        self.assertIn("fake failure in plugin install demo@test-market", result.stderr)
        self.assertEqual(self.cli_state("claude")["plugins"], [])
        self.install()
        self.assertEqual(self.cli_state("claude")["plugins"], ["demo@test-market"])
        self.install(command="uninstall")
        self.assertEqual(self.cli_state("claude"), {"marketplaces": [], "plugins": []})

    def test_dry_run_runs_no_cli(self):
        result = self.install("--dry-run")
        self.assertIn("PLUGIN demo@test-market (claude)", result.stdout)
        self.assertIn("PLUGIN demo@test-market (codex)", result.stdout)
        self.assertFalse(self.home.exists())
        self.install()
        self.take_calls("claude")
        result = self.install("--dry-run", command="uninstall")
        self.assertIn("REMOVE PLUGIN demo@test-market (claude)", result.stdout)
        self.assertEqual(self.take_calls("claude"), [])
        self.assertEqual(self.cli_state("claude")["plugins"], ["demo@test-market"])

    def test_plugin_removed_from_source_is_removed(self):
        self.write_source({"demo@test-market": "example/test-market", "extra@other-market": "example/other-market"})
        self.install()
        self.write_source({"demo@test-market": "example/test-market"})
        self.install()
        for name in ("claude", "codex"):
            self.assertEqual(self.cli_state(name), {"marketplaces": ["test-market"], "plugins": ["demo@test-market"]})

    def test_plugin_without_listed_marketplace_fails_before_writes(self):
        (self.repo / "settings/plugins.json").write_text(json.dumps({"plugins": ["demo@test-market"]}))
        result = self.install(success=False)
        self.assertIn("demo@test-market", result.stderr)
        self.assertFalse(self.home.exists())


if __name__ == "__main__":
    unittest.main()
