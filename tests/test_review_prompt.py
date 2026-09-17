import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / "skills/review-pr/scripts/build_prompt.py"
spec = importlib.util.spec_from_file_location("build_prompt", SCRIPT)
build_prompt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_prompt)


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, text):
        path = self.root / name
        path.write_text(text)
        return str(path)

    def build(self, *extra):
        out = str(self.root / "prompt.md")
        build_prompt.main(["code", "--intent", self.write("intent", "Fix the parser."),
                           "--diff", self.write("diff", "+ template line\n{{RUBRIC}}\n{{DIFF}}\n+ end"),
                           "--commits", self.write("commits", "abc123 fix"), "--out", out, *extra])
        return Path(out).read_text()

    def test_markers_inside_the_diff_survive(self):
        prompt = self.build("--tree", "/tmp/tree")
        self.assertIn("+ template line\n{{RUBRIC}}\n{{DIFF}}\n+ end", prompt)
        self.assertEqual(prompt.count("> Fix the parser."), 1)
        self.assertIn("The tree at /tmp/tree contains this change.", prompt)
        self.assertIn("This is the first review round on this change.", prompt)

    def test_lenses_prune_the_rubric_and_its_principles(self):
        prompt = self.build("--lenses", "verification")
        self.assertIn("## Verification", prompt)
        self.assertIn("#### Principle: prove-it-works.md", prompt)
        self.assertNotIn("## Security", prompt)
        self.assertNotIn("#### Principle: laziness-protocol.md", prompt)

    def test_a_later_round_carries_the_earlier_verdicts(self):
        prompt = self.build("--prior", self.write("prior", "Round 1 dismissed the StatusContext claim."))
        self.assertIn("Review only the change shown here.", prompt)
        self.assertIn("Round 1 dismissed the StatusContext claim.", prompt)


if __name__ == "__main__":
    unittest.main()
