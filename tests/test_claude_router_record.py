import json
from pathlib import Path
import re
import subprocess
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
COMPONENT = REPO / "components/claude-router"


class AcceptedRecordTest(unittest.TestCase):
    def test_the_accepted_record_covers_the_router_code_in_this_checkout(self):
        # Install and activation refuse a router whose code has no passing live
        # acceptance run, so a router change that merges without one blocks
        # every router update until a new record lands.
        record = json.loads((COMPONENT / "accepted.json").read_text())
        printed = subprocess.run(
            [sys.executable, "scripts/claude_router.py", "identity", "--code-only"],
            cwd=REPO, capture_output=True, text=True, check=True)
        code = json.loads(printed.stdout)
        pinned = re.findall(r"github\.com/router-for-me/CLIProxyAPI/v\d+ (\S+)",
                            (COMPONENT / "go.mod").read_text())[0]
        recorded = {key: record.get(key) for key in ("result", "sdk", "code_sha256", "helpers_sha256")}
        expected = {"result": "pass", "sdk": pinned, **code}
        self.assertEqual(
            recorded, expected,
            "components/claude-router/accepted.json does not match this checkout. "
            "Run a live acceptance run at this code and commit its record "
            "(components/claude-router/CONTRACT.md, Acceptance record).")


if __name__ == "__main__":
    unittest.main()
