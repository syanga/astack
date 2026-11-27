#!/usr/bin/env python3
"""Keep score for an ad refinement run: craft votes, buyer panel means, and the keep rule."""
import argparse
from dataclasses import asdict, dataclass
from enum import Enum
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

IMAGE_NAMES = ("ad.png", "ad.jpg", "ad.jpeg", "ad.webp")
PLACEHOLDER = re.compile(r"\{(panel|image|copy)\}")
COLUMNS = ("Round", "Targets", "New", "Best", "Craft", "Majors", "Panel A", "Panel B", "Decision")


class Refusal(Exception):
    pass


class Verdict(Enum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


class Panel(Enum):
    A = "A"
    B = "B"


@dataclass(frozen=True)
class Means:
    new: float
    best: float | None

    @property
    def delta(self) -> float:
        return round(self.new - self.best, 9)


@dataclass(frozen=True)
class Request:
    panel: Panel
    on_best: bool


@dataclass(frozen=True)
class Decision:
    keep: bool
    reason: str

    def __str__(self):
        return "{}: {}".format("keep" if self.keep else "revert", self.reason)


Majors = dict[str, int | None]


def compare(panel: Panel, means: Means, bound: float) -> tuple[bool, str]:
    delta = means.delta
    passed = delta > bound
    shown = "0" if bound == 0 else "{:.3f}".format(bound)
    return passed, "panel {} {:+.3f} {} {}".format(panel.value, delta, ">" if passed else "<=", shown)


def decide(new: Majors, best: Majors, margin: float, means: dict[Panel, Means]) -> Decision | Request:
    match verdict(new), verdict(best):
        case Verdict.INCONCLUSIVE, _:
            return Decision(False, "craft inconclusive")
        case Verdict.FAIL, Verdict.FAIL:
            pairs = [(new[member], best[member]) for member in new]
            if all(ours <= theirs for ours, theirs in pairs) and any(ours < theirs for ours, theirs in pairs):
                return Decision(True, "fewer major flaws")
            return Decision(False, "major flaws not fewer")
        case Verdict.FAIL, Verdict.PASS:
            return Decision(False, "craft fail")
        case Verdict.PASS, Verdict.FAIL:
            if Panel.A not in means:
                return Request(Panel.A, on_best=False)
            return Decision(True, "craft pass vs fail, panel A baseline")
    if Panel.A not in means:
        return Request(Panel.A, on_best=True)
    favors, a = compare(Panel.A, means[Panel.A], margin)
    if not favors:
        return Decision(False, a)
    if Panel.B not in means:
        return Request(Panel.B, on_best=True)
    confirms, b = compare(Panel.B, means[Panel.B], 0)
    if not confirms:
        return Decision(False, b)
    return Decision(True, a + ", " + b)


def conclusive(craft):
    return all(listed is not None for listed in craft.values())


def major_count(flaws) -> int:
    return sum(flaw["severity"] == "major" for flaw in flaws)


def majors(craft, version) -> Majors:
    return {member: None if listed is None else major_count(listed[version]) for member, listed in craft.items()}


def verdict(counts: Majors) -> Verdict:
    if None in counts.values():
        return Verdict.INCONCLUSIVE
    return Verdict.FAIL if any(counts.values()) else Verdict.PASS


def flaw_count(craft, version):
    return sum(len(listed[version]) for listed in craft.values() if listed is not None)


def load_craft(path, versions):
    try:
        craft = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refusal("cannot read craft file {}: {}".format(path, error))
    if not isinstance(craft, dict) or not craft:
        raise Refusal("craft file {} must be a JSON object with at least one member".format(path))
    for member, listed in craft.items():
        if listed is None:
            continue
        if not isinstance(listed, dict) or set(listed) != set(versions):
            raise Refusal("craft file {}: member {} must list exactly {}, or be null".format(
                path, member, " and ".join(versions)))
        for version, flaws in listed.items():
            if not isinstance(flaws, list) or not all(
                    isinstance(flaw, dict) and flaw.get("severity") in ("major", "minor") for flaw in flaws):
                raise Refusal("craft file {}: member {} must give {} a list of flaws, each with severity major or "
                              "minor".format(path, member, version))
    return craft


def version_files(run, name):
    if name in ("", ".", "..") or Path(name).name != name:
        raise Refusal("version {!r} must be the name of a folder in {}".format(name, run))
    folder = run / name
    if not folder.is_dir():
        raise Refusal("version folder {} is missing".format(folder))
    images = [folder / image for image in IMAGE_NAMES if (folder / image).is_file()]
    if len(images) != 1:
        raise Refusal("version folder {} needs exactly one of {}; it has {}".format(
            folder, ", ".join(IMAGE_NAMES), len(images)))
    copy = folder / "copy.txt"
    if not copy.is_file():
        raise Refusal("version folder {} has no copy.txt".format(folder))
    return images[0], copy


def panel_mean(output):
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        raise ValueError("no output")
    try:
        result = json.loads(lines[-1])
    except ValueError:
        raise ValueError("the last output line is not JSON")
    intent = result.get("purchase_intent") if isinstance(result, dict) else None
    mean = intent.get("mean") if isinstance(intent, dict) else None
    if isinstance(mean, bool) or not isinstance(mean, (int, float)) or not math.isfinite(mean):
        raise ValueError("purchase_intent.mean is missing or not a finite number")
    return mean


def run_panel(run, template, number, panel, version, files):
    image, copy = files
    values = {"panel": panel.value, "image": str(image), "copy": str(copy)}
    command = PLACEHOLDER.sub(lambda match: shlex.quote(values[match.group(1)]), template)
    folder = run / "panels"
    folder.mkdir(exist_ok=True)
    base = "r{}-{}-{}".format(number, version, panel.value)
    output, errors = folder / (base + ".json"), folder / (base + ".stderr")
    with output.open("wb") as out, errors.open("wb") as err:
        status = subprocess.run(["/bin/sh", "-c", command], cwd=run, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=err).returncode
    try:
        if status:
            raise ValueError("exit status {}".format(status))
        return panel_mean(output.read_text(encoding="utf-8", errors="replace"))
    except ValueError as error:
        raise Refusal("panel failed: panel {} on {}: {}; see {}".format(panel.value, version, error, errors))


def current_best(state):
    kept = [entry["version"] for entry in state["rounds"] if entry["decision"]["keep"]]
    return kept[-1] if kept else state["start"]["version"]


def stops(state):
    votes = [state["start"]["craft"]] + [entry["craft"] for entry in state["rounds"]]
    vote, best = votes[-1], current_best(state)
    reasons = []
    if len(state["rounds"]) == state["budget"]:
        reasons.append("budget")
    if conclusive(vote) and flaw_count(vote, best) == 0:
        reasons.append("empty flaw queue")
    if len(votes) > 1:
        previous = votes[-2]
        reasons += ["no score from {} in two votes in a row".format(member) for member, listed in vote.items()
                    if listed is None and member in previous and previous[member] is None]
    return reasons


def cell(text):
    return " ".join(text.split()).replace("|", "\\|")


def shown(means):
    if means is None:
        return "-"
    means = Means(**means)
    if means.best is None:
        return "{:.3f}".format(means.new)
    return "{:.3f} vs {:.3f} ({:+.3f})".format(means.new, means.best, means.delta)


def tally(craft, versions):
    return ", ".join("{} {}".format(member, "-" if listed is None else " vs ".join(
        str(major_count(listed[version])) for version in versions)) for member, listed in craft.items())


def report(state):
    start = state["start"]
    rows = [["0", "", start["version"], "", verdict(majors(start["craft"], start["version"])).value,
             tally(start["craft"], [start["version"]]), "-", "-", "start"]]
    for number, entry in enumerate(state["rounds"], 1):
        craft, new, best = entry["craft"], entry["version"], entry["best"]
        rows.append([str(number), entry["targets"], new, best,
                     "{} vs {}".format(verdict(majors(craft, new)).value, verdict(majors(craft, best)).value),
                     tally(craft, [new, best]), shown(entry["panels"].get("A")), shown(entry["panels"].get("B")),
                     str(Decision(**entry["decision"]))])
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    lines += ["| " + " | ".join(cell(value) for value in row) + " |" for row in rows]
    reasons = stops(state)
    status = ("stop: " + "; ".join(reasons) if reasons
              else "next: round {} against {}".format(len(state["rounds"]) + 1, current_best(state)))
    return "\n".join(lines) + "\n\n" + status + "\n"


def load_state(run):
    try:
        return json.loads((run / "run.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Refusal("{} holds no run.json; run start first".format(run))


def record(run, state):
    text = report(state)
    (run / "table.md").write_text(text, encoding="utf-8")
    pending = run / "run.json.tmp"
    pending.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(pending, run / "run.json")
    return text


def start_run(args):
    run = args.run.resolve()
    if (run / "run.json").exists():
        raise Refusal("{} already holds a run".format(run / "run.json"))
    version_files(run, args.version)
    craft = load_craft(args.craft, [args.version])
    return record(run, {"panel_command": args.panel_command, "margin": args.margin, "budget": args.rounds,
                        "start": {"version": args.version, "craft": craft}, "rounds": []})


def run_round(args):
    run = args.run.resolve()
    state = load_state(run)
    reasons = stops(state)
    if reasons:
        raise Refusal("the run has stopped: " + "; ".join(reasons))
    new, best = args.version, current_best(state)
    if new in [state["start"]["version"]] + [entry["version"] for entry in state["rounds"]]:
        raise Refusal("version {} already appears in this run; the current best is {}".format(new, best))
    files = {name: version_files(run, name) for name in (new, best)}
    craft = load_craft(args.craft, [new, best])
    if set(craft) != set(state["start"]["craft"]):
        raise Refusal("craft file {} must name the members {}; write null for a member without a score".format(
            args.craft, ", ".join(sorted(state["start"]["craft"]))))
    number = len(state["rounds"]) + 1
    means = {}
    while isinstance(outcome := decide(majors(craft, new), majors(craft, best), state["margin"], means), Request):
        means[outcome.panel] = Means(
            new=run_panel(run, state["panel_command"], number, outcome.panel, new, files[new]),
            best=run_panel(run, state["panel_command"], number, outcome.panel, best, files[best])
            if outcome.on_best else None)
    state["rounds"].append({"version": new, "best": best, "targets": args.targets, "craft": craft,
                            "panels": {panel.value: asdict(pair) for panel, pair in means.items()},
                            "decision": asdict(outcome)})
    return record(run, state)


def show_table(args):
    return report(load_state(args.run.resolve()))


def margin_value(text):
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("{!r} is not a number".format(text))
    if not 0 < value < math.inf:
        raise argparse.ArgumentTypeError("the margin must be a finite number above 0")
    return value


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="record the starting version and its craft vote")
    start.add_argument("run", type=Path)
    start.add_argument("--version", required=True)
    start.add_argument("--craft", required=True, type=Path)
    start.add_argument("--panel-command", required=True)
    start.add_argument("--margin", required=True, type=margin_value)
    start.add_argument("--rounds", type=int, choices=range(1, 6), default=5)
    start.set_defaults(handler=start_run)
    round_ = commands.add_parser("round", help="score a new version against the current best")
    round_.add_argument("run", type=Path)
    round_.add_argument("--version", required=True)
    round_.add_argument("--craft", required=True, type=Path)
    round_.add_argument("--targets", default="")
    round_.set_defaults(handler=run_round)
    table = commands.add_parser("table", help="print the round table again")
    table.add_argument("run", type=Path)
    table.set_defaults(handler=show_table)
    return result


def main():
    args = parser().parse_args()
    try:
        text = args.handler(args)
    except Refusal as refusal:
        print(refusal, file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
