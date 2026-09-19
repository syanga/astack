import csv
from datetime import datetime, timezone
import io
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/show-me-your-work/scripts/log.sh"


class DecisionLogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="astack decision log ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.log = self.root / "program café/decisions.tsv"

    def run_log(self, *args, success=True):
        result = subprocess.run(
            ["bash", str(SCRIPT), str(self.log), *args],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
        return result

    def rows(self):
        return list(csv.DictReader(io.StringIO(self.log.read_text()), delimiter="\t"))

    def test_creates_parent_and_appends_timestamped_decisions_without_rewriting_history(self):
        before = datetime.now(timezone.utc).replace(microsecond=0)
        self.run_log("api", "Use one parser", "Share validation", "commit abc123", "tests green")
        first = self.log.read_bytes()
        self.run_log("api", "Correct the earlier result", "Integration check failed", "reports/failure.txt", "open")
        self.assertTrue(self.log.read_bytes().startswith(first))
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual({key: value for key, value in rows[0].items() if key != "ts"}, {
            "phase": "api", "decision": "Use one parser", "why": "Share validation",
            "evidence": "commit abc123", "result": "tests green",
        })
        self.assertEqual(rows[1]["result"], "open")
        stamp = datetime.strptime(rows[0]["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertLessEqual(before, stamp)
        self.assertLessEqual(stamp, datetime.now(timezone.utc))

    def test_sanitizes_multiline_cells_and_spreadsheet_formula_prefixes(self):
        self.run_log("=phase", "+decision\twith tabs", "-why\nwith lines", "@evidence\rpath", "plain café")
        row = self.rows()[0]
        self.assertEqual([row[key] for key in ["phase", "decision", "why", "evidence", "result"]], [
            "'=phase", "'+decision with tabs", "'-why with lines", "'@evidence path", "plain café",
        ])
        self.assertEqual(len(self.log.read_text().splitlines()), 2)

    def test_rejects_legacy_empty_and_unterminated_headers_without_changing_the_log(self):
        self.log.parent.mkdir()
        for original in [
            "question\tevidence\tdecision\tunits\nWhich parser?\tabc\tReuse\tapi\n",
            "",
            "ts\tphase\tdecision\twhy\tevidence\tresult",
        ]:
            with self.subTest(original=original):
                self.log.write_text(original)
                result = self.run_log("api", "Choose parser", "Consistency", "abc", "open", success=False)
                self.assertIn("incompatible header", result.stderr)
                self.assertEqual(self.log.read_text(), original)

    def test_transitions_to_current_format_without_rewriting_legacy_facts(self):
        self.log.parent.mkdir()
        original = "question\tevidence\tdecision\tunits\nWhich parser?\tabc\tReuse\tapi\n"
        self.log.write_text(original)
        archive = self.log.with_name("decisions.legacy-20260919.tsv")
        self.log.rename(archive)
        self.run_log("setup", "Adopt upstream decision format", "Keep complete reasons and outcomes", str(archive), "Earlier history retained")
        self.assertEqual(archive.read_text(), original)
        self.assertEqual(self.rows()[0]["evidence"], str(archive))
        self.assertEqual(self.rows()[0]["result"], "Earlier history retained")

    def test_usage_error_creates_no_log(self):
        result = self.run_log("api", success=False)
        self.assertIn("usage:", result.stderr)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
