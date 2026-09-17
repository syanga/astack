import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

LAUNCHER = Path(__file__).resolve().parent.parent / "skills/open-pr/scripts/install_gitleaks_hook.py"


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="astack launcher ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = dict(os.environ, XDG_STATE_HOME=str(self.root / "state"))

    def run_launcher(self, *args):
        return subprocess.run([sys.executable, str(LAUNCHER), *args], cwd=str(self.root),
                              env=self.env, capture_output=True, text=True)

    def test_missing_source_explains_the_fix(self):
        result = self.run_launcher()
        self.assertEqual(result.returncode, 1)
        self.assertIn("run ./install.sh from the astack checkout", result.stderr)

    def test_runs_git_hooks_from_recorded_checkout(self):
        checkout = self.root / "checkout"
        (checkout / "scripts").mkdir(parents=True)
        log = self.root / "argv.json"
        (checkout / "scripts/git_hooks.py").write_text(
            "import json, sys\njson.dump(sys.argv[1:], open({!r}, 'w'))\n".format(str(log))
        )
        manifest = self.root / "state/astack/manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"version": 2, "targets": {}, "source": str(checkout)}))
        result = self.run_launcher("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(log.read_text()), ["install", "--repo", ".", "--dry-run"])


if __name__ == "__main__":
    unittest.main()
