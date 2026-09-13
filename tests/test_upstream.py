"""Provenance and update checks against local Git repositories; no network needed."""

import json
import fcntl
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

SOURCE = Path(__file__).resolve().parent.parent


class UpstreamTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="astack upstream test ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "astack checkout"
        shutil.copytree(SOURCE, self.repo, ignore=shutil.ignore_patterns(".git", ".cache", "__pycache__"))
        self.remote = self.root / "upstream repo"
        self.remote.mkdir()
        self.git("init", "--initial-branch=main")
        self.primary = self.remote / "plugin/skills/example"
        self.primary.mkdir(parents=True)
        (self.primary / "SKILL.md").write_text("Original skill guidance.\n")
        (self.primary / "old.txt").write_text("An old reference.\n")
        helper = self.remote / "shared/helper.sh"
        helper.parent.mkdir()
        helper.write_text("#!/bin/sh\nexit 0\n")
        helper.chmod(0o755)
        (self.remote / "LICENSE").write_text("Fixture license text.\n")
        self.original = self.commit()
        self.manifest_path = self.repo / "upstream/manifest.json"
        self.manifest_path.write_text(json.dumps({
            "version": 1,
            "sources": {"fixture": {"repository": str(self.remote), "ref": "refs/heads/main"}},
            "imports": {},
        }))

    def git(self, *arguments, directory=None):
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-C", str(directory or self.remote), *arguments],
            check=True, capture_output=True, text=True,
        )
        return result.stdout.strip()

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-m", "Fixture update")
        return self.git("rev-parse", "HEAD")

    def command(self, *arguments, success=True):
        result = subprocess.run(
            [sys.executable, str(self.repo / "scripts/upstream.py"), *arguments],
            cwd=self.root, capture_output=True, text=True,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def pin(self, *arguments, success=True):
        return self.command("track", "example-fixture", "--source", "fixture",
                            "--path", "plugin/skills/example", "--include", "shared/helper.sh",
                            "--include", "LICENSE", "--local", "skills/custom-example",
                            *arguments, success=success)

    def manifest(self):
        return json.loads(self.manifest_path.read_text())

    def test_tracks_original_bytes_without_importing_active_skill(self):
        self.pin("--notes", "Keep our custom workflow.")
        entry = self.manifest()["imports"]["example-fixture"]
        self.assertEqual(entry["base_commit"], self.original)
        self.assertEqual(entry["notes"], "Keep our custom workflow.")
        with zipfile.ZipFile(self.repo / "upstream/bases/example-fixture.zip") as base:
            self.assertEqual(base.read("plugin/skills/example/SKILL.md"),
                             (self.primary / "SKILL.md").read_bytes())
            self.assertEqual((base.getinfo("shared/helper.sh").external_attr >> 16) & 0o777, 0o755)
            self.assertIn("LICENSE", base.namelist())
        self.assertFalse((self.repo / "skills/custom-example").exists())

    def test_check_and_diff_show_upstream_changes_without_moving_pin(self):
        self.pin()
        before = self.manifest_path.read_bytes()
        (self.primary / "SKILL.md").write_text("Improved upstream guidance.\n")
        (self.primary / "old.txt").unlink()
        (self.primary / "new.txt").write_text("A new reference.\n")
        (self.remote / "shared/helper.sh").write_text("#!/bin/sh\nexit 1\n")
        (self.remote / "unrelated.txt").write_text("Untracked change.\n")
        current = self.commit()
        summary = self.command("check").stdout
        patch = self.command("diff", "example-fixture").stdout
        self.assertIn(current, summary)
        self.assertIn(self.original, summary)
        for name in ("SKILL.md", "old.txt", "new.txt", "helper.sh"):
            self.assertIn(name, summary)
        self.assertIn("+Improved upstream guidance.", patch)
        self.assertIn("-Original skill guidance.", patch)
        self.assertNotIn("Untracked change.", patch)
        self.assertEqual(self.manifest_path.read_bytes(), before)

    def test_local_diff_is_offline_and_preserves_customizations(self):
        self.pin()
        local = self.repo / "skills/custom-example"
        local.mkdir()
        skill = local / "SKILL.md"
        skill.write_text("Our customized guidance.\n")
        shutil.rmtree(self.remote)
        shutil.rmtree(self.repo / ".cache")
        patch = self.command("diff", "example-fixture", "--local").stdout
        self.assertIn("+Our customized guidance.", patch)
        self.assertIn("-Original skill guidance.", patch)
        self.assertEqual(skill.read_text(), "Our customized guidance.\n")
        self.assertFalse((self.repo / ".cache").exists())

    def test_deleted_upstream_directory_is_reported(self):
        self.pin()
        shutil.rmtree(self.primary)
        self.commit()
        patch = self.command("diff", "example-fixture").stdout
        self.assertIn("deleted file mode", patch)
        self.assertIn("-Original skill guidance.", patch)

    def test_baseline_survives_rewritten_remote_history_and_missing_cache(self):
        self.pin()
        self.git("checkout", "--orphan", "replacement")
        (self.primary / "SKILL.md").write_text("Rewritten upstream history.\n")
        self.commit()
        self.git("branch", "-M", "main")
        shutil.rmtree(self.repo / ".cache")
        patch = self.command("diff", "example-fixture").stdout
        self.assertIn("-Original skill guidance.", patch)
        self.assertIn("+Rewritten upstream history.", patch)

    def test_can_pin_historical_commit_and_compare_specific_revision(self):
        (self.primary / "SKILL.md").write_text("Later upstream revision.\n")
        self.commit()
        self.pin("--ref", self.original)
        self.assertEqual(self.manifest()["imports"]["example-fixture"]["base_commit"], self.original)
        patch = self.command("diff", "example-fixture", "--ref", self.original).stdout
        self.assertIn("No upstream changes", patch)

    def test_missing_ref_is_an_error_not_no_changes(self):
        self.pin()
        before = self.manifest_path.read_bytes()
        result = self.command("diff", "example-fixture", "--ref", "missing-branch", success=False)
        self.assertNotIn("No upstream changes", result.stdout)
        self.assertEqual(self.manifest_path.read_bytes(), before)

    def test_modified_baseline_is_rejected(self):
        self.pin()
        path = self.repo / "upstream/bases/example-fixture.zip"
        path.write_bytes(path.read_bytes() + b"Changed baseline.\n")
        result = self.command("check", success=False)
        self.assertIn("modified baseline", result.stderr)

    def test_repin_is_rejected(self):
        self.pin()
        before = self.manifest_path.read_bytes()
        result = self.pin(success=False)
        self.assertIn("already pinned", result.stderr)
        self.assertEqual(self.manifest_path.read_bytes(), before)

    def test_invalid_or_missing_source_paths_do_not_register_import(self):
        for path in ("../escape", "/absolute", "missing-path"):
            with self.subTest(path=path):
                self.pin("--include", path, success=False)
                self.assertFalse(self.manifest()["imports"])
                self.assertFalse((self.repo / "upstream/bases/example-fixture.zip").exists())

    def test_upstream_symlinks_are_not_materialized(self):
        (self.primary / "link").symlink_to("/tmp")
        self.commit()
        result = self.pin(success=False)
        self.assertIn("symlink or submodule", result.stderr)
        self.assertFalse(self.manifest()["imports"])
        self.assertFalse((self.repo / "upstream/bases/example-fixture.zip").exists())

    def test_empty_list_and_check_do_not_fetch(self):
        self.assertIn("No tracked imports", self.command("list").stdout)
        self.assertIn("No tracked imports", self.command("check").stdout)
        self.assertFalse((self.repo / ".cache").exists())

    def test_snapshot_survives_normal_git_commit_and_clone_with_upstream_ignore_rules(self):
        (self.primary / ".gitignore").write_text("*.txt\n")
        (self.primary / ".gitattributes").write_text("* text eol=crlf\n")
        self.commit()
        self.pin()
        archive_path = "upstream/bases/example-fixture.zip"
        original_archive = (self.repo / archive_path).read_bytes()
        self.git("init", "--initial-branch=main", directory=self.repo)
        self.git("add", ".", directory=self.repo)
        self.git("commit", "-m", "Track an upstream skill", directory=self.repo)
        cloned = self.root / "fresh astack clone"
        self.git("clone", "--quiet", str(self.repo), str(cloned))
        self.assertEqual((cloned / archive_path).read_bytes(), original_archive)
        with zipfile.ZipFile(cloned / archive_path) as archive:
            self.assertEqual(archive.read("plugin/skills/example/old.txt"), b"An old reference.\n")
        self.repo = cloned
        self.assertIn("No upstream changes", self.command("check").stdout)

    def test_concurrent_registration_is_rejected_and_can_retry_without_lost_imports(self):
        lock = self.repo / ".cache/upstream/operation.lock"
        lock.parent.mkdir(parents=True)
        before = self.manifest_path.read_bytes()
        with lock.open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            result = self.pin(success=False)
            self.assertIn("Another upstream operation is running", result.stderr)
            self.assertEqual(self.manifest_path.read_bytes(), before)
            self.assertFalse((self.repo / "upstream/bases/example-fixture.zip").exists())
        self.pin()
        self.command("track", "second-origin", "--source", "fixture", "--path", "LICENSE",
                     "--local", "skills/custom-example/LICENSE")
        self.assertEqual(set(self.manifest()["imports"]), {"example-fixture", "second-origin"})
        self.assertIn("No upstream changes", self.command("check").stdout)


if __name__ == "__main__":
    unittest.main()
