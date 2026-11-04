#!/usr/bin/env python3
"""Check that a viewer can read every scene of a brag timeline before it cuts,
and that every timed event lands on the music's grid.

The timeline is JSON: an object mapping each video's name to its scenes.

    {"launch": [
        {"start": 0, "end": 2, "items": [
            {"at": 0, "text": "Ship it"},
            {"at": 0.33, "text": "<em>today.</em>"},
            {"at": 1, "seconds": 0.2}
        ], "hits": [1.33]}
    ]}

A scene runs from `start` to `end` in seconds; `end` is when its text starts to
leave. Each item is something the viewer must take in: a line of text, a
highlighted phrase, a labeled icon. `at` is required: when the item is fully on
screen, in seconds after the scene starts. A text item needs the reading rate for
each word; HTML tags and entities are ignored. Give `seconds` instead for an item
that is not plain words, such as an icon or text in a script written without
spaces. Leave texture out of the items. `hits` lists every other timed event in
the scene, such as a footage switch or a sound effect, in seconds after its start.

The viewer reads items in the order they appear and can start an item only once
it is on screen. A scene passes when the viewer finishes before it ends, and its
start, end, item entrances and hits all fall on the music's eighth-note grid,
worked out from --bpm.
"""
import argparse
import html
import json
import math
import re
import sys

WORD = re.compile(r"\w+(?:['’]\w+)*")
TAG = re.compile(r"</?[A-Za-z][^>]*>")
TOLERANCE = 0.01


def reading_time(item, rate):
    if "seconds" in item:
        seconds = float(item["seconds"])
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError(f"invalid seconds: {item['seconds']!r}")
        return seconds
    return rate * len(WORD.findall(html.unescape(TAG.sub(" ", item["text"]))))


def nearest(t, grid):
    return grid * math.floor(t / grid + 0.5)


def check(timeline, rate, grid):
    if not isinstance(timeline, dict) or not timeline:
        raise ValueError("the timeline has no videos")
    problems = []
    for video, scenes in timeline.items():
        if not scenes:
            raise ValueError(f"{video} has no scenes")
        for n, scene in enumerate(scenes, 1):
            start, end = float(scene["start"]), float(scene["end"])
            where = f"{video} scene {n} ({start:g}s to {end:g}s)"
            length = end - start
            entrances = [float(i["at"]) for i in scene["items"]]
            if not isinstance(scene.get("hits", []), list):
                raise ValueError(f"{where} has hits that are not a list")
            hits = [float(t) for t in scene.get("hits", [])]
            if not all(map(math.isfinite, [start, end, *entrances, *hits])) or length <= 0:
                raise ValueError(f"{where} has an invalid time")
            outside = [t for t in entrances + hits if not 0 <= t < length]
            if outside:
                problems.append(f"{where}: an event at {outside[0]:g}s falls outside the scene; times count from the scene's start")
                continue
            bounds = [(label, t) for label, t in (("start", start), ("end", end)) if abs(t - nearest(t, grid)) > TOLERANCE]
            for label, t in bounds:
                problems.append(f"{where}: the scene's {label} is off the {grid:.3f}s grid; move it to {nearest(t, grid):.3f}s")
            if length < grid - TOLERANCE:
                problems.append(f"{where}: the scene is shorter than one {grid:.3f}s grid step")
            events = [] if bounds or length < grid - TOLERANCE else [("item", t) for t in entrances] + [("hit", t) for t in hits]
            for label, t in events:
                if abs(start + t - nearest(start + t, grid)) > TOLERANCE:
                    to = nearest(start + t, grid) - start
                    to = to - grid if to >= length else to + grid if to < 0 else to
                    problems.append(f"{where}: the {label} at {t:.3f}s is off the {grid:.3f}s grid; move it to {to:.3f}s")
            done = 0.0
            for item in sorted(scene["items"], key=lambda i: float(i["at"])):
                done = max(done, float(item["at"])) + reading_time(item, rate)
            if done - length >= TOLERANCE:
                problems.append(
                    f"{where}: reading takes {done:.2f}s but the scene lasts {length:.2f}s; "
                    f"add {done - length:.2f}s or cut words"
                )
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("timeline", help="path to the timeline JSON")
    ap.add_argument("--bpm", type=float, required=True, help="the music's tempo in beats per minute")
    ap.add_argument("--rate", type=float, default=0.3, help="seconds per word (default 0.3)")
    args = ap.parse_args()
    for name in ("bpm", "rate"):
        if not math.isfinite(getattr(args, name)) or getattr(args, name) <= 0:
            ap.error(f"--{name} must be a positive number")
    try:
        with open(args.timeline) as f:
            problems = check(json.load(f), args.rate, 30 / args.bpm)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        print(f"cannot read timeline: {e}", file=sys.stderr)
        return 2
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
