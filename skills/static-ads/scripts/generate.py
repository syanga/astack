#!/usr/bin/env python3
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import mimetypes
import os
from pathlib import Path
import struct
import sys
import threading
import urllib.error
import urllib.request

DEFAULT_MODEL = "gpt-image-2.6-sunburst"
TIMEOUT_SECONDS = 300
OUTPUT = threading.Lock()


class RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


OPENER = urllib.request.build_opener(RefuseRedirect)


def say(line):
    with OUTPUT:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def data_url(path):
    kind = mimetypes.guess_type(path.name)[0] or "image/png"
    return "data:{};base64,{}".format(kind, base64.b64encode(path.read_bytes()).decode())


def png_size(data):
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return "{}x{}".format(*struct.unpack(">II", data[16:24]))
    return "unknown size"


def api_error(error):
    try:
        message = json.loads(error.read())["error"]["message"]
    except Exception:
        message = error.reason
    return "HTTP {}: {}".format(error.code, message)


def ad_name(request_path):
    return request_path.name[: -len(".req.json")]


def load(request_path):
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(request, dict) or not isinstance(request.get("prompt"), str) or not request["prompt"]:
        raise ValueError("the request has no prompt")
    references = request.get("images", [])
    if not isinstance(references, list) or not all(isinstance(image, str) for image in references):
        raise ValueError("images must be a list of file names")
    folder = request_path.parent.resolve()
    paths = [(folder / image).resolve() for image in references]
    outside = [image for image, path in zip(references, paths) if not path.is_relative_to(folder)]
    if outside:
        raise ValueError("reference outside the run folder {}".format(", ".join(outside)))
    missing = [image for image, path in zip(references, paths) if not path.is_file()]
    if missing:
        raise ValueError("missing reference {}".format(", ".join(missing)))
    return request, paths


def check(request_path):
    try:
        request, references = load(request_path)
    except Exception as error:
        say("{}: FAILED ({})".format(ad_name(request_path), error))
        return False
    say("{}: {} {}, {} chars, {} refs".format(ad_name(request_path), request.get("size", "auto"),
                                              request.get("quality", "auto"), len(request["prompt"]),
                                              len(references)))
    return True


def generate(request_path, model, base_url, key):
    folder = request_path.parent
    name = ad_name(request_path)
    png = folder / (name + ".png")
    try:
        if png.is_file() and png.stat().st_size:
            say("{}: exists {}, skip".format(name, png_size(png.read_bytes())))
            return True
        request, references = load(request_path)
        body = {**{field: value for field, value in request.items() if field != "images"}, "model": model}
        endpoint = "/images/generations"
        if references:
            body["images"] = [{"image_url": data_url(image)} for image in references]
            endpoint = "/images/edits"
        call = urllib.request.Request(base_url + endpoint, data=json.dumps(body).encode(), method="POST",
                                      headers={"Authorization": "Bearer " + key,
                                               "Content-Type": "application/json"})
        with OPENER.open(call, timeout=TIMEOUT_SECONDS) as reply:
            response = json.loads(reply.read())
        image = base64.b64decode(response["data"][0]["b64_json"])
        partial = folder / (name + ".png.partial")
        partial.write_bytes(image)
        partial.replace(png)
        for item in response["data"]:
            item.pop("b64_json", None)
        (folder / (name + ".res.json")).write_text(json.dumps(response, indent=2) + "\n", encoding="utf-8")
    except urllib.error.HTTPError as error:
        say("{}: FAILED ({})".format(name, api_error(error)))
        return False
    except Exception as error:
        say("{}: FAILED ({})".format(name, error))
        return False
    say("{}: done {}".format(name, png_size(image)))
    return True


def main():
    parser = argparse.ArgumentParser(description="Generate every <name>.req.json in a folder with GPT Image.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="check the request files without calling OpenAI")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    requests = sorted(args.folder.glob("*.req.json"))
    if not requests:
        parser.exit(1, "generate.py: no .req.json files in {}\n".format(args.folder))
    if args.dry_run:
        say("dry-run: checking request files only. Not calling OpenAI.")
        say("a real run would use {}".format(args.model))
        return 0 if all([check(path) for path in requests]) else 1
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key or not key.isprintable():
        parser.exit(1, "generate.py: set OPENAI_API_KEY to an OpenAI API key on one line\n")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        results = list(pool.map(lambda path: generate(path, args.model, base_url, key), requests))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
