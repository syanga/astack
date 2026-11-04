import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/brag-slim/scripts/check_pacing.py"
LINES = [{"at": 0, "text": "Your build:"}, {"at": 0.33, "text": "two clicks,"}, {"at": 0.67, "text": "zero config."}]


def run(timeline, *args, bpm="3000"):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "timeline.json"
        path.write_text(timeline if isinstance(timeline, str) else json.dumps(timeline))
        return subprocess.run([sys.executable, str(SCRIPT), f"--bpm={bpm}", *args, str(path)], capture_output=True, text=True)


class CheckPacingTest(unittest.TestCase):
    def test_lines_each_readable_alone_fail_when_read_in_order(self):
        result = run({"launch": [{"start": 4, "end": 5.33, "items": LINES}]})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stdout,
            "launch scene 1 (4s to 5.33s): reading takes 1.80s but the scene lasts 1.33s; add 0.47s or cut words\n",
        )

    def test_a_scene_long_enough_to_read_passes(self):
        result = run({"launch": [{"start": 4, "end": 6.33, "items": LINES}]})
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_items_listed_out_of_order_are_read_by_entrance_and_wait_for_it(self):
        items = [{"at": 1.5, "seconds": 0.2}, {"at": 0, "text": "Ship it today."}]
        result = run({"end": [{"start": 0, "end": 1.6, "items": items}]})
        self.assertEqual(
            result.stdout,
            "end scene 1 (0s to 1.6s): reading takes 1.70s but the scene lasts 1.60s; add 0.10s or cut words\n",
        )

    def test_tags_and_entities_are_not_read(self):
        text = "Ship<br><em>it</em>&nbsp;now, don't wait."
        result = run({"launch": [{"start": 0, "end": 1.5, "items": [{"at": 0, "text": text}]}]})
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_an_item_outside_its_scene_is_reported_as_a_timing_error(self):
        result = run({"launch": [{"start": 4, "end": 5, "items": [{"at": 4.2, "text": "Go"}]}]})
        self.assertEqual(
            result.stdout,
            "launch scene 1 (4s to 5s): an event at 4.2s falls outside the scene; times count from the scene's start\n",
        )

    def test_an_off_grid_scene_start_is_fixed_before_its_events_get_scene_relative_fixes(self):
        scene = {"start": 1.2, "end": 4, "items": [{"at": 0, "text": "Go"}, {"at": 0.65, "text": "now"}], "hits": [1.7]}
        result = run({"launch": [scene]}, bpm="60")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stdout,
            "launch scene 1 (1.2s to 4s): the scene's start is off the 0.500s grid; move it to 1.000s\n",
        )
        scene["start"] = 1
        self.assertEqual(
            run({"launch": [scene]}, bpm="60").stdout,
            "launch scene 1 (1s to 4s): the item at 0.650s is off the 0.500s grid; move it to 0.500s\n"
            "launch scene 1 (1s to 4s): the hit at 1.700s is off the 0.500s grid; move it to 1.500s\n",
        )

    def test_a_scene_on_a_90_bpm_grid_passes_despite_rounded_times(self):
        items = [{"at": 0, "text": "Go"}, {"at": 0.33, "text": "now"}, {"at": 0.67, "text": "today"}]
        result = run({"launch": [{"start": 1.33, "end": 2.67, "items": items, "hits": [0.9999]}]}, bpm="90")
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_a_malformed_timeline_is_an_input_error(self):
        for bad in (
            '{"launch": [{"start": 0, "items": []}]}',
            "{}",
            '{"launch": []}',
            '{"launch": [{"start": 0, "end": NaN, "items": []}]}',
            '{"launch": [{"start": 0, "end": 1, "items": [{"text": "Go"}]}]}',
            '{"launch": [{"start": 0, "end": 1, "items": [{"at": 0, "seconds": NaN}]}]}',
            '{"launch": [{"start": 0, "end": 1, "items": [{"at": 0, "seconds": -1}]}]}',
        ):
            result = run(bad)
            self.assertEqual(result.returncode, 2, bad)
            self.assertIn("cannot read timeline", result.stderr)

    def test_a_rate_or_grid_that_is_not_a_positive_number_is_rejected(self):
        timeline = {"launch": [{"start": 0, "end": 1, "items": [{"at": 0, "text": "one two three four"}]}]}
        for flag in ("rate", "bpm"):
            for value in ("nan", "0", "-1"):
                result = run(timeline, f"--{flag}={value}")
                self.assertEqual(result.returncode, 2, (flag, value))
                self.assertIn(f"--{flag} must be a positive number", result.stderr)


if __name__ == "__main__":
    unittest.main()
