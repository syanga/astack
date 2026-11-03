#!/usr/bin/env python3
"""Check that a viewer can read every beat of a brag timeline before it cuts.

The timeline is JSON: an object mapping each video's name to its beats.

    {"launch": [
        {"start": 0, "end": 2, "items": [
            {"at": 0, "text": "Ship it"},
            {"at": 0.33, "text": "<em>today.</em>"},
            {"at": 0.9, "seconds": 0.2}
        ]}
    ]}

A beat runs from `start` to `end` in seconds; `end` is when its text starts to
leave. Each item is something the viewer must take in: a line of text, a
highlighted phrase, a labeled icon. `at` is required: when the item is fully on
screen, in seconds after the beat starts. A text item needs the reading rate for
each word; HTML tags and entities are ignored. Give `seconds` instead for an item
that is not plain words, such as an icon or text in a script written without
spaces. Leave texture out of the timeline.

The viewer reads items in the order they appear and can start an item only once
it is on screen. A beat passes when the viewer finishes before it ends.
"""
import argparse
import html
import json
import math
import re
import sys

WORD = re.compile(r"\w+(?:['’]\w+)*")
TAG = re.compile(r"</?[A-Za-z][^>]*>")


def reading_time(item, rate):
    if "seconds" in item:
        seconds = float(item["seconds"])
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError(f"invalid seconds: {item['seconds']!r}")
        return seconds
    return rate * len(WORD.findall(html.unescape(TAG.sub(" ", item["text"]))))


def check(timeline, rate):
    if not isinstance(timeline, dict) or not timeline:
        raise ValueError("the timeline has no videos")
    problems = []
    for video, beats in timeline.items():
        if not beats:
            raise ValueError(f"{video} has no beats")
        for n, beat in enumerate(beats, 1):
            where = f"{video} beat {n} ({beat['start']:g}s to {beat['end']:g}s)"
            length = beat["end"] - beat["start"]
            entrances = [float(i["at"]) for i in beat["items"]]
            if not all(map(math.isfinite, [length, *entrances])) or length <= 0:
                raise ValueError(f"{where} has an invalid time")
            outside = [t for t in entrances if not 0 <= t < length]
            if outside:
                problems.append(f"{where}: an item appears at {outside[0]:g}s, outside the beat; `at` counts from the beat's start")
                continue
            done = 0.0
            for item in sorted(beat["items"], key=lambda i: float(i["at"])):
                done = max(done, float(item["at"])) + reading_time(item, rate)
            if done - length >= 0.005:
                problems.append(
                    f"{where}: reading takes {done:.2f}s but the beat lasts {length:.2f}s; "
                    f"add {done - length:.2f}s or cut words"
                )
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("timeline", help="path to the timeline JSON")
    ap.add_argument("--rate", type=float, default=0.3, help="seconds per word (default 0.3)")
    args = ap.parse_args()
    if not math.isfinite(args.rate) or args.rate <= 0:
        ap.error("--rate must be a positive number of seconds")
    try:
        with open(args.timeline) as f:
            problems = check(json.load(f), args.rate)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        print(f"cannot read timeline: {e}", file=sys.stderr)
        return 2
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
