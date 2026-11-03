import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/brag-slim/scripts/check_pacing.py"
LINES = [{"at": 0, "text": "Your build:"}, {"at": 0.33, "text": "two clicks,"}, {"at": 0.67, "text": "zero config."}]


def run(timeline):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "timeline.json"
        path.write_text(timeline if isinstance(timeline, str) else json.dumps(timeline))
        return subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)


class CheckPacingTest(unittest.TestCase):
    def test_lines_read_in_order_count_against_the_beat(self):
        # Each line alone has time to be read once it appears; read in order, the last one is cut off.
        result = run({"launch": [{"start": 4, "end": 5.33, "items": LINES}]})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stdout,
            "launch beat 1 (4s to 5.33s): reading takes 1.80s but the beat lasts 1.33s; add 0.47s or cut words\n",
        )

    def test_a_beat_long_enough_to_read_passes(self):
        result = run({"launch": [{"start": 4, "end": 6.33, "items": LINES}]})
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_a_late_item_waits_for_its_entrance(self):
        # Items arrive out of order in the file; the viewer reads the early line, then waits for the icon.
        items = [{"at": 1.5, "seconds": 0.2}, {"at": 0, "text": "Ship it today."}]
        result = run({"end": [{"start": 0, "end": 1.6, "items": items}]})
        self.assertEqual(
            result.stdout,
            "end beat 1 (0s to 1.6s): reading takes 1.70s but the beat lasts 1.60s; add 0.10s or cut words\n",
        )

    def test_tags_and_entities_are_not_read(self):
        text = "Ship<br><em>it</em>&nbsp;now, don't wait."
        result = run({"launch": [{"start": 0, "end": 1.5, "items": [{"at": 0, "text": text}]}]})
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_an_item_outside_its_beat_is_reported_as_a_timing_error(self):
        result = run({"launch": [{"start": 4, "end": 5, "items": [{"at": 4.2, "text": "Go"}]}]})
        self.assertEqual(
            result.stdout,
            "launch beat 1 (4s to 5s): an item appears at 4.2s, outside the beat; `at` counts from the beat's start\n",
        )

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


if __name__ == "__main__":
    unittest.main()
