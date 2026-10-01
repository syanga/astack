"""Check the repository's own skills against the installer's rules, without installing."""

from pathlib import Path
import subprocess
import sys
import unittest

SOURCE = Path(__file__).resolve().parent.parent


class RepositorySkillsTests(unittest.TestCase):
    def test_every_skill_passes_install_checks(self):
        result = subprocess.run(
            [sys.executable, "-B", "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); import manage; manage.skill_files()",
             str(SOURCE / "scripts")],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
