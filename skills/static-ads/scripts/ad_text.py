#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import re
import sys
import unicodedata
from urllib.parse import urlencode, urlsplit, urlunsplit

MAX_HEADLINE = 27
MAX_NAME = 12
SHOWN = 125
EMOJI = re.compile("[\u231a\u231b\u23e9-\u23ec\u23f0\u23f3\u25fd\u25fe\u2600-\u27bf\u2b1b\u2b1c\u2b50\u2b55\ufe0f\U0001f000-\U0001faff]")
DASHES = {"\u2014": "em dash", "\u2015": "em dash", "\u2e3a": "em dash", "\u2e3b": "em dash", "\u2013": "en dash"}


def utm_content(name):
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def link(page, campaign, content):
    parts = urlsplit(page)
    kept = [part for part in parts.query.split("&") if part and not part.startswith("utm_")]
    utm = urlencode([("utm_source", "meta"), ("utm_medium", "paid"), ("utm_campaign", campaign),
                     ("utm_content", content)])
    return urlunsplit(parts._replace(query="&".join(kept + [utm])))


def images(name, folder):
    if any(folder.glob("* Feed.png")) or any(folder.glob("* Story.png")):
        return [folder / "{} {}.png".format(name, placement) for placement in ["Feed", "Story"]]
    return [folder / "pages" / "{}.png".format(name)]


def problems(ad, folder):
    found = []
    name = ad.get("name", "")
    for field in ["name", "page", "headline", "primary_text"]:
        if not isinstance(ad.get(field), str) or not ad[field].strip():
            found.append("no {}".format(field))
    if found:
        return found
    if len(name) > MAX_NAME:
        found.append("name is {} characters, over {}".format(len(name), MAX_NAME))
    if len(ad["headline"]) > MAX_HEADLINE:
        found.append("headline is {} characters, over {}".format(len(ad["headline"]), MAX_HEADLINE))
    if urlsplit(ad["page"]).scheme not in ("http", "https"):
        found.append("page is not an http or https URL")
    for field in ["name", "headline", "primary_text"]:
        text = unicodedata.normalize("NFKC", ad[field])
        for label in sorted({label for dash, label in DASHES.items() if dash in text}):
            found.append("{} has an {}".format(field, label))
        if EMOJI.search(ad[field]):
            found.append("{} has an emoji".format(field))
    if not utm_content(name):
        found.append("name has no ASCII letters or digits for utm_content")
    for path in images(name, folder):
        if not path.is_file():
            found.append("no {}".format(path.relative_to(folder).as_posix()))
    return found


def main():
    parser = argparse.ArgumentParser(description="Check ad-text.json in a run folder and write copy.md from it.")
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="backslashreplace")
    try:
        spec = json.loads((args.folder / "ad-text.json").read_text(encoding="utf-8"))
        ads = spec["ads"]
        if not isinstance(ads, list) or not ads or not all(isinstance(ad, dict) for ad in ads):
            raise ValueError("ads must be a list of objects")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, "ad_text.py: can't read {}: {}\n".format(args.folder / "ad-text.json", error))
    campaign = spec.get("campaign").strip() if isinstance(spec.get("campaign"), str) else ""
    refused, seen = False, {}
    if "<" in campaign or ">" in campaign:
        print("campaign: still the template's placeholder")
        refused = True
    campaign = campaign or args.folder.resolve().name
    for ad in ads:
        found = problems(ad, args.folder)
        content = utm_content(ad["name"]) if isinstance(ad.get("name"), str) else ""
        if content and content in seen:
            found.append("utm_content {} is also {}'s".format(content, seen[content]))
        seen.setdefault(content, ad.get("name"))
        for problem in found:
            print("{}: {}".format(ad.get("name") or "(no name)", problem))
        refused = refused or bool(found)
    if refused:
        (args.folder / "copy.md").unlink(missing_ok=True)
        print("copy.md not written")
        return 1
    lines = ["# Ad text for {}".format(campaign), ""]
    for ad in ads:
        lines += ["## {}".format(ad["name"]), "",
                  "- Files: {}".format(", ".join(path.relative_to(args.folder).as_posix()
                                                 for path in images(ad["name"], args.folder))),
                  "- Headline: {}".format(ad["headline"]),
                  "- Link: {}".format(link(ad["page"], campaign, utm_content(ad["name"]))),
                  "- Before \"See more\": {}".format(re.sub(r"\s*\n\s*", " / ", ad["primary_text"][:SHOWN])),
                  "", "Primary text:", "", ad["primary_text"], ""]
    (args.folder / "copy.md").write_text("\n".join(lines), encoding="utf-8")
    print("wrote {} with {} ads".format(args.folder / "copy.md", len(ads)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
