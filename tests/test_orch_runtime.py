import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/orchestrate/scripts"


class OrchestrationRuntimeTests(unittest.TestCase):
    def test_imported_runtime_and_adaptations(self):
        bun = shutil.which("bun")
        dependencies = os.environ.get("ASTACK_ORCH_NODE_MODULES")
        if not bun or not dependencies:
            self.skipTest("requires Bun and prepared ASTACK_ORCH_NODE_MODULES; see orchestrate/CLI.md")
        with tempfile.TemporaryDirectory(prefix="astack-orch-runtime-") as directory:
            root = Path(directory)
            shutil.copytree(SCRIPTS, root / "scripts", ignore=shutil.ignore_patterns("node_modules"))
            scripts = root / "scripts"
            shutil.copytree(dependencies, scripts / "node_modules", symlinks=True)
            install_key = hashlib.sha256(
                (scripts / "package.json").read_bytes() + b"\0" + (scripts / "bun.lock").read_bytes()
            ).hexdigest()
            (scripts / "node_modules/.poteto-mode-tools-install-key").write_text(install_key + "\n")
            env = dict(os.environ, ORCH_OFFLINE="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
            result = subprocess.run(
                [bun, "test", "orch"], cwd=scripts, env=env,
                capture_output=True, text=True, timeout=120, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("0 fail", result.stderr)
            typecheck = subprocess.run(
                [bun, "run", "typecheck"], cwd=scripts, env=env,
                capture_output=True, text=True, timeout=120, check=False,
            )
            self.assertEqual(typecheck.returncode, 0, typecheck.stdout + typecheck.stderr)


if __name__ == "__main__":
    unittest.main()
