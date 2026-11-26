import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/ad-refine/scripts/rounds.py"

FAKE_PANEL = """
import json
from pathlib import Path
import sys

panel, image, copy, log = sys.argv[1:5]
scores = json.loads(Path(image).read_text(encoding="utf-8"))
Path(copy).read_text(encoding="utf-8")
with open(log, "a", encoding="utf-8") as calls:
    calls.write("{} {}\\n".format(panel, Path(image).parent.name))
if scores.get("fail") == panel:
    sys.exit("panel {} crashed".format(panel))
print("drawing personas")
print(json.dumps({"purchase_intent": {"mean": scores[panel]}}))
print()
"""


def cells(line):
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line)[1:-1]]


def row(output, number):
    lines = output.splitlines()
    header = cells(lines[0])
    for line in lines[2:]:
        values = cells(line)
        if values and values[0] == str(number):
            return dict(zip(header, values))
    return None


class RoundsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="astack ad refine ")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.run_folder = self.home / "brand refine"
        self.run_folder.mkdir()
        fake = self.home / "fake panel.py"
        fake.write_text(FAKE_PANEL, encoding="utf-8")
        self.log = self.home / "panel calls.log"
        self.template = " ".join([shlex.quote(sys.executable), shlex.quote(str(fake)), "{panel}", "{image}", "{copy}",
                                  shlex.quote(str(self.log)), "'{\"seed\": 7}'"])
        self.votes = 0

    def version(self, name, a, b, fail=None):
        folder = self.run_folder / name
        folder.mkdir(exist_ok=True)
        scores = {"A": a, "B": b} | ({"fail": fail} if fail else {})
        (folder / "ad.png").write_text(json.dumps(scores), encoding="utf-8")
        (folder / "copy.txt").write_text("Sleep through the night.\n", encoding="utf-8")

    def vote(self, silent=(), **flaws):
        self.votes += 1
        craft = {member: None if member in silent else {
            version: [{"severity": severity, "check": "claims", "quote": "US$36"}
                      for severity in (severities if member == "opus" else [])]
            for version, severities in flaws.items()} for member in ("opus", "sol")}
        path = self.home / "craft {}.json".format(self.votes)
        path.write_text(json.dumps(craft), encoding="utf-8")
        return path

    def vote_without(self, member, **flaws):
        path = self.vote(**flaws)
        craft = json.loads(path.read_text(encoding="utf-8"))
        del craft[member]
        path.write_text(json.dumps(craft), encoding="utf-8")
        return path

    def rounds(self, *arguments):
        return subprocess.run([sys.executable, str(SCRIPT), *arguments], capture_output=True, text=True, timeout=60)

    def start(self, craft, *arguments):
        return self.rounds("start", str(self.run_folder), "--version", "v0", "--craft", str(craft),
                           "--panel-command", self.template, "--margin", "0.25", *arguments)

    def round(self, version, craft, targets="headline"):
        return self.rounds("round", str(self.run_folder), "--version", version, "--craft", str(craft),
                           "--targets", targets)

    def calls(self):
        return sorted(self.log.read_text(encoding="utf-8").splitlines()) if self.log.exists() else []

    def assertCalls(self, expected):
        self.assertEqual(self.calls(), sorted(expected))

    def assertRefused(self, result):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def assertStatus(self, result, status):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], status)

    def test_a_version_that_passes_craft_and_rises_on_both_panels_is_kept_and_faces_the_next_round(self):
        self.version("v0", 2.5, 2.5)
        self.version("v1", 3.0, 2.75)
        self.version("v2", 3.0, 3.0)
        self.assertStatus(self.start(self.vote(v0=["minor"])), "next: round 1 against v0")

        kept = self.round("v1", self.vote(v1=["minor"], v0=["minor"]), targets="headline | price")

        self.assertStatus(kept, "next: round 2 against v1")
        self.assertEqual(row(kept.stdout, 1), {
            "Round": "1", "Targets": "headline \\| price", "New": "v1", "Best": "v0", "Craft": "pass vs pass",
            "Panel A": "3.000 vs 2.500 (+0.500)", "Panel B": "2.750 vs 2.500 (+0.250)",
            "Decision": "keep: panel A +0.500 > 0.250, panel B +0.250 > 0"})
        self.assertEqual((self.run_folder / "table.md").read_text(encoding="utf-8"), kept.stdout)
        self.assertCalls(["A v1", "A v0", "B v1", "B v0"])

        self.round("v2", self.vote(v2=["minor"], v1=["minor"]))

        self.assertCalls(["A v1", "A v0", "B v1", "B v0", "A v2", "A v1"])

    def test_a_rise_on_panel_a_of_exactly_the_margin_reverts_without_running_panel_b(self):
        self.version("v0", 2.5, 2.5)
        self.version("v1", 2.75, 3.0)
        self.start(self.vote(v0=["minor"]))

        result = self.round("v1", self.vote(v1=[], v0=["minor"]))

        self.assertStatus(result, "next: round 2 against v0")
        self.assertEqual(row(result.stdout, 1)["Panel B"], "-")
        self.assertEqual(row(result.stdout, 1)["Decision"], "revert: panel A +0.250 <= 0.250")
        self.assertCalls(["A v1", "A v0"])

    def test_a_rise_that_equals_a_decimal_margin_reverts(self):
        self.version("v0", 2.0, 2.0)
        self.version("v1", 2.2, 3.0)
        self.rounds("start", str(self.run_folder), "--version", "v0", "--craft", str(self.vote(v0=["minor"])),
                    "--panel-command", self.template, "--margin", "0.2")

        result = self.round("v1", self.vote(v1=[], v0=["minor"]))

        self.assertStatus(result, "next: round 2 against v0")
        self.assertEqual(row(result.stdout, 1)["Decision"], "revert: panel A +0.200 <= 0.200")
        self.assertCalls(["A v1", "A v0"])

    def test_panel_b_without_a_rise_reverts(self):
        self.version("v0", 2.5, 2.5)
        self.version("v1", 3.0, 2.5)
        self.start(self.vote(v0=["minor"]))

        result = self.round("v1", self.vote(v1=[], v0=["minor"]))

        self.assertStatus(result, "next: round 2 against v0")
        self.assertEqual(row(result.stdout, 1)["Decision"], "revert: panel B +0.000 <= 0")
        self.assertCalls(["A v1", "A v0", "B v1", "B v0"])

    def test_a_major_flaw_in_the_new_version_reverts_without_running_a_panel(self):
        self.version("v0", 2.5, 2.5)
        self.version("v1", 3.0, 3.0)
        self.version("v2", 3.0, 3.0)
        self.start(self.vote(v0=["minor"]))

        failed = self.round("v1", self.vote(v1=["minor", "major"], v0=["minor"]))

        self.assertStatus(failed, "next: round 2 against v0")
        self.assertEqual(row(failed.stdout, 1), {
            "Round": "1", "Targets": "headline", "New": "v1", "Best": "v0", "Craft": "fail vs pass",
            "Panel A": "-", "Panel B": "-", "Decision": "revert: craft fail"})
        self.assertEqual(self.calls(), [])

        self.round("v2", self.vote(v2=["minor"], v0=["minor"]))

        self.assertCalls(["A v2", "A v0", "B v2", "B v0"])

    def test_a_gate_fix_keeps_drops_within_the_margin_and_reverts_a_drop_beyond_it(self):
        self.version("v0", 2.5, 2.5)
        self.version("v1", 2.125, 3.0)
        self.version("v2", 2.25, 2.375)
        self.assertStatus(self.start(self.vote(v0=["major"])), "next: round 1 against v0")

        dropped = self.round("v1", self.vote(v1=["minor"], v0=["major"]))

        self.assertStatus(dropped, "next: round 2 against v0")
        self.assertEqual(row(dropped.stdout, 1)["Craft"], "pass vs fail")
        self.assertEqual(row(dropped.stdout, 1)["Decision"], "revert (gate fix): panel A -0.375 < -0.250")
        self.assertCalls(["A v1", "A v0"])

        held = self.round("v2", self.vote(v2=["minor"], v0=["major"]))

        self.assertStatus(held, "next: round 3 against v2")
        self.assertEqual(row(held.stdout, 2)["Decision"],
                         "keep (gate fix): panel A -0.250 >= -0.250, panel B -0.125 >= -0.250")

    def test_an_inconclusive_vote_reverts_without_running_a_panel(self):
        self.version("v0", 2.5, 2.5)
        self.version("v1", 3.0, 3.0)
        self.version("v2", 3.0, 3.0)
        self.start(self.vote(v0=["minor"]))

        result = self.round("v1", self.vote(silent=("sol",), v1=[], v0=[]))

        self.assertStatus(result, "next: round 2 against v0")
        self.assertEqual(row(result.stdout, 1)["Craft"], "inconclusive vs inconclusive")
        self.assertEqual(row(result.stdout, 1)["Panel A"], "-")
        self.assertEqual(row(result.stdout, 1)["Decision"], "revert: craft inconclusive")
        self.assertEqual(self.calls(), [])

        self.round("v2", self.vote(v2=["minor"], v0=["minor"]))

        self.assertCalls(["A v2", "A v0", "B v2", "B v0"])

    def test_the_same_member_without_a_score_in_two_votes_in_a_row_stops_the_run(self):
        self.version("v0", 2.5, 2.5)
        self.version("v1", 3.0, 3.0)
        self.version("v2", 3.0, 3.0)
        self.assertStatus(self.start(self.vote(silent=("sol",), v0=["minor"])), "next: round 1 against v0")

        result = self.round("v1", self.vote(silent=("sol",), v1=[], v0=["minor"]))

        self.assertStatus(result, "stop: no score from sol in two votes in a row")
        refused = self.round("v2", self.vote(v2=["minor"], v0=["minor"]))
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertEqual(self.calls(), [])

    def test_different_members_without_a_score_in_consecutive_votes_do_not_stop_the_run(self):
        self.version("v0", 2.5, 2.5)
        self.version("v1", 3.0, 3.0)
        self.version("v2", 3.0, 3.0)
        self.start(self.vote(silent=("sol",), v0=["minor"]))

        self.assertStatus(self.round("v1", self.vote(silent=("opus",), v1=[], v0=[])), "next: round 2 against v0")
        self.assertStatus(self.round("v2", self.vote(silent=("sol",), v2=[], v0=[])), "next: round 3 against v0")

    def test_the_run_stops_when_the_round_budget_is_spent(self):
        for name in ["v0", "v1", "v2", "v3"]:
            self.version(name, 2.5, 2.5)
        self.start(self.vote(v0=["minor"]), "--rounds", "2")

        self.assertStatus(self.round("v1", self.vote(v1=["major"], v0=["minor"])), "next: round 2 against v0")
        self.assertStatus(self.round("v2", self.vote(v2=["major"], v0=["minor"])), "stop: budget")

        refused = self.round("v3", self.vote(v3=[], v0=["minor"]))

        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        table = self.rounds("table", str(self.run_folder))
        self.assertStatus(table, "stop: budget")
        self.assertIsNotNone(row(table.stdout, 2))
        self.assertIsNone(row(table.stdout, 3))

    def test_a_start_version_without_flaws_stops_the_run_at_once(self):
        self.version("v0", 2.5, 2.5)
        self.version("v1", 3.0, 3.0)

        self.assertStatus(self.start(self.vote(v0=[])), "stop: empty flaw queue")

        refused = self.round("v1", self.vote(v1=[], v0=[]))
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertEqual(self.calls(), [])

    def test_keeping_a_version_without_flaws_stops_the_run(self):
        self.version("v0", 2.5, 2.5)
        self.version("v1", 3.0, 3.0)
        self.start(self.vote(v0=["minor"]))

        result = self.round("v1", self.vote(v1=[], v0=["minor"]))

        self.assertStatus(result, "stop: empty flaw queue")
        self.assertTrue(row(result.stdout, 1)["Decision"].startswith("keep:"), result.stdout)

    def test_a_failed_panel_records_nothing_and_the_same_command_can_run_again(self):
        self.version("v0", 2.5, 2.5)
        self.version("v1", 3.0, 2.75, fail="B")
        self.start(self.vote(v0=["minor"]))
        craft = self.vote(v1=["minor"], v0=["minor"])

        failed = self.round("v1", craft)

        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertIn("panel B on v1", failed.stderr)
        saved = Path(failed.stderr.rsplit("see ", 1)[1].strip())
        self.assertIn("panel B crashed", saved.read_text(encoding="utf-8"))
        table = self.rounds("table", str(self.run_folder))
        self.assertStatus(table, "next: round 1 against v0")
        self.assertIsNone(row(table.stdout, 1))

        self.version("v1", 3.0, 2.75)
        retried = self.round("v1", craft)

        self.assertStatus(retried, "next: round 2 against v1")
        self.assertEqual(row(retried.stdout, 1)["Decision"], "keep: panel A +0.500 > 0.250, panel B +0.250 > 0")
        self.assertCalls(["A v1", "A v0", "B v1", "A v1", "A v0", "B v1", "B v0"])

    def test_a_round_refuses_a_version_or_vote_it_cannot_score_and_records_nothing(self):
        self.version("v0", 2.5, 2.5)
        (self.run_folder / "v1").mkdir()
        (self.run_folder / "v1/copy.txt").write_text("No image yet.\n", encoding="utf-8")
        self.version("v2", 3.0, 3.0)
        (self.run_folder / "v2/ad.jpg").write_bytes(b"second image")
        self.version("v3", 3.0, 3.0)
        self.start(self.vote(v0=["minor"]))

        for label, version, craft in [
            ("the current best", "v0", self.vote(v0=["minor"])),
            ("a folder without an image", "v1", self.vote(v1=[], v0=["minor"])),
            ("a folder with two images", "v2", self.vote(v2=[], v0=["minor"])),
            ("a vote without the current best", "v3", self.vote(v3=[])),
            ("a flaw without a severity", "v3", self.vote(v3=["blocker"], v0=["minor"])),
            ("a vote that leaves out a member", "v3", self.vote_without("sol", v3=[], v0=["minor"])),
        ]:
            with self.subTest(label):
                self.assertRefused(self.round(version, craft))
        table = self.rounds("table", str(self.run_folder))
        self.assertIsNone(row(table.stdout, 1))
        self.assertEqual(self.calls(), [])

        accepted = self.round("v3", self.vote(v3=["minor"], v0=["minor"]))

        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertCalls(["A v3", "A v0", "B v3", "B v0"])
        self.assertEqual(self.round("v3", self.vote(v3=["minor"], v1=["minor"])).returncode, 1)

    def test_a_second_start_is_refused(self):
        self.version("v0", 2.5, 2.5)
        self.assertStatus(self.start(self.vote(v0=["minor"])), "next: round 1 against v0")

        again = self.start(self.vote(v0=[]))

        self.assertEqual(again.returncode, 1, again.stdout + again.stderr)
        self.assertStatus(self.rounds("table", str(self.run_folder)), "next: round 1 against v0")

    def test_a_budget_above_five_rounds_is_rejected(self):
        self.version("v0", 2.5, 2.5)

        rejected = self.start(self.vote(v0=["minor"]), "--rounds", "6")

        self.assertEqual(rejected.returncode, 2, rejected.stdout + rejected.stderr)
        self.assertStatus(self.start(self.vote(v0=["minor"]), "--rounds", "5"), "next: round 1 against v0")


if __name__ == "__main__":
    unittest.main()
