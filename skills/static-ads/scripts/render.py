#!/usr/bin/env python3
import argparse
import html
import json
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile

PLACEMENTS = (("Feed", 1440, 1800), ("Story", 1440, 2560))
STORY_TOP = 320
STORY_BOTTOM = 693
MIN_TEXT = 43
EDGE = 160
OVERLAP = 4
MAX_NAME = 12
TIMEOUT_SECONDS = 60
PAGE_WAIT_MS = 15000
UMASK = os.umask(0)
os.umask(UMASK)
CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
MAC_CHROMES = (Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
               Path("/Applications/Chromium.app/Contents/MacOS/Chromium"))
CHECK_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><style>html, body { margin: 0; } iframe { display: block; border: 0; }</style></head>
<body><iframe id="ad" src="{src}" style="width: {width}px; height: {height}px"></iframe>
<script>
const frame = document.getElementById("ad");
frame.addEventListener("load", async () => {
  const doc = frame.contentDocument, view = frame.contentWindow, text = [], blocks = new Map();
  const block = (node) => {
    let element = node.parentElement;
    while (element.parentElement && view.getComputedStyle(element).display === "inline") element = element.parentElement;
    if (!blocks.has(element)) blocks.set(element, blocks.size);
    return blocks.get(element);
  };
  await doc.fonts.ready;
  const broken = [...doc.images].filter((image) => image.complete && !image.naturalWidth)
    .map((image) => image.getAttribute("src"));
  const walk = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
  while (walk.nextNode()) {
    const words = walk.currentNode.textContent.trim().replace(/\\s+/g, " ");
    const range = doc.createRange();
    range.selectNodeContents(walk.currentNode);
    const boxes = [...range.getClientRects()].filter((box) => box.width && box.height);
    if (!words || !boxes.length) continue;
    text.push({
      words, size: parseFloat(view.getComputedStyle(walk.currentNode.parentElement).fontSize),
      block: block(walk.currentNode), boxes: boxes.map((box) => [box.left, box.top, box.right, box.bottom]),
      left: Math.min(...boxes.map((box) => box.left)), right: Math.max(...boxes.map((box) => box.right)),
      top: Math.min(...boxes.map((box) => box.top)), bottom: Math.max(...boxes.map((box) => box.bottom)),
    });
  }
  const report = document.createElement("pre");
  report.id = "static-ads-check";
  report.textContent = JSON.stringify({text, broken});
  document.body.append(report);
});
</script></body></html>
"""
REPORT = re.compile(r'<pre id="static-ads-check">(.*?)</pre>', re.S)


def find_chrome(path):
    if path:
        return path
    found = next(filter(None, map(shutil.which, CHROME_NAMES)), None)
    if found:
        return found
    return next((str(path) for path in MAC_CHROMES if path.is_file()), None)


def chrome(binary, scratch, width, height, *arguments, network=False):
    command = [binary, "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
               "--no-default-browser-check", "--force-device-scale-factor=1", "--run-all-compositor-stages-before-draw",
               "--user-data-dir=" + str(scratch / "profile"), "--window-size={},{}".format(width, height), *arguments]
    if not network:
        command[1:1] = ["--host-resolver-rules=MAP * ~NOTFOUND", "--virtual-time-budget=10000"]
    env = {**os.environ, "HOME": str(scratch / "home"), "XDG_CONFIG_HOME": str(scratch / "config"),
           "XDG_CACHE_HOME": str(scratch / "cache"), "XDG_DATA_HOME": str(scratch / "data")}
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
                               env=env, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=TIMEOUT_SECONDS)
    except BaseException as error:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        if isinstance(error, subprocess.TimeoutExpired):
            raise RuntimeError("Chrome took over {} s".format(TIMEOUT_SECONDS))
        raise
    if process.returncode != 0:
        lines = stderr.strip().splitlines() or [""]
        raise RuntimeError("Chrome exited {}: {}".format(process.returncode, lines[-1]))
    return stdout


def png_size(path):
    data = path.read_bytes()[:24] if path.is_file() else b""
    if len(data) == 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    return None


def short(words):
    return words if len(words) <= 40 else words[:39] + "…"


def letter_band(box):
    left, top, right, bottom = box
    trim = (bottom - top) * 0.15
    return left, top + trim, right, bottom - trim


def overlap(first, second):
    first, second = letter_band(first), letter_band(second)
    width = min(first[2], second[2]) - max(first[0], second[0])
    height = min(first[3], second[3]) - max(first[1], second[1])
    return width >= OVERLAP and height >= OVERLAP


def problems(text, placement, width, height):
    found = []
    for index, item in enumerate(text):
        for other in text[index + 1:]:
            if item["block"] == other["block"]:
                continue
            if any(overlap(first, second) for first in item["boxes"] for second in other["boxes"]):
                found.append('text overlaps other text: "{}" and "{}"'.format(short(item["words"]),
                                                                            short(other["words"])))
    for item in text:
        words = short(item["words"])
        if item["left"] < 0 or item["top"] < 0 or item["right"] > width or item["bottom"] > height:
            found.append('text cut off at an edge: "{}"'.format(words))
        elif min(item["left"], item["top"], width - item["right"], height - item["bottom"]) < EDGE:
            found.append('text within {} px of an edge: "{}"'.format(EDGE, words))
        if item["size"] < MIN_TEXT:
            found.append('text under {} px: "{}"'.format(MIN_TEXT, words))
        if placement == "Story" and item["top"] < STORY_TOP:
            found.append('text in the top {} px, which Instagram covers: "{}"'.format(STORY_TOP, words))
        if placement == "Story" and item["bottom"] > height - STORY_BOTTOM:
            found.append('text in the bottom {} px, which Instagram covers: "{}"'.format(STORY_BOTTOM, words))
    return found


def capture(binary, scratch, width, height, url, target, network=False):
    handle, name = tempfile.mkstemp(prefix=".partial.", suffix=".png", dir=target.parent)
    os.close(handle)
    partial = Path(name)
    extra = ["--timeout={}".format(PAGE_WAIT_MS)] if network else []
    try:
        chrome(binary, scratch, width, height, *extra, "--screenshot=" + str(partial), url, network=network)
        size = png_size(partial)
        if size is None:
            raise RuntimeError("Chrome wrote no screenshot")
        if size != (width, height):
            raise RuntimeError("the screenshot is {}x{}, not {}x{}".format(*size, width, height))
        partial.chmod(0o666 & ~UMASK)
        partial.replace(target)
        return size
    except BaseException:
        partial.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise


def render(page, folder, binary, scratch):
    name = page.stem
    if len(name) > MAX_NAME:
        print("{}: FAILED (the name is {} characters, over {})".format(name, len(name), MAX_NAME))
        return False
    sizes, found = [], []
    for placement, width, height in PLACEMENTS:
        png = folder / "{} {}.png".format(name, placement)
        try:
            capture(binary, scratch, width, height, page.resolve().as_uri(), png)
            check = scratch / "check.html"
            check.write_text(CHECK_PAGE.replace("{src}", html.escape(page.resolve().as_uri()))
                             .replace("{width}", str(width)).replace("{height}", str(height)), encoding="utf-8")
            dom = chrome(binary, scratch, width, height, "--allow-file-access-from-files", "--dump-dom",
                         check.as_uri())
            report = REPORT.search(dom)
            if not report:
                raise RuntimeError("the page did not finish loading")
            checked = json.loads(html.unescape(report[1]))
        except (OSError, RuntimeError, ValueError) as error:
            print("{} {}: FAILED ({})".format(name, placement, error))
            for other, _, _ in PLACEMENTS:
                (folder / "{} {}.png".format(name, other)).unlink(missing_ok=True)
            return False
        sizes.append("{} {}x{}".format(placement, width, height))
        found += ["{} {}: {}".format(name, placement, problem)
                  for problem in problems(checked["text"], placement, width, height)]
        found += ["{} {}: image failed to load: {}".format(name, placement, src) for src in checked["broken"]]
    print("{}: {}".format(name, ", ".join(sizes)))
    for line in found:
        print(line)
    return not found


def screenshot_page(url, folder, binary, scratch):
    png = folder / "site-screenshot.png"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        size = capture(binary, scratch, 1440, 1800, url, png, network=True)
    except (OSError, RuntimeError) as error:
        print("page: FAILED ({})".format(error))
        return 1
    print("page: {} {}".format("x".join(map(str, size)), png))
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Screenshot every pages/<Name>.html in a run folder as <Name> Feed.png and <Name> Story.png.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--chrome", help="path to Google Chrome or Chromium, when it is not found on its own")
    parser.add_argument("--page", metavar="URL",
                        help="only screenshot the live page's first screen as site-screenshot.png")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="backslashreplace")
    signal.signal(signal.SIGTERM, lambda number, frame: sys.exit(128 + number))
    pages = sorted((args.folder / "pages").glob("*.html"))
    if not pages and not args.page:
        parser.exit(1, "render.py: no .html files in {}\n".format(args.folder / "pages"))
    binary = find_chrome(args.chrome)
    if not binary:
        parser.exit(1, "render.py: Chrome not found. Install Google Chrome or Chromium, or pass --chrome <path>\n")
    with tempfile.TemporaryDirectory(prefix="static-ads-render-", ignore_cleanup_errors=True) as scratch:
        if args.page:
            return screenshot_page(args.page, args.folder, binary, Path(scratch))
        results = [render(page, args.folder, binary, Path(scratch)) for page in pages]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
