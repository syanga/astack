#!/usr/bin/env python3
import argparse
import base64
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from math import gcd
import mimetypes
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 300
RETRIED_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_WAITS = (5, 10)
MAX_RETRY_AFTER = 60
OUTPUT = threading.Lock()
VERTEX_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
VERTEX_TIERS = (("1K", 1024), ("2K", 2048), ("4K", 4096))


class RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


OPENER = urllib.request.build_opener(RefuseRedirect)


class SetupError(Exception):
    pass


@dataclass(frozen=True)
class Session:
    url: str
    token: str
    model: str


@dataclass(frozen=True)
class Backend:
    service: str
    model: str
    connect: Callable[[str], Session]
    describe: Callable[[dict], str]
    build: Callable[[Session, dict, list[Path]], tuple[str, dict]]
    unpack: Callable[[dict], tuple[bytes, dict]]


def say(line):
    with OUTPUT:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def encoded(path):
    return mimetypes.guess_type(path.name)[0] or "image/png", base64.b64encode(path.read_bytes()).decode()


def data_url(path):
    return "data:{};base64,{}".format(*encoded(path))


def inline_data(path):
    kind, data = encoded(path)
    return {"inlineData": {"mimeType": kind, "data": data}}


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


def openai_session(model):
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key or not key.isprintable():
        raise SetupError("set OPENAI_API_KEY to an OpenAI API key on one line")
    return Session(os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"), key, model)


def openai_shape(request):
    return "{} {}".format(request.get("size", "auto"), request.get("quality", "auto"))


def openai_call(session, request, references):
    body = {**{field: value for field, value in request.items() if field != "images"}, "model": session.model}
    if not references:
        return session.url + "/images/generations", body
    body["images"] = [{"image_url": data_url(image)} for image in references]
    return session.url + "/images/edits", body


def openai_image(response):
    image = base64.b64decode(response["data"][0]["b64_json"])
    for item in response["data"]:
        item.pop("b64_json", None)
    return image, response


def gcloud(*arguments):
    try:
        ran = subprocess.run(["gcloud", *arguments], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return "", str(error)
    return (ran.stdout.strip() if ran.returncode == 0 else ""), ran.stderr


def vertex_session(model):
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", model):
        raise SetupError("{} is not a Vertex model name".format(model))
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip() or gcloud("config", "get-value", "project")[0]
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project):
        raise SetupError("set GOOGLE_CLOUD_PROJECT to a Google Cloud project id")
    token, failure = gcloud("auth", "application-default", "print-access-token")
    if not token or not token.isprintable():
        last = ([line.strip() for line in failure.splitlines() if line.strip()] or [""])[-1]
        raise SetupError("could not get a Vertex access token. Run: gcloud auth application-default login"
                         + (" ({})".format(last) if last else ""))
    base_url = os.environ.get("VERTEX_BASE_URL", "https://aiplatform.googleapis.com/v1").rstrip("/")
    return Session("{}/projects/{}/locations/global/publishers/google/models".format(base_url, project), token,
                   model)


def vertex_image_config(request):
    size = request.get("size", "auto")
    if size == "auto":
        return None
    sides = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", size) if isinstance(size, str) else None
    if sides:
        width, height = map(int, sides.groups())
        divisor = gcd(width, height)
        ratio = "{}:{}".format(width // divisor, height // divisor)
        if ratio in VERTEX_RATIOS:
            tier = next((name for name, side in VERTEX_TIERS if width * height <= side * side), "4K")
            return {"aspectRatio": ratio, "imageSize": tier}
    raise ValueError("size {} is not <width>x<height> in a ratio Vertex supports: {}".format(
        size, ", ".join(VERTEX_RATIOS)))


def vertex_shape(request):
    config = vertex_image_config(request)
    return "{aspectRatio} {imageSize}".format(**config) if config else "auto"


def vertex_call(session, request, references):
    config = {"responseModalities": ["IMAGE"]}
    image_config = vertex_image_config(request)
    if image_config:
        config["imageConfig"] = image_config
    parts = [{"text": request["prompt"]}, *map(inline_data, references)]
    return ("{}/{}:generateContent".format(session.url, session.model),
            {"contents": [{"role": "user", "parts": parts}], "generationConfig": config})


def vertex_image(response):
    candidates = response.get("candidates", [])
    parts = [part for candidate in candidates for part in candidate.get("content", {}).get("parts", [])]
    image = next((part["inlineData"]["data"] for part in parts if part.get("inlineData", {}).get("data")), None)
    if image is None:
        raise ValueError("Vertex returned no image (blockReason={}, finishReason={})".format(
            response.get("promptFeedback", {}).get("blockReason"),
            [candidate.get("finishReason") for candidate in candidates]))
    for part in parts:
        part.pop("thoughtSignature", None)
        part.get("inlineData", {}).pop("data", None)
    return base64.b64decode(image), response


BACKENDS = {
    "openai": Backend("OpenAI", "gpt-image-2.6-sunburst", openai_session, openai_shape, openai_call, openai_image),
    "gemini": Backend("Vertex", "gemini-3-pro-image", vertex_session, vertex_shape, vertex_call, vertex_image),
}


def post(url, body, token):
    call = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                  headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    for fallback in (*RETRY_WAITS, None):
        try:
            with OPENER.open(call, timeout=TIMEOUT_SECONDS) as reply:
                return json.loads(reply.read())
        except urllib.error.HTTPError as error:
            if fallback is None or error.code not in RETRIED_STATUSES:
                raise
            asked = (error.headers.get("Retry-After") or "").strip()
            error.close()
            time.sleep(min(int(asked), MAX_RETRY_AFTER) if asked.isascii() and asked.isdigit() else fallback)


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


def check(request_path, backend):
    try:
        request, references = load(request_path)
        shape = backend.describe(request)
    except Exception as error:
        say("{}: FAILED ({})".format(ad_name(request_path), error))
        return False
    say("{}: {}, {} chars, {} refs".format(ad_name(request_path), shape, len(request["prompt"]), len(references)))
    return True


def generate(request_path, backend, session):
    folder = request_path.parent
    name = ad_name(request_path)
    png = folder / (name + ".png")
    try:
        if png.is_file() and png.stat().st_size:
            say("{}: exists {}, skip".format(name, png_size(png.read_bytes())))
            return True
        request, references = load(request_path)
        url, body = backend.build(session, request, references)
        image, response = backend.unpack(post(url, body, session.token))
        partial = folder / (name + ".png.partial")
        partial.write_bytes(image)
        partial.replace(png)
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
    parser = argparse.ArgumentParser(description="Generate every <name>.req.json in a folder with GPT Image or Gemini.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="check the request files without calling the image API")
    parser.add_argument("--backend", choices=BACKENDS, default="openai")
    parser.add_argument("--model", help="defaults to the backend's model")
    args = parser.parse_args()
    backend = BACKENDS[args.backend]
    model = backend.model if args.model is None else args.model
    sys.stdout.reconfigure(errors="backslashreplace")
    requests = sorted(args.folder.glob("*.req.json"))
    if not requests:
        parser.exit(1, "generate.py: no .req.json files in {}\n".format(args.folder))
    if args.dry_run:
        say("dry-run: checking request files only. Not calling {}.".format(backend.service))
        say("a real run would use {}".format(model))
        return 0 if all([check(path, backend) for path in requests]) else 1
    try:
        session = backend.connect(model)
    except SetupError as error:
        parser.exit(1, "generate.py: {}\n".format(error))
    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        results = list(pool.map(lambda path: generate(path, backend, session), requests))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
