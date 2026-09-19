#!/usr/bin/env python3
import argparse
from dataclasses import dataclass, field
from pathlib import Path
import re
import sys


RULE = (
    "Tests alone are not sufficient verification. A PR is verified only when its "
    "applicable unit, live, and perf boxes are checked with evidence."
)
SUB_BLOCKS = [
    "Depends on.", "Files.", "Build.", "You see.", "Verify, unit.",
    "Verify, live.", "Verify, perf.", "Review gate.", "Merge.",
]
PROGRAM_H3 = ["Arm the program", "Spawn owners", "PR mechanics", "Verdict and merge", "Boot recipe"]
PROGRAM_MARKERS = ["execution authorization", "merge authorization", "audit", "status message"]
HOW_MARKERS = [
    "One box is one unit of work", "names the evidence",
    "Check a box only when its evidence exists", "SKILL.md", RULE,
]
PERF_ITEMS = ["Metric.", "Probe.", "Baseline.", "Rule."]
BOX = re.compile(r"^\s*- \[[ x]\] (.*)$")


@dataclass
class Line:
    number: int
    text: str


@dataclass
class Section:
    title: str
    number: int
    rest: str = ""
    lines: list[Line] = field(default_factory=list)

    def text(self):
        return "\n".join(line.text for line in self.lines)

    def boxes(self):
        return [Line(line.number, match[1]) for line in self.lines if (match := BOX.match(line.text))]


def check(text):
    problems = []

    def fail(number, message):
        problems.append((number, message))

    raw = text.splitlines()
    start = 0
    if raw and raw[0] == "---":
        try:
            start = raw.index("---", 1) + 1
        except ValueError:
            fail(1, "unclosed frontmatter")
    lines = []
    fence = None
    for number, value in enumerate(raw[start:], start + 1):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})(.*)$", value)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            continue
        if marker:
            fence = marker[1]
            continue
        lines.append(Line(number, value))
        prose = re.sub(r"`[^`]*`|!?\[[^\]]*\]\([^)]*\)", "", value)
        if re.search(r"[\u2013\u2014]", prose):
            fail(number, "long dash")
        if re.search(r"[\u2018\u2019\u201c\u201d]", prose):
            fail(number, "curly quote")
        if re.search(r": \S", prose):
            fail(number, "mid-sentence colon")
    if fence:
        fail(len(raw), "unclosed code fence")

    sections = []
    for line in lines:
        if line.text.startswith("## "):
            sections.append(Section(line.text[3:].strip(), line.number))
        elif sections:
            sections[-1].lines.append(line)

    def find(title):
        matches = [section for section in sections if section.title == title]
        if not matches:
            fail(1, f'no "## {title}" section')
            return None
        if len(matches) > 1:
            fail(matches[1].number, f'duplicate "## {title}" section')
        return matches[0]

    h1 = [line for line in lines if line.text.startswith("# ")]
    if len(h1) != 1:
        fail(1, "exactly one H1 title required")
    how = find("How to read this")
    program = find("Program checklist")
    close = find("Close the program")
    if how:
        if h1 and len([line for line in lines if h1[0].number < line.number < how.number and line.text.strip()]) >= 10:
            fail(h1[0].number, "intro must be under ten lines")
        for marker in HOW_MARKERS:
            if marker not in how.text():
                fail(how.number, f'How to read this lacks "{marker}"')
    if program:
        headings = [line.text[4:].strip() for line in program.lines if line.text.startswith("### ")]
        cursor = 0
        for name in PROGRAM_H3:
            found = next((i for i in range(cursor, len(headings)) if headings[i].startswith(name)), None)
            if found is None:
                fail(program.number, f'Program checklist lacks "### {name}" in order')
            else:
                cursor = found + 1
        for marker in PROGRAM_MARKERS:
            if marker not in program.text():
                fail(program.number, f'Program checklist lacks "{marker}"')
    if how and program and close and not how.number < program.number < close.number:
        fail(1, "How to read this, Program checklist, and Close the program must appear in order")
    prs = [section for section in sections if program and close and program.number < section.number < close.number]
    if not prs:
        fail(1, "no PR sections between Program checklist and Close the program")
    report = []
    for pr in prs:
        blocks = []
        for line in pr.lines:
            match = re.match(r"^\*\*([^*]+)\*\*(.*)$", line.text)
            if match and match[1] in SUB_BLOCKS:
                blocks.append(Section(match[1], line.number, match[2].strip()))
            elif blocks:
                blocks[-1].lines.append(line)
        if [block.title for block in blocks] != SUB_BLOCKS:
            fail(pr.number, f"{pr.title}: sub-blocks must be {', '.join(SUB_BLOCKS)} in order")
        for block in blocks:
            boxes = block.boxes()
            prefix = f"{pr.title}: {block.title}"
            if block.title == "Depends on." and not block.rest:
                fail(block.number, f"{prefix} names nothing")
            if block.title in ["Files.", "Build.", "You see.", "Verify, unit.", "Merge."] and not boxes:
                fail(block.number, f"{prefix} has no box")
            for box in boxes:
                if not box.text.strip():
                    fail(box.number, f"{prefix} has an empty box")
            if block.title.startswith("Verify,") and not block.rest.startswith(RULE):
                fail(block.number, f"{prefix} does not open with the rule")
            if block.title == "Verify, live.":
                numbers = []
                for box in boxes:
                    lane = re.match(r"Lane (\d+)\. .+", box.text)
                    if not lane:
                        fail(box.number, f"{prefix} live box is not a lane")
                    else:
                        numbers.append(int(lane[1]))
                    if not re.search(r"Save `[^`]+`", box.text):
                        fail(box.number, f"{prefix} lane names no artifact")
                    if not re.search(r"Pass when \S", box.text):
                        fail(box.number, f"{prefix} lane has no pass predicate")
                if not numbers or numbers != list(range(1, len(boxes) + 1)):
                    fail(block.number, f"{prefix} lanes must be numbered consecutively from 1")
                if not any("Regression lane against trunk." in box.text for box in boxes):
                    fail(block.number, f"{prefix} lacks Regression lane against trunk")
            if block.title == "Verify, perf.":
                detail = (block.rest.removeprefix(RULE) + "\n" + block.text()).strip()
                if detail.startswith("Not applicable."):
                    reason = detail.removeprefix("Not applicable.").strip()
                    if not reason or boxes:
                        fail(block.number, f"{prefix} exemption needs a reason and no boxes")
                else:
                    if [box.text.split(" ")[0] for box in boxes] != PERF_ITEMS:
                        fail(block.number, f"{prefix} needs Metric, Probe, Baseline, Rule boxes or Not applicable with a reason")
                    elif any(not box.text.partition(" ")[2].strip() for box in boxes):
                        fail(block.number, f"{prefix} has an empty measurement field")
                    elif not re.search(r"\d", boxes[-1].text):
                        fail(boxes[-1].number, f"{prefix} Rule needs a numeric failure threshold")
            if block.title == "Review gate.":
                if block.rest.startswith("None."):
                    if not block.rest.removeprefix("None.").strip() or boxes:
                        fail(block.number, f"{prefix} None needs a reason and no boxes")
                elif not block.rest or not boxes or "operator" not in block.text().lower():
                    fail(block.number, f"{prefix} needs the gate, evidence boxes, and operator decision")
        report.append(f"{pr.title}  boxes={len(pr.boxes())}")
    if close:
        tail = [section for section in sections if section.number > close.number]
        for section in tail:
            if not section.title.startswith("Appendix"):
                fail(section.number, f'"## {section.title}" after Close the program is not an appendix')
        if not any("Prototype evidence" in section.title for section in tail):
            fail(close.number, "no Prototype evidence appendix")
        if not close.boxes():
            fail(close.number, "Close the program has no boxes")
    report.append(f"{len(prs)} PR sections, {len(problems)} problems")
    return report, problems


def main():
    parser = argparse.ArgumentParser(description="Check the structure and evidence fields of a multi-PR plan.")
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        content = args.plan.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        parser.exit(2, f"{args.plan}: {error}\n")
    report, problems = check(content)
    print("\n".join(report))
    for number, message in problems:
        print(f"{args.plan}:{number}: {message}", file=sys.stderr)
    return bool(problems)


if __name__ == "__main__":
    sys.exit(main())
