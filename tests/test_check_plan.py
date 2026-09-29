from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/multi-phase-plan/scripts/check_plan.py"
PLAN = """# Validate configuration plan

The CLI reports invalid configuration before starting a job.

## How to read this

One box is one unit of work. Every box names the evidence that checks it.
Check a box only when its evidence exists.
The program runs `skills/orchestrate/SKILL.md`.
Tests alone are not sufficient verification. A PR is verified only when its applicable unit, live, and perf boxes are checked with evidence.

## Program checklist

### Arm the program
- [ ] Record execution authorization and merge authorization in the handoff.
- [ ] Run an audit at each completion and post a status message.
### Spawn owners
- [ ] Assign PR1 to the CLI owner in the handoff.
### PR mechanics
- [ ] Follow open-pr and save the PR URL.
### Verdict and merge
- [ ] Record the verified SHA, then stop at merge-ready.
### Boot recipe
- [ ] Run `python3 cli.py --config bad.json` in a temporary directory.

## Reject invalid configuration (PR1)

**Depends on.** None.
**Files.**
- [ ] Edit `cli.py` and `test_cli.py`.
**Build.**
- [ ] Validate the config in `main` before starting a job.
**You see.**
- [ ] The CLI prints `Invalid config` and exits 2.
**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its applicable unit, live, and perf boxes are checked with evidence.
- [ ] Add malformed-config coverage. Run `python3 -m unittest test_cli`.
**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its applicable unit, live, and perf boxes are checked with evidence.
- [ ] Lane 1. Regression lane against trunk. Run the CLI with bad.json at trunk and head. Save `cli.log`. Pass when head exits 2 before starting a job.
**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its applicable unit, live, and perf boxes are checked with evidence.
Not applicable. This adds validation on the startup error path only.
**Review gate.** None. No user review remains for this CLI fix.
**Merge.**
- [ ] Record the exact verified SHA and the merge-ready PR URL.

## Close the program
- [ ] Deliver the PR URL and verification receipts.

## Appendix A. Prototype evidence
The reproduction script and output are attached to PR1.
"""

NAMED_PLAN = (
    PLAN.replace("Run `python3 -m unittest test_cli`.", "Probe `PR1.unit`.")
    .replace("Lane 1. Regression", "Lane 1. Probe `PR1.live.1`. Regression")
    + "\n## Appendix B. Probes\n"
    + "```sh probe=PR1.unit\npython3 -m unittest test_cli\n```\n"
    + "~~~sh probe=PR1.live.1\npython3 cli.py --config bad.json\n~~~\n"
)


class CheckPlanTests(unittest.TestCase):
    def run_plan(self, content, *args):
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "plan.md"
            plan.write_text(content, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(plan), *args],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(plan.read_text(encoding="utf-8"), content)
            return result

    def test_probe_selection_uses_identity_after_blocks_move(self):
        plan = PLAN.replace("Run `python3 -m unittest test_cli`.", "Probe `PR1.unit`.")
        plan = plan.replace("Lane 1. Regression", "Lane 1. Probe `PR1.live.1`. Regression")
        unit = "```sh probe=PR1.unit\npython3 -m unittest test_cli\n```\n"
        live = "~~~sh probe=PR1.live.1\npython3 cli.py --config bad.json\n~~~\n"
        for blocks in (unit + live, live + unit):
            with self.subTest(blocks=blocks):
                result = self.run_plan(plan + "\n## Appendix B. Probes\n" + blocks, "--probe", "PR1.live.1")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "python3 cli.py --config bad.json\n")

    def test_invalid_probe_bindings_emit_no_selected_command(self):
        section = NAMED_PLAN[NAMED_PLAN.index("## Reject invalid"):NAMED_PLAN.index("## Close the program")]
        for plan, diagnostic in [
            (NAMED_PLAN.replace("probe=PR1.unit", "probe=PR2.unit"), "undefined probe PR1.unit"),
            (NAMED_PLAN.replace("PR1.live.1", "PR2.live.1"), "matching its unit and lane"),
            (NAMED_PLAN.replace("PR1.live.1", "PR1.live.2"), "matching its unit and lane"),
            (NAMED_PLAN.replace("Probe `PR1.unit`.", "Run the tests."), "needs one Probe reference"),
            (NAMED_PLAN + "```sh probe=PR1.unit\necho duplicate\n```\n", "duplicate probe PR1.unit"),
            (NAMED_PLAN + "```sh probe=PR1.unit.extra\necho unused\n```\n", "has no verification box"),
            (NAMED_PLAN.replace("python3 -m unittest test_cli\n", "\n"), "empty probe PR1.unit"),
            (NAMED_PLAN.replace("probe=PR1.unit", "probe=PR1.unknown"), "probe fence needs"),
            (NAMED_PLAN.replace("(PR1)", "without an ID"), "needs a unit ID"),
            (NAMED_PLAN.replace("## Close the program", section + "## Close the program"), "duplicate unit ID PR1"),
        ]:
            with self.subTest(diagnostic=diagnostic):
                result = self.run_plan(plan, "--probe", "PR1.live.1")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn(diagnostic, result.stderr)
        for probe in ("PR9.live.1", ""):
            result = self.run_plan(NAMED_PLAN, "--probe", probe)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertIn(f"unknown probe {probe}", result.stderr)

    def test_probe_selection_preserves_heredoc_without_executing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "must-not-exist"
            command = f"python3 - <<'PY'\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\nPY\n"
            plan = NAMED_PLAN.replace("python3 -m unittest test_cli\n", command)
            result = self.run_plan(plan, "--probe", "PR1.unit")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, command)
            self.assertFalse(marker.exists())

    def test_performance_probe_reference_is_checked_and_selectable(self):
        plan = NAMED_PLAN.replace(
            "Not applicable. This adds validation on the startup error path only.",
            "- [ ] Metric. Startup in ms.\n- [ ] Probe. `PR1.perf`.\n"
            "- [ ] Baseline. Record trunk first.\n- [ ] Rule. Fail above 10 percent.",
        ) + "```sh probe=PR1.perf\npython3 bench.py\n```\n"
        result = self.run_plan(plan, "--probe", "PR1.perf")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "python3 bench.py\n")
        result = self.run_plan(plan.replace("Probe. `PR1.perf`", "Probe. `PR1.unit`"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("matching its unit and lane", result.stderr)

    def test_cli_accepts_plan_with_live_evidence_and_reasoned_perf_exemption(self):
        result = self.run_plan(PLAN)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Reject invalid configuration (PR1)  boxes=6\n1 PR sections, 0 problems\n")

    def test_missing_live_receipt_and_predicate_are_reported_at_the_lane(self):
        result = self.run_plan(PLAN.replace(" Save `cli.log`. Pass when head exits 2 before starting a job.", ""))
        self.assertEqual(result.returncode, 1)
        self.assertIn("lane names no artifact", result.stderr)
        self.assertIn("lane has no pass predicate", result.stderr)
        self.assertIn("plan.md:38:", result.stderr)

    def test_exemptions_need_reasons(self):
        for original, replacement, diagnostic in [
            ("Not applicable. This adds validation on the startup error path only.", "Not applicable.", "exemption needs a reason"),
            ("None. No user review remains for this CLI fix.", "None.", "None needs a reason"),
        ]:
            with self.subTest(diagnostic=diagnostic):
                result = self.run_plan(PLAN.replace(original, replacement))
                self.assertEqual(result.returncode, 1)
                self.assertIn(diagnostic, result.stderr)

    def test_performance_plan_needs_all_measurement_fields_and_numeric_budget(self):
        perf = """- [ ] Metric. Startup duration in ms.
- [ ] Probe. Run `bench.py` on trunk and head, interleaved.
- [ ] Baseline. Record trunk duration first.
- [ ] Rule. Fail if head exceeds trunk by 10 percent.
"""
        plan = PLAN.replace("Not applicable. This adds validation on the startup error path only.\n", perf)
        self.assertEqual(self.run_plan(plan).returncode, 0)
        result = self.run_plan(plan.replace("10 percent", "an unacceptable amount"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("numeric failure threshold", result.stderr)
        result = self.run_plan(plan.replace("- [ ] Baseline. Record trunk duration first.\n", ""))
        self.assertEqual(result.returncode, 1)
        self.assertIn("needs Metric, Probe, Baseline, Rule", result.stderr)

    def test_code_examples_cannot_supply_missing_live_evidence(self):
        lane = next(line for line in PLAN.splitlines() if line.startswith("- [ ] Lane"))
        result = self.run_plan(PLAN.replace(lane, "````markdown\n" + lane + "\n```\n````"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("lanes must be numbered consecutively", result.stderr)

    def test_regression_scenario_and_consecutive_lane_numbers_are_required(self):
        for old, new, diagnostic in [
            ("Lane 1.", "Lane 3.", "numbered consecutively"),
            ("Regression lane against trunk.", "Head only.", "lacks Regression lane against trunk"),
        ]:
            with self.subTest(diagnostic=diagnostic):
                result = self.run_plan(PLAN.replace(old, new))
                self.assertEqual(result.returncode, 1)
                self.assertIn(diagnostic, result.stderr)

    def test_malformed_structure_and_unclosed_fences_fail(self):
        for plan, diagnostic in [
            (PLAN.replace("**Files.**", "**Paths.**"), "sub-blocks must be"),
            (PLAN + "\n## Extra task\n", "is not an appendix"),
            (PLAN + "\n~~~text\n", "unclosed code fence"),
            ("---\n" + PLAN, "unclosed frontmatter"),
        ]:
            with self.subTest(diagnostic=diagnostic):
                result = self.run_plan(plan)
                self.assertEqual(result.returncode, 1)
                self.assertIn(diagnostic, result.stderr)

    def test_gate_requires_operator_decision(self):
        gate = "Approval of the CLI wording before merge.\n- [ ] Publish the CLI output for review.\n- [ ] Record the operator's decision."
        plan = PLAN.replace("None. No user review remains for this CLI fix.", gate)
        self.assertEqual(self.run_plan(plan).returncode, 0)
        result = self.run_plan(plan.replace("- [ ] Record the operator's decision.\n", ""))
        self.assertEqual(result.returncode, 1)
        self.assertIn("operator decision", result.stderr)

    def test_missing_file_reports_usage_failure_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(Path(directory) / "missing.md")],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("missing.md:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
