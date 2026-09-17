#!/usr/bin/env python3
"""Write a reviewer prompt for the review-pr skill to a file.

The template is read line by line. A line that is exactly a marker such as
{{DIFF}} is replaced by a file's content, and that content is never scanned
again, so a diff that itself contains a marker survives intact.

    build_prompt.py code --intent F --diff F --commits F --out F [--tree PATH] [--prior F] [--lenses a,b]
    build_prompt.py spec --intent F --diff F --commits F --sources F --out F [--prior F]

--lenses keeps only the named rubric lenses, matched by the start of their
heading (correctness, root, structure, verification, complexity, security).
Each kept lens is followed by the text of the principle files it links.
"""

import argparse
from pathlib import Path
import re
import sys

SKILL = Path(__file__).resolve().parent.parent
PRINCIPLE_LINK = re.compile(r"\]\(\.\./principles/([a-z-]+\.md)\)")
FIRST_ROUND = "This is the first review round on this change."
LATER_ROUND = ("Earlier rounds reviewed this pull request up to the first commit listed above. Review only the change "
               "shown here. A finding already answered below is raised again only when the new commits reopen it.\n\n")


def without_first_heading(text):
    lines = text.strip().split("\n")
    return "\n".join(lines[1:] if lines and lines[0].startswith("# ") else lines).strip()


def build_rubric(rubric_text, principles_dir, lenses=None):
    preamble, *sections = re.split(r"(?m)^(?=## )", rubric_text)
    wanted = [lens.strip().lower() for lens in lenses] if lenses else None
    parts, pasted = [without_first_heading(preamble)], set()
    for section in sections:
        heading = section.split("\n", 1)[0][3:].strip().lower()
        if wanted is not None and not any(heading.startswith(lens) for lens in wanted):
            continue
        parts.append(section.strip())
        for name in PRINCIPLE_LINK.findall(section):
            if name not in pasted:
                pasted.add(name)
                body = without_first_heading((principles_dir / name).read_text(encoding="utf-8"))
                parts.append("#### Principle: {}\n\n{}".format(name, body))
    return "\n\n".join(parts)


def fill(template, values):
    out = []
    for line in template.split("\n"):
        marker = line.strip()
        out.append(values[marker] if marker in values else line)
    return "\n".join(out)


def read(path):
    return Path(path).read_text(encoding="utf-8").strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=["code", "spec"])
    parser.add_argument("--intent", required=True)
    parser.add_argument("--diff", required=True)
    parser.add_argument("--commits", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--tree")
    parser.add_argument("--sources")
    parser.add_argument("--prior")
    parser.add_argument("--lenses")
    args = parser.parse_args(argv)
    if args.kind == "spec" and not args.sources:
        parser.error("spec needs --sources")

    values = {
        "{{INTENT}}": "\n".join("> " + line for line in read(args.intent).split("\n")),
        "{{DIFF}}": read(args.diff),
        "{{COMMITS}}": read(args.commits),
        "{{PRIOR}}": LATER_ROUND + read(args.prior) if args.prior else FIRST_ROUND,
    }
    if args.kind == "code":
        values["{{TREE}}"] = ("The tree at {} contains this change. Read callers, callees, types, and tests there "
                              "whenever a lens asks you to.".format(args.tree) if args.tree
                              else "No tree is available. Judge from the change shown.")
        values["{{RUBRIC}}"] = build_rubric((SKILL / "rubric.md").read_text(encoding="utf-8"), SKILL.parent / "principles",
                                            args.lenses.split(",") if args.lenses else None)
        values["{{LADDER}}"] = read(SKILL.parent / "blast-radius/evidence.md")
    else:
        values["{{SOURCES}}"] = read(args.sources)
    template = (SKILL / "prompts" / (args.kind + ".md")).read_text(encoding="utf-8")
    Path(args.out).write_text(fill(template, values) + "\n", encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    sys.exit(main())
