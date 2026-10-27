import os
from pathlib import Path
import pty
import select
import stat
import subprocess
import sys
import tempfile
import time
import unittest


TEMPLATE = Path(__file__).resolve().parent.parent / "skills/wizard/template.sh"


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="astack wizard ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = os.environ.copy()
        self.env.update(ENV_FILE=str(self.root / ".env"), GH_LOG=str(self.root / "secret"),
                        HOME=str(self.root))
        self.env.pop("INPUTRC", None)
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
        script = self.library_script(stages)
        return subprocess.run(["/bin/bash", str(script)], input=input, text=True,
                              capture_output=True, cwd=self.root, env=self.env)

    def run_in_terminal(self, script, keystrokes=b""):
        controller, terminal = pty.openpty()
        process = subprocess.Popen(["/bin/bash", str(script)], stdin=terminal, stdout=terminal,
                                   stderr=subprocess.PIPE, cwd=self.root, env=self.env)
        os.close(terminal)
        os.write(controller, keystrokes)
        output = b""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            ready, _, _ = select.select([controller], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(controller, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
            elif process.poll() is not None:
                break
        os.close(controller)
        stderr = process.communicate(timeout=10)[1].decode()
        return process.returncode, output, stderr

    def library_script(self, stages):
        script = self.root / "wizard.sh"
        script.write_text(TEMPLATE.read_text().split("# STAGES:", 1)[0] + "\n" + stages)
        return script

    def test_clear_falls_back_when_tput_clear_fails(self):
        tput = self.root / "bin/tput"
        tput.write_text("#!/bin/sh\nexit 1\n")
        tput.chmod(0o755)
        code, output, stderr = self.run_in_terminal(self.library_script('stage "One"\nfinish\n'))
        self.assertEqual(code, 0, stderr)
        self.assertIn(b"\033[2J", output)
        self.assertIn(b"Setup complete", output)

    def test_open_url_warns_without_a_browser_opener(self):
        self.env["PATH"] = str(self.root / "bin")
        result = self.run_wizard('open_url "https://example.com/keys"\nfinish\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("visit it manually: https://example.com/keys", result.stdout)

    def test_ask_accepts_arrow_key_edits(self):
        script = self.library_script('ask TOKEN "Token:"\nwrite_env TOKEN "$TOKEN"\n')
        code, _, stderr = self.run_in_terminal(script, b"abd\x1b[Dc\r")
        self.assertEqual(code, 0, stderr)
        self.assertEqual((self.root / ".env").read_text(), "TOKEN=abcd\n")

    def test_ask_keeps_a_typed_tab_instead_of_completing_a_file_name(self):
        (self.root / "zfile1").write_text("")
        script = self.library_script('ask TOKEN "Token:"\nwrite_env TOKEN "$TOKEN"\n')
        code, _, stderr = self.run_in_terminal(script, b"z\tq\r")
        self.assertEqual(code, 0, stderr)
        self.assertEqual((self.root / ".env").read_text(), "TOKEN='z\tq'\n")

    def test_ask_prints_its_prompt_when_input_is_piped(self):
        result = self.run_wizard('ask TOKEN "Token:"\n', "abc\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Token:", result.stdout)

    def test_writes_through_a_symlinked_environment_file_and_keeps_its_mode(self):
        target = self.root / "shared.env"
        target.write_text("OTHER=keep\n")
        target.chmod(0o644)
        link = self.root / ".env"
        link.symlink_to(target)
        result = self.run_wizard('write_env TOKEN abc123\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_text(), "OTHER=keep\nTOKEN=abc123\n")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    def test_editing_the_script_mid_run_does_not_change_the_run(self):
        script = self.root / "wizard.sh"
        script.write_text(TEMPLATE.read_text())
        process = subprocess.Popen(["/bin/bash", str(script)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   cwd=self.root, env=self.env)
        seen = b""
        while b"Ready to start?" not in seen:
            chunk = process.stdout.read1(4096)
            self.assertTrue(chunk, "wizard exited before its first prompt")
            seen += chunk
        script.write_text("# edited while the wizard runs\n" * 3 + TEMPLATE.read_text())
        stdout, stderr = process.communicate(b"\npk_test_1\nsk_test_2\n", timeout=10)
        self.assertEqual(process.returncode, 0, stderr.decode())
        self.assertEqual((seen + stdout).count(b"Ready to start?"), 1)
        self.assertEqual((self.root / ".env").read_text(),
                         "STRIPE_PUBLISHABLE_KEY=pk_test_1\nSTRIPE_SECRET_KEY=sk_test_2\n")

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

    def test_creates_private_environment_file_on_first_run(self):
        result = self.run_wizard('ask TOKEN "Token:"\nwrite_env TOKEN "$TOKEN"\n', "abc123\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / ".env").read_text(), "TOKEN=abc123\n")
        self.assertEqual(stat.S_IMODE((self.root / ".env").stat().st_mode), 0o600)

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
