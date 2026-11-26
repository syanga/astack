"""Check the repository's own skills against the installer's rules and against each other, without installing."""

from pathlib import Path
import re
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

    def test_review_pr_copies_provider_execution_invocations(self):
        def invocations(skill_path):
            text = (SOURCE / "skills" / skill_path).read_text(encoding="utf-8")
            blocks = re.findall(r"^```bash\n(.*?)^```", text, re.DOTALL | re.MULTILINE)
            return {block for block in blocks if block.startswith(("codex ", "claude ", "agy "))}

        providers = invocations("arena/PROVIDERS.md")
        self.assertEqual({block.split()[0] for block in providers}, {"codex", "claude", "agy"})
        self.assertEqual(invocations("review-pr/SKILL.md"), providers)


if __name__ == "__main__":
    unittest.main()
