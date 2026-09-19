import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


TEMPLATE = Path(__file__).resolve().parent.parent / "skills/wizard/template.sh"


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="astack wizard ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = os.environ.copy()
        self.env.update(ENV_FILE=str(self.root / ".env"), GH_LOG=str(self.root / "secret"))
        binary = self.root / "bin"
        binary.mkdir()
        self.env["PATH"] = str(binary) + os.pathsep + os.defpath
        gh = binary / "gh"
        gh.write_text("#!" + sys.executable + '''
import os
from pathlib import Path
import sys

if sys.argv[1:] == ["auth", "status"]:
    sys.exit(0)
if sys.argv[1:] == ["secret", "set", "TOKEN"]:
    Path(os.environ["GH_LOG"]).write_text(sys.stdin.read())
    sys.exit(0)
sys.exit(1)
''')
        gh.chmod(0o755)

    def run_wizard(self, stages, input=""):
        script = self.root / "wizard.sh"
        script.write_text(TEMPLATE.read_text().split("# STAGES:", 1)[0] + "\n" + stages)
        return subprocess.run(["/bin/bash", str(script)], input=input, text=True,
                              capture_output=True, cwd=self.root, env=self.env)

    def test_secret_survives_write_and_rerun_without_disclosure(self):
        env_file = self.root / ".env"
        env_file.write_text("OTHER=keep\nTOKEN=old\nTOKEN=duplicate\n")
        stages = '''
ask_secret TOKEN "Token:"
write_env TOKEN "$TOKEN"
set_secret TOKEN "$TOKEN"
finish
'''
        for response in ("  abc#def=xyz  \n", "\n"):
            result = self.run_wizard(stages, response)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(env_file.read_text(), "OTHER=keep\nTOKEN='  abc#def=xyz  '\n")
            self.assertEqual((self.root / "secret").read_text(), "  abc#def=xyz  ")
            self.assertNotIn("abc#def", result.stdout + result.stderr)

    def test_reuses_existing_quoted_values_as_literal_secrets(self):
        for stored in ("'abc#123'", '"abc#123"', " 'abc#123' # comment"):
            with self.subTest(stored=stored):
                (self.root / ".env").write_text("TOKEN=" + stored + "\n")
                result = self.run_wizard('ask_secret TOKEN "Token:"\nset_secret TOKEN "$TOKEN"\n', "\n")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((self.root / "secret").read_text(), "abc#123")

    def test_adds_value_to_existing_file_and_preserves_apostrophe(self):
        env_file = self.root / ".env"
        env_file.write_text("OTHER=keep\n")
        stages = 'ask TOKEN "Token:"\nwrite_env TOKEN "$TOKEN"\nset_secret TOKEN "$TOKEN"\n'
        for response in ("it's a token\n", "\n"):
            result = self.run_wizard(stages, response)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(env_file.read_text(), 'OTHER=keep\nTOKEN="it\'s a token"\n')
            self.assertEqual((self.root / "secret").read_text(), "it's a token")

    def test_creates_environment_file_on_first_run(self):
        result = self.run_wizard('ask TOKEN "Token:"\nwrite_env TOKEN "$TOKEN"\n', "abc123\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / ".env").read_text(), "TOKEN=abc123\n")

    def test_eof_stops_before_later_writes(self):
        for prompt in ('ask TOKEN "Token:"', 'ask_secret TOKEN "Token:"',
                       'pause "Continue?"', 'if confirm "Continue?"; then :; fi'):
            with self.subTest(prompt=prompt):
                env_file = self.root / ".env"
                env_file.write_text("TOKEN=before\n")
                result = self.run_wizard(prompt + '\nwrite_env TOKEN after\n')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(env_file.read_text(), "TOKEN=before\n")

    def test_ambiguous_values_fail_without_changing_file(self):
        for value in ("one\ntwo", "both'\"quotes", "back\\slash", "${OTHER}"):
            with self.subTest(value=value):
                env_file = self.root / ".env"
                env_file.write_text("TOKEN=before\n")
                self.env["VALUE"] = value
                result = self.run_wizard('write_env TOKEN "$VALUE"\n')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(env_file.read_text(), "TOKEN=before\n")
                self.assertNotIn(value, result.stdout + result.stderr)

    def test_unsupported_existing_syntax_stops_before_writes(self):
        for stored in ('"first\nsecond"', '"${OTHER}"', '"back\\nslash"'):
            with self.subTest(stored=stored):
                env_file = self.root / ".env"
                original = "TOKEN=" + stored + "\n"
                env_file.write_text(original)
                result = self.run_wizard('ask_secret TOKEN "Token:"\nwrite_env TOKEN after\n', "\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(env_file.read_text(), original)
                self.assertNotIn(stored, result.stdout + result.stderr)

    def test_action_only_wizard_finishes(self):
        result = self.run_wizard('stage "Manual action"\npause "Done?"\nfinish\n', "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Setup complete", result.stdout)


if __name__ == "__main__":
    unittest.main()
