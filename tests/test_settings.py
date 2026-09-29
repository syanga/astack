"""Shared settings ownership, lossless TOML edits, and reversible installation."""

import json
import stat
from pathlib import Path
from unittest.mock import patch

import test_install_lifecycle as lifecycle
from test_install import InstallerFixture

HOOK = {"matcher": "Bash", "hooks": [{"type": "command", "command": "check", "timeout": 10}]}
OTHER = {"matcher": "Bash", "hooks": [{"type": "command", "command": "other"}]}
MINE = {"matcher": "Edit", "hooks": [{"type": "command", "command": "mine"}]}


class SettingsTests(InstallerFixture):
    manager = lifecycle.InstallerLifecycleTests.manager
    run_with_config = lifecycle.InstallerLifecycleTests.run_with_config
    inventory = lifecycle.InstallerLifecycleTests.inventory

    def source(self, target, values):
        (self.repo / "settings" / (target + ".json")).write_text(json.dumps(values))

    def config(self, target, content):
        path = self.home / (".claude/settings.json" if target == "claude" else ".codex/config.toml")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode())
        path.chmod(0o640)
        return path

    def test_defaults_disable_claude_memory_and_never_read_codex_config(self):
        codex = self.config("codex", "not even valid TOML; leave untouched\n")
        before = codex.read_bytes(), codex.stat().st_mtime_ns
        self.run_installer()
        self.assertEqual(json.loads((self.home / ".claude/settings.json").read_text()), {"autoMemoryEnabled": False})
        self.assertEqual(before, (codex.read_bytes(), codex.stat().st_mtime_ns))
        self.assertEqual(self.manifest()["version"], 3)
        self.assertNotIn("codex", self.manifest()["settings"])
        self.run_installer(command="uninstall")
        self.assertFalse((self.home / ".claude/settings.json").exists())
        self.assertEqual(codex.read_bytes(), before[0])

    def test_both_harnesses_install_update_and_exact_restore(self):
        self.source("codex", {"model_reasoning_effort": "high", "features": {"memories": False}})
        originals = {
            "claude": '{ "autoMemoryEnabled": true, "env": {"KEEP": "café"}, "permissions": {"allow": ["Read"]} }\n',
            "codex": '# personal\r\nmodel_reasoning_effort = \'low\' # choice\r\n[features] # toggles\r\nmemories = true\r\n[projects."/a.b"]\r\ntrust_level = "trusted"\r\n',
        }
        paths = {name: self.config(name, text) for name, text in originals.items()}
        before = self.inventory()
        preview = self.run_installer("--dry-run")
        self.assertIn("MERGE SETTINGS", preview.stdout)
        self.assertEqual(before, self.inventory())
        self.run_installer()
        self.assertFalse(json.loads(paths["claude"].read_text())["autoMemoryEnabled"])
        self.assertIn(b'model_reasoning_effort = "high" # choice\r\n', paths["codex"].read_bytes())
        self.assertIn(b'[projects."/a.b"]\r\ntrust_level = "trusted"\r\n', paths["codex"].read_bytes())
        times = {name: path.stat().st_mtime_ns for name, path in paths.items()}
        self.run_installer()
        self.assertEqual(times, {name: path.stat().st_mtime_ns for name, path in paths.items()})
        self.source("claude", {"autoMemoryEnabled": True})
        self.source("codex", {"model_reasoning_effort": "medium", "features": {"memories": True}})
        self.run_installer()
        self.run_installer(command="uninstall")
        for name, path in paths.items():
            self.assertEqual(path.read_bytes(), originals[name].encode())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertEqual(self.manifest()["settings"], {})

    def test_unrelated_edits_survive_uninstall(self):
        self.source("codex", {"model_reasoning_effort": "high"})
        claude = self.config("claude", '{"autoMemoryEnabled": true, "env": {"KEEP": "old"}}\n')
        codex = self.config("codex", 'model_reasoning_effort = "low" # original\n[features]\nmemories = true\n')
        self.run_installer()
        obj = json.loads(claude.read_text())
        obj["env"]["KEEP"] = "new"
        claude.write_text(json.dumps(obj))
        codex.write_text(codex.read_text().replace("memories = true", "memories = false # my edit") + "# local comment\n")
        self.run_installer()
        self.run_installer(command="uninstall")
        self.assertEqual(json.loads(claude.read_text()), {"autoMemoryEnabled": True, "env": {"KEEP": "new"}})
        self.assertIn('model_reasoning_effort = "low" # original', codex.read_text())
        self.assertIn('memories = false # my edit\n# local comment', codex.read_text())

    def test_conflicts_preflight_and_force_backup_preserves_original_baseline(self):
        self.source("codex", {"model_reasoning_effort": "high"})
        for target, original, edited in (
            ("claude", '{"autoMemoryEnabled":true}', '{"autoMemoryEnabled":1,"other":true}'),
            ("codex", 'model_reasoning_effort="low"', 'model_reasoning_effort="medium"\n# local'),
        ):
            with self.subTest(target=target):
                path = self.config(target, original)
                self.run_installer("--target", target)
                path.write_text(edited)
                before = self.inventory()
                for command in ("install", "uninstall"):
                    result = self.run_installer("--target", target, command=command, success=False)
                    self.assertIn("locally modified", result.stderr)
                    self.assertEqual(self.inventory(), before)
                self.run_installer("--target", target, "--force", "--dry-run")
                self.assertEqual(self.inventory(), before)
                self.run_installer("--target", target, "--force", command="uninstall")
                backups = list((self.home / ".local/state/astack/backups").rglob(path.name))
                self.assertIn(edited, [p.read_text() for p in backups])
                if target == "claude":
                    self.assertEqual(json.loads(path.read_text()), {"autoMemoryEnabled": True, "other": True})
                else:
                    self.assertIn('model_reasoning_effort="low"', path.read_text())
                    self.assertIn("# local", path.read_text())

    def test_removing_source_keys_restores_only_those_keys(self):
        self.source("claude", {"autoMemoryEnabled": False, "env": {"ASTACK_TEST": "yes"}})
        path = self.config("claude", '{"env":{"KEEP":"here"}}')
        self.run_installer("--target", "claude")
        self.source("claude", {"autoMemoryEnabled": False})
        self.run_installer("--target", "claude")
        self.assertEqual(json.loads(path.read_text()), {"autoMemoryEnabled": False, "env": {"KEEP": "here"}})
        self.source("claude", {})
        self.run_installer("--target", "claude")
        self.assertEqual(path.read_text(), '{"env":{"KEEP":"here"}}')
        self.assertNotIn("claude", self.manifest()["settings"])

    def test_new_nested_json_keys_restore_empty_original(self):
        self.source("claude", {"env": {"X": "yes"}})
        path = self.config("claude", '{}\n')
        self.run_installer("--target", "claude")
        self.run_installer("--target", "claude", command="uninstall")
        self.assertEqual(path.read_text(), '{}\n')

    def test_new_codex_file_with_unrelated_comment_is_retained(self):
        self.source("codex", {"features": {"memories": False}})
        self.run_installer("--target", "codex")
        path = self.home / ".codex/config.toml"
        path.write_text(path.read_text() + "# user comment\n")
        self.run_installer("--target", "codex")
        self.run_installer("--target", "codex", command="uninstall")
        self.assertIn("# user comment", path.read_text())
        self.assertNotIn("memories", path.read_text())

    def test_toml_multiline_arrays_quoted_keys_and_literal_restoration(self):
        self.source("codex", {"model_reasoning_effort": "high", "features": {"memories": False}})
        original = '# preserve\nmodel_reasoning_effort = """low\nvalue""" # multiline\nnotify = [\n "x", # comment\n "y",\n]\n[features]\n"memories"=true\n[[agents.list]]\nname="ignored"\n'
        path = self.config("codex", original)
        self.run_installer("--target", "codex")
        self.assertIn('notify = [\n "x", # comment\n "y",\n]', path.read_text())
        self.assertIn('"memories"=false', path.read_text())
        self.run_installer("--target", "codex", command="uninstall")
        self.assertEqual(path.read_text(), original)

    def test_codex_nested_insertions_and_arrays_preserve_existing_tables(self):
        self.source("codex", {"features": {"a.b": True, "memories": False}, "notify": ["tool", "--arg", "café"]})
        original = '# before\n[features]\n# existing toggle\napps = true\n[projects."/path"]\ntrust_level="trusted"\n'
        path = self.config("codex", original)
        self.run_installer("--target", "codex")
        self.assertIn('"a.b" = true', path.read_text())
        self.assertIn('"notify" = ["tool", "--arg", "café"]', path.read_text())
        self.assertIn('# existing toggle\napps = true', path.read_text())
        self.run_installer("--target", "codex", command="uninstall")
        self.assertEqual(path.read_text(), original)

    def test_absent_defaults_do_not_create_codex_config(self):
        self.run_installer("--target", "codex")
        self.assertFalse((self.home / ".codex/config.toml").exists())

    def test_toml_header_whitespace_preserved_on_update_and_restore(self):
        self.source("codex", {"features": {"memories": False, "apps": True}})
        original = ('[ \tfeatures \t] # toggles\r\nmemories = true\r\n'
                    '[[ \tagents . "list" ]]\r\nname = "keep"\r\n')
        path = self.config("codex", original)
        self.run_installer("--target", "codex")
        self.assertIn(b'[ \tfeatures \t] # toggles\r\n', path.read_bytes())
        self.assertIn(b'memories = false\r\n', path.read_bytes())
        self.assertIn(b'"apps" = true\r\n', path.read_bytes())
        self.assertIn(b'[[ \tagents . "list" ]]\r\nname = "keep"\r\n', path.read_bytes())
        self.run_installer("--target", "codex", command="uninstall")
        self.assertEqual(path.read_bytes(), original.encode())

    def test_uninstall_preview_preserves_settings_and_ownership(self):
        self.source("codex", {"model_reasoning_effort": "high"})
        self.config("codex", 'model_reasoning_effort="low"\n')
        self.run_installer()
        before = self.inventory()
        result = self.run_installer("--dry-run", command="uninstall")
        self.assertIn("SETTINGS", result.stdout)
        self.assertEqual(before, self.inventory())

    def test_manually_removed_managed_key_is_a_conflict(self):
        path = self.config("claude", '{}')
        self.run_installer("--target", "claude")
        path.write_text('{"unrelated":true}')
        before = self.inventory()
        self.run_installer("--target", "claude", command="uninstall", success=False)
        self.assertEqual(before, self.inventory())
        self.run_installer("--target", "claude", "--force", command="uninstall")
        self.assertEqual(json.loads(path.read_text()), {"unrelated": True})

    def test_invalid_documents_and_unsupported_structures_never_mutate(self):
        self.source("codex", {"features": {"memories": False}})
        for target, content in (
            ("claude", '{"autoMemoryEnabled":true,"autoMemoryEnabled":false}'),
            ("claude", '[]'), ("claude", '{"secret":"dont-print",}'),
            ("codex", 'features = { memories = true }'),
            ("codex", '[features]\nmemories=true\nmemories=false'),
            ("codex", '[[features]]\nmemories=true'),
            ("codex", 'features="scalar"'),
        ):
            with self.subTest(target=target, content=content):
                self.config(target, content)
                before = self.inventory()
                result = self.run_installer("--target", target, "--force", success=False)
                self.assertNotIn("dont-print", result.stderr)
                self.assertEqual(before, self.inventory())

    def test_symlink_and_directory_destinations_rejected_even_with_force(self):
        path = self.home / ".claude/settings.json"
        path.parent.mkdir(parents=True)
        referent = self.root / "outside.json"
        referent.write_text('{}')
        path.symlink_to(referent)
        self.run_installer("--force", success=False)
        self.assertEqual(referent.read_text(), '{}')
        self.assertFalse((self.home / ".claude/CLAUDE.md").exists())
        path.unlink()
        path.mkdir()
        self.run_installer("--force", success=False)
        self.assertTrue(path.is_dir())

    def test_settings_cannot_overlap_payload_or_other_harness(self):
        registry = self.repo / "harnesses.json"
        config = json.loads(registry.read_text())
        config["claude"]["settings"]["suffix"] = "CLAUDE.md"
        registry.write_text(json.dumps(config))
        result = self.run_installer(success=False)
        self.assertIn("distinct destinations", result.stderr)
        self.assertFalse(self.home.exists())

    def test_custom_roots_and_recorded_path_uninstall(self):
        self.source("codex", {"model_reasoning_effort": "high"})
        module = self.manager()
        env = {"CLAUDE_CONFIG_DIR": str(self.root / "custom claude"), "CODEX_HOME": str(self.root / "custom codex")}
        self.run_with_config(module, env, "install")
        paths = [Path(env["CLAUDE_CONFIG_DIR"]) / "settings.json", Path(env["CODEX_HOME"]) / "config.toml"]
        self.assertTrue(all(p.exists() for p in paths))
        self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in paths))
        self.run_with_config(module, {}, "uninstall")
        self.assertTrue(all(not p.exists() for p in paths))

    def test_older_manifest_versions_migrate_without_losing_file_ownership(self):
        for version in (1, 2):
            with self.subTest(version=version):
                self.source("claude", {})
                self.run_installer()
                state_path = self.home / ".local/state/astack/manifest.json"
                state = self.manifest()
                state["version"] = version
                state.pop("settings", None)
                state_path.write_text(json.dumps(state))
                self.source("claude", {"autoMemoryEnabled": False})
                self.run_installer()
                self.assertEqual(self.manifest()["version"], 3)
                self.assertEqual(self.manifest()["targets"], state["targets"])
                self.run_installer(command="uninstall")
                self.assertEqual(self.manifest()["targets"], {})

    def test_settings_state_write_failure_rolls_back_payload(self):
        original = '{"autoMemoryEnabled":true,"keep":1}'
        path = self.config("claude", original)
        module = self.manager()
        real_write = module.files.atomic_write

        def fail_settings_state(target, data, mode=0o644):
            if target.name == "manifest.json" and '"settings":' in data.decode():
                raise OSError("Injected settings ownership write failure")
            return real_write(target, data, mode)

        with patch.object(module.files, "atomic_write", side_effect=fail_settings_state):
            with self.assertRaises(OSError):
                self.run_with_config(module, {}, "install", "--target", "claude")
        self.assertEqual(path.read_text(), original)
        self.assertNotIn("settings", self.manifest())
        self.run_installer("--target", "claude")
        self.run_installer("--target", "claude", command="uninstall")
        self.assertEqual(path.read_text(), original)

    def test_setting_modified_after_preview_is_not_overwritten(self):
        path = self.config("claude", '{"autoMemoryEnabled":true}')
        module = self.manager()
        real_write = module.files.atomic_write

        def edit_during_payload(target, data, mode=0o644):
            if target.name == "CLAUDE.md":
                path.write_text('{"autoMemoryEnabled":true,"concurrent":true}')
            return real_write(target, data, mode)

        with patch.object(module.files, "atomic_write", side_effect=edit_during_payload):
            with self.assertRaisesRegex(ValueError, "Settings changed"):
                self.run_with_config(module, {}, "install", "--target", "claude")
        self.assertTrue(json.loads(path.read_text())["concurrent"])
        self.assertNotIn("settings", self.manifest())

    def test_later_settings_failure_preserves_completed_installation(self):
        self.source("codex", {"model_reasoning_effort": "high"})
        module = self.manager()
        real_write = module.files.atomic_write

        def fail_codex_state(target, data, mode=0o644):
            if target.name == "manifest.json" and "codex" in json.loads(data).get("settings", {}):
                raise OSError("Injected settings ownership write failure")
            return real_write(target, data, mode)

        with patch.object(module.files, "atomic_write", side_effect=fail_codex_state):
            with self.assertRaises(OSError):
                self.run_with_config(module, {}, "install")
        self.assertFalse((self.home / ".codex/config.toml").exists())
        claude = self.home / ".claude/settings.json"
        self.assertFalse(json.loads(claude.read_text())["autoMemoryEnabled"])
        state = self.manifest()
        self.assertEqual(set(state["settings"]), {"claude"})
        self.assertEqual(set(state["targets"]), {"claude", "codex"})
        self.assertTrue(all(Path(path).exists() for records in state["targets"].values() for path in records))
        self.run_installer()
        self.run_installer(command="uninstall")
        self.assertFalse(claude.exists())
        self.assertFalse((self.home / ".codex/config.toml").exists())

    def test_uninstall_state_failure_restores_removed_settings(self):
        self.run_installer("--target", "claude")
        path = self.home / ".claude/settings.json"
        original = path.read_bytes()
        ownership = self.manifest()["settings"]
        module = self.manager()
        real_write = module.files.atomic_write

        def fail_settings_removal(target, data, mode=0o644):
            if target.name == "manifest.json" and json.loads(data).get("settings") == {}:
                raise OSError("Injected settings ownership write failure")
            return real_write(target, data, mode)

        with patch.object(module.files, "atomic_write", side_effect=fail_settings_removal):
            with self.assertRaises(OSError):
                self.run_with_config(module, {}, "uninstall", "--target", "claude")
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.manifest()["settings"], ownership)
        self.assertEqual(self.manifest()["targets"], {})
        self.run_installer("--target", "claude", command="uninstall")
        self.assertFalse(path.exists())

    def test_list_entries_join_user_list_idempotently_and_restore_exactly(self):
        self.source("claude", {"hooks": {"PreToolUse": {"$entries": [HOOK]}}})
        original = '{"hooks": {"PreToolUse": [%s]}, "keep": 1}\n' % json.dumps(MINE)
        path = self.config("claude", original)
        self.run_installer("--target", "claude")
        self.assertEqual(json.loads(path.read_text()), {"hooks": {"PreToolUse": [MINE, HOOK]}, "keep": 1})
        self.assertEqual(self.manifest()["version"], 3)
        written = path.stat().st_mtime_ns
        self.assertIn("KEEP SETTINGS", self.run_installer("--target", "claude").stdout)
        self.assertEqual(written, path.stat().st_mtime_ns)
        self.run_installer("--target", "claude", command="uninstall")
        self.assertEqual(path.read_text(), original)
        self.assertNotIn("claude", self.manifest()["settings"])

    def test_list_entries_create_and_remove_absent_list_and_parents(self):
        self.source("claude", {"autoMemoryEnabled": False, "hooks": {"PreToolUse": {"$entries": [HOOK, OTHER]}}})
        path = self.config("claude", '{"env": {}}\n')
        self.run_installer("--target", "claude")
        self.assertEqual(json.loads(path.read_text()),
                         {"env": {}, "autoMemoryEnabled": False, "hooks": {"PreToolUse": [HOOK, OTHER]}})
        self.run_installer("--target", "claude", command="uninstall")
        self.assertEqual(path.read_text(), '{"env": {}}\n')
        path.unlink()
        self.run_installer("--target", "claude")
        self.run_installer("--target", "claude", command="uninstall")
        self.assertFalse(path.exists())

    def test_list_entries_update_and_uninstall_keep_user_elements(self):
        self.source("claude", {"hooks": {"PreToolUse": {"$entries": [HOOK]}}})
        path = self.config("claude", '{}')
        self.run_installer("--target", "claude")
        document = json.loads(path.read_text())
        document["hooks"]["PreToolUse"].insert(0, MINE)
        path.write_text(json.dumps(document))
        self.source("claude", {"hooks": {"PreToolUse": {"$entries": [OTHER]}}})
        self.run_installer("--target", "claude")
        self.assertEqual(json.loads(path.read_text()), {"hooks": {"PreToolUse": [MINE, OTHER]}})
        self.run_installer("--target", "claude", command="uninstall")
        self.assertEqual(json.loads(path.read_text()), {"hooks": {"PreToolUse": [MINE]}})
        self.assertEqual(self.manifest()["settings"], {})

    def test_list_entries_keep_empty_list_the_user_created_after_first_install(self):
        path = self.config("claude", '{}')
        self.run_installer("--target", "claude")
        path.write_text('{"autoMemoryEnabled": false, "hooks": {"PreToolUse": []}}')
        self.source("claude", {"autoMemoryEnabled": False, "hooks": {"PreToolUse": {"$entries": [HOOK]}}})
        self.run_installer("--target", "claude")
        self.assertEqual(json.loads(path.read_text())["hooks"], {"PreToolUse": [HOOK]})
        self.run_installer("--target", "claude", command="uninstall")
        self.assertEqual(json.loads(path.read_text()), {"hooks": {"PreToolUse": []}})

    def test_list_entry_already_present_is_left_to_the_user(self):
        self.source("claude", {"hooks": {"PreToolUse": {"$entries": [HOOK, OTHER]}}})
        path = self.config("claude", json.dumps({"hooks": {"PreToolUse": [HOOK]}}))
        self.run_installer("--target", "claude")
        self.assertEqual(json.loads(path.read_text()), {"hooks": {"PreToolUse": [HOOK, OTHER]}})
        self.run_installer("--target", "claude", command="uninstall")
        self.assertEqual(json.loads(path.read_text()), {"hooks": {"PreToolUse": [HOOK]}})

    def test_missing_owned_entry_is_a_conflict_that_force_releases(self):
        self.source("claude", {"hooks": {"PreToolUse": {"$entries": [HOOK]}}})
        path = self.config("claude", json.dumps({"hooks": {"PreToolUse": [MINE]}}))
        self.run_installer("--target", "claude")
        path.write_text(json.dumps({"hooks": {"PreToolUse": [MINE]}, "edited": True}))
        before = self.inventory()
        for command in ("install", "uninstall"):
            result = self.run_installer("--target", "claude", command=command, success=False)
            self.assertIn("locally modified", result.stderr)
            self.assertEqual(before, self.inventory())
        self.run_installer("--target", "claude", "--force", command="uninstall")
        self.assertEqual(json.loads(path.read_text()), {"hooks": {"PreToolUse": [MINE]}, "edited": True})
        self.assertEqual(self.manifest()["settings"], {})

    def test_duplicated_owned_entry_is_refused_even_with_force(self):
        self.source("claude", {"hooks": {"PreToolUse": {"$entries": [HOOK]}}})
        path = self.config("claude", '{}')
        self.run_installer("--target", "claude")
        path.write_text(json.dumps({"hooks": {"PreToolUse": [HOOK, MINE, HOOK]}}))
        before = self.inventory()
        for command in ("install", "uninstall"):
            for flags in ((), ("--force",)):
                with self.subTest(command=command, flags=flags):
                    self.run_installer("--target", "claude", *flags, command=command, success=False)
                    self.assertEqual(before, self.inventory())
        path.write_text(json.dumps({"hooks": {"PreToolUse": [MINE, HOOK]}}))
        self.run_installer("--target", "claude", command="uninstall")
        self.assertEqual(json.loads(path.read_text()), {"hooks": {"PreToolUse": [MINE]}})

    def test_non_list_entries_destination_is_refused_even_with_force(self):
        self.source("claude", {"hooks": {"PreToolUse": {"$entries": [HOOK]}}})
        for content in ('{"hooks": {"PreToolUse": {"matcher": "Bash"}}}', '{"hooks": {"PreToolUse": null}}'):
            with self.subTest(content=content):
                self.config("claude", content)
                before = self.inventory()
                self.run_installer("--target", "claude", "--force", success=False)
                self.assertEqual(before, self.inventory())

    def test_invalid_entries_sources_are_rejected_before_any_write(self):
        path = self.config("codex", 'model = "x"\n')
        self.source("codex", {"notify": {"$entries": ["tool"]}})
        before = self.inventory()
        self.run_installer("--target", "codex", success=False)
        self.assertEqual(before, self.inventory())
        self.assertEqual(path.read_text(), 'model = "x"\n')
        for values in (
            {"hooks": {"PreToolUse": {"$entries": []}}},
            {"hooks": {"PreToolUse": {"$entries": HOOK}}},
            {"hooks": {"PreToolUse": {"$entries": [HOOK, dict(HOOK)]}}},
            {"hooks": {"PreToolUse": {"$entries": [HOOK], "extra": 1}}},
            {"$entries": [HOOK]},
        ):
            with self.subTest(values=values):
                self.source("claude", values)
                self.run_installer("--target", "claude", success=False)
                self.assertEqual(before, self.inventory())

    def test_switching_between_value_and_entries_restores_original(self):
        original = '{"hooks": {"PreToolUse": [%s]}}\n' % json.dumps(MINE)
        path = self.config("claude", original)
        self.source("claude", {"hooks": {"PreToolUse": [OTHER]}})
        self.run_installer("--target", "claude")
        self.source("claude", {"hooks": {"PreToolUse": {"$entries": [HOOK]}}})
        self.run_installer("--target", "claude")
        self.assertEqual(json.loads(path.read_text()), {"hooks": {"PreToolUse": [MINE, HOOK]}})
        self.source("claude", {"hooks": {"PreToolUse": [OTHER]}})
        self.run_installer("--target", "claude")
        self.assertEqual(json.loads(path.read_text()), {"hooks": {"PreToolUse": [OTHER]}})
        self.run_installer("--target", "claude", command="uninstall")
        self.assertEqual(path.read_text(), original)
