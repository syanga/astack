import base64
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import string
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib

SCRIPT = Path(__file__).resolve().parents[1] / "skills/static-ads/scripts/generate.py"


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png_header(width, height):
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IEND", b""))


def png(width, rows, kind):
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, len(rows), 8, kind, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(b"\0" + row for row in rows))) + chunk(b"IEND", b""))


RENDER = png_header(1472, 1840)
FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = unittest.skipUnless(FFMPEG, "compose needs ffmpeg")
BACKGROUND = bytes.fromhex("061521")
ART_BACKGROUND = bytes.fromhex("0d1925")
ART_SQUARE = bytes.fromhex("f0c040")
ART_ACCENT = bytes.fromhex("ffc5a0")
LOGO = bytes.fromhex("ffc800")
WHITE = bytes.fromhex("ffffff")
PILL = bytes(round(back * (1 - 0.82) + fill * 0.82) for back, fill in zip(BACKGROUND, bytes.fromhex("026692")))
WIDTH_CLASSES = {100: 5, 125: 7}
BRAND = {
    "backgrounds": ["#061521", "#5b86a8"],
    "logo": "lockup.png",
    "button": {"color": "#026692", "opacity": 0.82},
    "type": {
        "headline": {"font": "fonts/headline.ttf", "weight": 500, "width": 125, "case": "upper", "color": "#ffffff"},
        "subline": {"font": "fonts/subline.ttf", "weight": 450, "width": 100, "color": "#ffffff"},
        "cta": {"font": "fonts/cta.ttf", "weight": 700, "width": 100, "case": "upper", "color": "#ffffff"},
    },
}
COPY = {"headline": "Read the sky you were\nborn under", "subline": "A written reading.", "cta": "Get your reading"}


def square(width, height):
    return round(0.42 * width), round(0.40 * height), round(0.58 * width), round(0.46 * height)


def column(width):
    return width // 10


def art(width, height, color=ART_SQUARE, ruled=False):
    x0, y0, x1, y1 = square(width, height)
    edge = bytes((a + b) // 2 for a, b in zip(ART_BACKGROUND, color))
    plain = ART_BACKGROUND * width
    marked = ART_BACKGROUND * (x0 - 1) + edge + color * (x1 - x0) + edge + ART_BACKGROUND * (width - x1 - 1)
    rows = [marked if y0 <= y < y1 else plain for y in range(height)]
    if ruled:
        at = 3 * column(width)
        rows = [row[:at] + color + row[at + 3:] for row in rows]
    return png(width, rows, 2)


def image_for(prompt):
    asked = re.search(r"ART ([0-9]+)x([0-9]+)", prompt)
    if not asked:
        return RENDER
    return art(*map(int, asked.groups()), ART_ACCENT if "ACCENT" in prompt else ART_SQUARE, "RULED" in prompt)


def sfnt(tables):
    tags = sorted(tables)
    power = 2 ** (len(tags).bit_length() - 1)
    header = struct.pack(">IHHHH", 0x10000, len(tags), 16 * power, power.bit_length() - 1, 16 * (len(tags) - power))
    directory, body = b"", b""
    for tag in tags:
        directory += struct.pack(">4sIII", tag, 0, 12 + 16 * len(tags) + len(body), len(tables[tag]))
        body += tables[tag] + b"\0" * (-len(tables[tag]) % 4)
    return header + directory + body


def glyph(top):
    return (struct.pack(">5hHH", 1, 50, 0, 550, top, 3, 0) + bytes([1] * 4)
            + struct.pack(">8h", 50, 0, 500, 0, 0, top, 0, -top))


def font(weight, width, chars=string.ascii_letters + string.digits + " .,!?'-", variable=False, tall="É"):
    block, high = glyph(700), glyph(950)
    codes = sorted(map(ord, chars + tall))
    segments = [(code, ((1 if code == 32 else 3 if chr(code) in tall else 2) - code) & 0xFFFF)
                for code in codes] + [(0xFFFF, 1)]
    count = len(segments)
    power = 2 ** (count.bit_length() - 1)
    starts = struct.pack(">{}H".format(count), *(code for code, _ in segments))
    cmap = (struct.pack(">HHHHI", 0, 1, 3, 1, 12)
            + struct.pack(">7H", 4, 16 + 8 * count, 0, 2 * count, 2 * power, power.bit_length() - 1,
                          2 * (count - power))
            + starts + b"\0\0" + starts + struct.pack(">{}H".format(count), *(delta for _, delta in segments))
            + bytes(2 * count))
    os2 = bytearray(96)
    struct.pack_into(">HhHH", os2, 0, 4, 600, weight, width)
    struct.pack_into(">HHhhhHH", os2, 64, codes[0], codes[-1], 900, -200, 0, 900, 200)
    struct.pack_into(">hh", os2, 86, 500, 700)
    tables = {
        b"head": struct.pack(">IIIIHHqq4hHHhhh", 0x10000, 0x10000, 0, 0x5F0F3CF5, 3, 1000, 0, 0, 0, 0, 550, 700,
                             0, 8, 2, 1, 0),
        b"hhea": struct.pack(">IhhhHhhhhhh4hhH", 0x10000, 900, -200, 0, 600, 0, 50, 550, 1, 0, 0, 0, 0, 0, 0, 0, 4),
        b"maxp": struct.pack(">IH13H", 0x10000, 4, 4, 1, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0),
        b"OS/2": bytes(os2),
        b"hmtx": struct.pack(">8h", 500, 0, 300, 0, 600, 50, 600, 50),
        b"cmap": cmap,
        b"loca": struct.pack(">5I", 0, 0, 0, len(block), len(block) + len(high)),
        b"glyf": block + high,
        b"name": struct.pack(">3H", 0, 0, 6),
        b"post": struct.pack(">IIhhIIIII", 0x30000, 0, -100, 50, 0, 0, 0, 0, 0),
    }
    if variable:
        tables[b"fvar"] = b""
    return sfnt(tables)


def marks(path):
    width, height = struct.unpack(">II", path.read_bytes()[16:24])
    data = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                          capture_output=True, check=True).stdout
    found = {}
    for y in range(height):
        row = data[3 * width * y:3 * width * (y + 1)]
        if row != BACKGROUND * width:
            found.update(((x, y), row[3 * x:3 * x + 3]) for x in range(width) if row[3 * x:3 * x + 3] != BACKGROUND)
    return width, height, found
TOKEN = "ya29.fake-access-token"
USAGE = {"promptTokenCount": 10, "candidatesTokenCount": 1120, "totalTokenCount": 1130}
FAKE_GCLOUD = """#!{python}
import sys
command = " ".join(sys.argv[1:])
with open({log!r}, "a") as log:
    log.write(command + "\\n")
code, out, err = {replies!r}.get(command, (2, "", "ERROR: unknown command\\n"))
sys.stdout.write(out)
sys.stderr.write(err)
sys.exit(code)
"""


def vertex_path(project="brand-ads-test", model="gemini-3-pro-image"):
    return "/v1/projects/{}/locations/global/publishers/google/models/{}:generateContent".format(project, model)


def prompt(body):
    return body["contents"][0]["parts"][0]["text"] if "contents" in body else body["prompt"]


def vertex_reply(model, prompt):
    if "BLOCKED" in prompt:
        return {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}, "usageMetadata": USAGE,
                "modelVersion": model}
    if "UNSAFE" in prompt:
        return {"candidates": [{"finishReason": "IMAGE_SAFETY"}], "usageMetadata": USAGE, "modelVersion": model}
    return {"candidates": [{"content": {"role": "model", "parts": [
                {"text": "Planning the layout.", "thought": True},
                {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(image_for(prompt)).decode()},
                 "thoughtSignature": "sig"}]},
            "finishReason": "STOP"}],
            "usageMetadata": USAGE, "modelVersion": model}


class FakeImagesAPI:
    def __init__(self, test):
        self.requests = []
        self.limited = set()
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                api.requests.append({"path": self.path, "authorization": self.headers["Authorization"],
                                     "body": body})
                asked = prompt(body)
                if "REDIRECT" in asked:
                    self.send_response(302)
                    self.send_header("Location", "/v1/elsewhere")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                busy = "UNAVAILABLE" in asked or ("RATE LIMITED" in asked and asked not in api.limited)
                if "REJECT" in asked:
                    message = "Your request was rejected."
                    if "UNPRINTABLE" in asked:
                        message += " \ud800"
                    status, reply = 400, {"error": {"message": message}}
                elif busy:
                    api.limited.add(asked)
                    status = 503 if "UNAVAILABLE" in asked else 429
                    reply = {"error": {"code": status, "message": "Try again later.", "status": "UNAVAILABLE"}}
                elif self.path.endswith(":generateContent"):
                    status, reply = 200, vertex_reply(self.path.rpartition("/")[2].partition(":")[0], asked)
                else:
                    image = RENDER[:16] if "SHORT" in asked else image_for(asked)
                    status, reply = 200, {"created": 1, "data": [{"b64_json": base64.b64encode(image).decode()}],
                                          "usage": {"input_tokens": 10, "output_tokens": 6240}}
                data = json.dumps(reply).encode()
                self.send_response(status)
                if busy:
                    self.send_header("Retry-After", "0")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data[: len(data) // 2] if "CUT OFF" in asked else data)

            def do_GET(self):
                api.requests.append({"path": self.path, "authorization": self.headers["Authorization"], "body": None})
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)
        self.url = "http://127.0.0.1:{}/v1".format(self.server.server_address[1])


class GenerateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="astack static ads ")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.run_folder = self.home / "Downloads/brand-ads-2026-11-23"
        self.run_folder.mkdir(parents=True)
        self.api = FakeImagesAPI(self)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.gcloud_log = self.home / "gcloud.log"
        self.write_gcloud()
        if FFMPEG:
            (self.bin / "ffmpeg").symlink_to(FFMPEG)

    def write_gcloud(self, token=(0, TOKEN + "\n", ""), project=(0, "brand-ads-test\n", "")):
        gcloud = self.bin / "gcloud"
        gcloud.write_text(FAKE_GCLOUD.format(python=sys.executable, log=str(self.gcloud_log), replies={
            "auth application-default print-access-token": token, "config get-value project": project}))
        gcloud.chmod(0o755)

    def gcloud_calls(self):
        return self.gcloud_log.read_text().splitlines() if self.gcloud_log.exists() else []

    def write_request(self, name, request):
        (self.run_folder / "{}.req.json".format(name)).write_text(json.dumps(request), encoding="utf-8")

    def write_brand(self):
        (self.run_folder / "fonts").mkdir(exist_ok=True)
        (self.run_folder / "layer.json").write_text(json.dumps(BRAND), encoding="utf-8")
        for style in BRAND["type"].values():
            (self.run_folder / style["font"]).write_bytes(font(style["weight"], WIDTH_CLASSES[style["width"]]))
        (self.run_folder / "lockup.png").write_bytes(png(240, [(LOGO + b"\xff") * 240] * 60, 6))

    def write_ad(self, name, art_size, size="1472x1840", **copy):
        self.write_request(name, {"prompt": "Night sky, no words, ART " + art_size, "size": size,
                                  "compose": {**COPY, **copy}})

    def assertLine(self, output, name, *parts):
        lines = [line for line in output.splitlines() if line.startswith(name + ":")]
        self.assertTrue(any(all(part in line for part in parts) for line in lines), output)

    def generate(self, *arguments, key="sk-test", project=None):
        env = {name: value for name, value in os.environ.items()
               if not name.startswith(("OPENAI_", "VERTEX_", "GOOGLE_", "CLOUDSDK_"))
               and not name.lower().endswith("_proxy")}
        env.update(HOME=str(self.home), PATH=str(self.bin), OPENAI_BASE_URL=self.api.url,
                   VERTEX_BASE_URL=self.api.url, no_proxy="*")
        if key:
            env["OPENAI_API_KEY"] = key
        if project:
            env["GOOGLE_CLOUD_PROJECT"] = project
        return subprocess.run([sys.executable, str(SCRIPT), str(self.run_folder), *arguments],
                              capture_output=True, text=True, env=env, timeout=60)

    def generate_on_vertex(self, *arguments, project=None):
        return self.generate("--backend", "gemini", *arguments, key=None, project=project)

    def test_an_ad_with_a_reference_is_generated_from_that_reference(self):
        reference = b"\x89PNG reference bytes"
        (self.run_folder / "og-image.png").write_bytes(reference)
        self.write_request("1-proof", {"prompt": "A headline", "size": "1472x1840", "quality": "high",
                                       "images": ["og-image.png"]})

        result = self.generate()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.api.requests, [{
            "path": "/v1/images/edits",
            "authorization": "Bearer sk-test",
            "body": {"model": "gpt-image-2.6-sunburst", "prompt": "A headline", "size": "1472x1840",
                     "quality": "high",
                     "images": [{"image_url": "data:image/png;base64," + base64.b64encode(reference).decode()}]},
        }])
        self.assertEqual((self.run_folder / "1-proof.png").read_bytes(), RENDER)
        self.assertEqual(json.loads((self.run_folder / "1-proof.res.json").read_text(encoding="utf-8")),
                         {"created": 1, "data": [{}], "usage": {"input_tokens": 10, "output_tokens": 6240}})
        self.assertLine(result.stdout, "1-proof", "done", "1472x1840")

    def test_an_ad_without_a_reference_is_generated_from_its_prompt_with_the_chosen_model(self):
        self.write_request("2-pain", {"prompt": "A headline", "size": "1472x1840", "quality": "high",
                                      "model": "gpt-image-1.5"})

        result = self.generate("--model", "gpt-image-2.6-flare")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([(request["path"], request["body"]) for request in self.api.requests], [
            ("/v1/images/generations",
             {"model": "gpt-image-2.6-flare", "prompt": "A headline", "size": "1472x1840", "quality": "high"}),
        ])
        self.assertEqual((self.run_folder / "2-pain.png").read_bytes(), RENDER)

    def test_an_ad_that_already_has_an_image_is_not_generated_again_and_its_size_is_shown(self):
        self.write_request("1-proof", {"prompt": "Kept"})
        earlier = png_header(1080, 1350)
        (self.run_folder / "1-proof.png").write_bytes(earlier)
        self.write_request("2-pain", {"prompt": "New"})

        result = self.generate()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([request["body"]["prompt"] for request in self.api.requests], ["New"])
        self.assertEqual((self.run_folder / "1-proof.png").read_bytes(), earlier)
        self.assertLine(result.stdout, "1-proof", "1080x1350")

    def test_a_rejected_ad_fails_while_the_others_finish(self):
        self.write_request("1-proof", {"prompt": "REJECT this one"})
        self.write_request("2-pain", {"prompt": "Fine"})

        result = self.generate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertLine(result.stdout, "1-proof", "FAILED", "Your request was rejected.")
        self.assertLine(result.stdout, "2-pain", "done", "1472x1840")
        self.assertFalse((self.run_folder / "1-proof.png").exists())
        self.assertEqual((self.run_folder / "2-pain.png").read_bytes(), RENDER)

    def test_an_error_that_cannot_be_printed_as_is_still_fails_only_its_ad(self):
        self.write_request("1-proof", {"prompt": "REJECT, UNPRINTABLE"})
        self.write_request("2-pain", {"prompt": "Fine"})

        result = self.generate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertLine(result.stdout, "1-proof", "FAILED", "Your request was rejected.")
        self.assertLine(result.stdout, "2-pain", "done", "1472x1840")

    def test_a_dry_run_checks_the_requests_without_calling_the_api(self):
        (self.run_folder / "og-image.png").write_bytes(b"reference")
        self.write_request("1-proof", {"prompt": "A headline", "size": "1472x1840", "quality": "high",
                                       "images": ["og-image.png"]})
        self.write_request("2-pain", {"prompt": "Another", "size": "1472x1840", "quality": "high",
                                      "images": ["missing.png"]})

        result = self.generate("--dry-run")

        self.assertEqual(self.api.requests, [])
        self.assertLine(result.stdout, "1-proof", "1472x1840", "high", "10 chars", "1 refs")
        self.assertLine(result.stdout, "2-pain", "FAILED", "missing.png")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(sorted(path.name for path in self.run_folder.iterdir()),
                         ["1-proof.req.json", "2-pain.req.json", "og-image.png"])

        self.generate()

        self.assertEqual([request["body"]["prompt"] for request in self.api.requests], ["A headline"])

    def test_a_reference_outside_the_run_folder_is_never_uploaded(self):
        outside = self.home / "outside.png"
        outside.write_bytes(b"not part of the run")
        (self.run_folder / "og-image.png").write_bytes(b"reference")
        (self.run_folder / "linked.png").symlink_to(outside)
        self.write_request("1-proof", {"prompt": "Inside", "images": ["og-image.png"]})
        self.write_request("2-pain", {"prompt": "Absolute", "images": [str(outside)]})
        self.write_request("3-offer", {"prompt": "Relative", "images": ["../../outside.png"]})
        self.write_request("4-story", {"prompt": "Linked", "images": ["linked.png"]})

        for arguments in [("--dry-run",), ()]:
            result = self.generate(*arguments)

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            for name in ["2-pain", "3-offer", "4-story"]:
                self.assertLine(result.stdout, name, "FAILED")
        self.assertEqual([request["body"]["prompt"] for request in self.api.requests], ["Inside"])

    def test_a_reference_that_loops_fails_while_the_others_finish(self):
        (self.run_folder / "loop.png").symlink_to("loop.png")
        self.write_request("1-proof", {"prompt": "Looped", "images": ["loop.png"]})
        self.write_request("2-pain", {"prompt": "Fine"})

        checked = self.generate("--dry-run")

        self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)
        self.assertLine(checked.stdout, "1-proof", "FAILED")
        self.assertLine(checked.stdout, "2-pain", "0 refs")

        result = self.generate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertLine(result.stdout, "1-proof", "FAILED")
        self.assertLine(result.stdout, "2-pain", "done", "1472x1840")
        self.assertEqual([request["body"]["prompt"] for request in self.api.requests], ["Fine"])

    def test_an_ad_whose_reply_is_cut_off_fails_while_the_others_finish(self):
        self.write_request("1-proof", {"prompt": "CUT OFF partway"})
        self.write_request("2-offer", {"prompt": "REJECT, then CUT OFF partway"})
        self.write_request("3-pain", {"prompt": "Fine"})

        result = self.generate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertLine(result.stdout, "1-proof", "FAILED")
        self.assertLine(result.stdout, "2-offer", "FAILED")
        self.assertLine(result.stdout, "3-pain", "done", "1472x1840")
        self.assertFalse((self.run_folder / "1-proof.png").exists())

    def test_a_redirect_is_not_followed(self):
        self.write_request("1-proof", {"prompt": "REDIRECT elsewhere"})
        self.write_request("2-pain", {"prompt": "Fine"})

        result = self.generate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertLine(result.stdout, "1-proof", "FAILED")
        self.assertLine(result.stdout, "2-pain", "done", "1472x1840")
        self.assertEqual([request["path"] for request in self.api.requests], ["/v1/images/generations"] * 2)

    def test_an_image_too_short_to_read_is_reported_without_a_size(self):
        self.write_request("1-proof", {"prompt": "SHORT image"})
        self.write_request("2-pain", {"prompt": "Fine"})

        result = self.generate()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertLine(result.stdout, "1-proof", "done")
        self.assertNotIn("1472x1840", [line for line in result.stdout.splitlines() if line.startswith("1-proof:")][0])
        self.assertLine(result.stdout, "2-pain", "done", "1472x1840")

    def test_an_ad_whose_files_cannot_be_saved_fails_while_the_others_finish(self):
        self.write_request("1-proof", {"prompt": "Unsaved"})
        (self.run_folder / "1-proof.res.json").mkdir()
        self.write_request("2-pain", {"prompt": "Fine"})

        result = self.generate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertLine(result.stdout, "1-proof", "FAILED")
        self.assertLine(result.stdout, "2-pain", "done", "1472x1840")

    def test_a_key_with_a_line_break_is_never_printed(self):
        self.write_request("1-proof", {"prompt": "A headline"})

        trailing = self.generate(key="sk-secret\r")

        self.assertEqual(trailing.returncode, 0, trailing.stdout + trailing.stderr)
        self.assertEqual([request["authorization"] for request in self.api.requests], ["Bearer sk-secret"])

        self.write_request("2-pain", {"prompt": "Another"})
        inside = self.generate(key="sk-sec\rret")

        self.assertNotEqual(inside.returncode, 0)
        self.assertEqual(len(self.api.requests), 1)
        self.assertNotIn("sk-sec", inside.stdout + inside.stderr)

    def test_a_real_run_without_an_api_key_stops_before_any_request(self):
        self.write_request("1-proof", {"prompt": "A headline"})

        result = self.generate(key=None)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OPENAI_API_KEY", result.stderr)
        self.assertEqual(self.api.requests, [])
        self.assertFalse((self.run_folder / "1-proof.png").exists())

        self.generate()

        self.assertEqual([request["body"]["prompt"] for request in self.api.requests], ["A headline"])

    def test_a_vertex_ad_is_generated_from_its_prompt_and_its_references_in_order(self):
        photo, logo = b"\x89PNG reference bytes", b"\xff\xd8 logo bytes"
        (self.run_folder / "og-image.png").write_bytes(photo)
        (self.run_folder / "logo.jpg").write_bytes(logo)
        self.write_request("1-proof", {"prompt": "A headline", "size": "1472x1840", "quality": "high",
                                       "images": ["og-image.png", "logo.jpg"]})

        result = self.generate_on_vertex()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.api.requests, [{
            "path": "/v1/projects/brand-ads-test/locations/global/publishers/google/models/"
                    "gemini-3-pro-image:generateContent",
            "authorization": "Bearer ya29.fake-access-token",
            "body": {"contents": [{"role": "user", "parts": [
                         {"text": "A headline"},
                         {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(photo).decode()}},
                         {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(logo).decode()}}]}],
                     "generationConfig": {"responseModalities": ["IMAGE"],
                                          "imageConfig": {"aspectRatio": "4:5", "imageSize": "2K"}}},
        }])
        self.assertEqual((self.run_folder / "1-proof.png").read_bytes(), RENDER)
        self.assertEqual(json.loads((self.run_folder / "1-proof.res.json").read_text(encoding="utf-8")), {
            "candidates": [{"content": {"role": "model", "parts": [
                               {"text": "Planning the layout.", "thought": True},
                               {"inlineData": {"mimeType": "image/png"}}]},
                            "finishReason": "STOP"}],
            "usageMetadata": USAGE, "modelVersion": "gemini-3-pro-image"})
        self.assertLine(result.stdout, "1-proof", "done", "1472x1840")

    def test_a_vertex_dry_run_shows_each_ratio_and_tier_without_calling_vertex_or_gcloud(self):
        (self.run_folder / "og-image.png").write_bytes(b"reference")
        sizes = {"1-portrait": "1472x1840", "2-square": "1024x1024", "3-square": "1080x1080", "4-wide": "3840x2160",
                 "5-huge": "8192x8192", "7-odd": "1000x1001", "8-empty": "0x0", "9-cinema": "2520x1080"}
        for name, size in sizes.items():
            self.write_request(name, {"prompt": name, "size": size, "quality": "high", "images": ["og-image.png"]})
        self.write_request("6-auto", {"prompt": "6-auto"})

        checked = self.generate_on_vertex("--dry-run")

        self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)
        lines = checked.stdout.splitlines()
        for line in ["dry-run: checking request files only. Not calling Vertex.",
                     "a real run would use gemini-3-pro-image",
                     "1-portrait: 4:5 2K, 10 chars, 1 refs",
                     "2-square: 1:1 1K, 8 chars, 1 refs",
                     "3-square: 1:1 2K, 8 chars, 1 refs",
                     "4-wide: 16:9 4K, 6 chars, 1 refs",
                     "5-huge: 1:1 4K, 6 chars, 1 refs",
                     "6-auto: auto, 6 chars, 0 refs",
                     "9-cinema: 21:9 2K, 8 chars, 1 refs"]:
            self.assertIn(line, lines)
        self.assertLine(checked.stdout, "7-odd", "FAILED", "1000x1001", "4:5")
        self.assertLine(checked.stdout, "8-empty", "FAILED", "0x0", "4:5")
        self.assertEqual(self.api.requests, [])
        self.assertEqual(self.gcloud_calls(), [])

        result = self.generate_on_vertex()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertLine(result.stdout, "7-odd", "FAILED", "1000x1001")
        self.assertLine(result.stdout, "8-empty", "FAILED", "0x0")
        self.assertIn("auth application-default print-access-token", self.gcloud_calls())
        self.assertEqual({prompt(request["body"]): request["body"]["generationConfig"]
                          for request in self.api.requests}, {
            "1-portrait": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "4:5", "imageSize": "2K"}},
            "2-square": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "1:1", "imageSize": "1K"}},
            "3-square": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "1:1", "imageSize": "2K"}},
            "4-wide": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "16:9", "imageSize": "4K"}},
            "5-huge": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "1:1", "imageSize": "4K"}},
            "6-auto": {"responseModalities": ["IMAGE"]},
            "9-cinema": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "21:9", "imageSize": "2K"}},
        })

    def test_a_busy_reply_is_retried_for_up_to_three_attempts_on_either_backend(self):
        for backend in ["openai", "gemini"]:
            with self.subTest(backend=backend):
                self.run_folder = self.home / backend
                self.run_folder.mkdir()
                self.api = FakeImagesAPI(self)
                self.write_request("1-proof", {"prompt": "RATE LIMITED once"})
                self.write_request("2-pain", {"prompt": "UNAVAILABLE every time"})
                self.write_request("3-offer", {"prompt": "Fine"})

                started = time.monotonic()
                result = self.generate("--backend", backend)
                elapsed = time.monotonic() - started

                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertLine(result.stdout, "1-proof", "done", "1472x1840")
                self.assertLine(result.stdout, "2-pain", "FAILED", "HTTP 503", "Try again later.")
                self.assertLine(result.stdout, "3-offer", "done", "1472x1840")
                self.assertEqual(Counter(prompt(request["body"]) for request in self.api.requests),
                                 {"RATE LIMITED once": 2, "UNAVAILABLE every time": 3, "Fine": 1})
                self.assertLess(elapsed, 4, "Retry-After: 0 should replace the default waits")

    def test_a_rejected_or_redirected_vertex_ad_fails_after_one_request_while_the_others_finish(self):
        self.write_request("1-proof", {"prompt": "REJECT this one"})
        self.write_request("2-pain", {"prompt": "REDIRECT elsewhere"})
        self.write_request("3-offer", {"prompt": "Fine"})

        result = self.generate_on_vertex()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertLine(result.stdout, "1-proof", "FAILED", "HTTP 400: Your request was rejected.")
        self.assertLine(result.stdout, "2-pain", "FAILED", "302")
        self.assertLine(result.stdout, "3-offer", "done", "1472x1840")
        self.assertEqual([request["path"] for request in self.api.requests], [vertex_path()] * 3)
        self.assertEqual(sorted(prompt(request["body"]) for request in self.api.requests),
                         ["Fine", "REDIRECT elsewhere", "REJECT this one"])

    def test_a_vertex_reply_without_an_image_fails_its_ad_with_the_reason(self):
        self.write_request("1-proof", {"prompt": "BLOCKED prompt"})
        self.write_request("2-pain", {"prompt": "UNSAFE image"})
        self.write_request("3-offer", {"prompt": "Fine"})

        result = self.generate_on_vertex()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertLine(result.stdout, "1-proof", "FAILED", "Vertex returned no image", "PROHIBITED_CONTENT")
        self.assertLine(result.stdout, "2-pain", "FAILED", "Vertex returned no image", "IMAGE_SAFETY")
        self.assertLine(result.stdout, "3-offer", "done", "1472x1840")
        self.assertEqual(sorted(path.name for path in self.run_folder.glob("*.png")), ["3-offer.png"])

    def test_a_vertex_run_uses_the_chosen_model_and_project_and_skips_an_ad_that_has_an_image(self):
        self.write_request("1-proof", {"prompt": "Kept"})
        earlier = png_header(1856, 2304)
        (self.run_folder / "1-proof.png").write_bytes(earlier)
        self.write_request("2-pain", {"prompt": "New"})

        result = self.generate_on_vertex("--model", "gemini-3.1-flash-image", project="other-project-1")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([request["path"] for request in self.api.requests],
                         [vertex_path("other-project-1", "gemini-3.1-flash-image")])
        self.assertEqual((self.run_folder / "1-proof.png").read_bytes(), earlier)
        self.assertLine(result.stdout, "1-proof", "exists", "1856x2304")
        self.assertNotIn("config get-value project", self.gcloud_calls())

    def test_a_vertex_run_without_a_token_stops_before_any_request_and_never_prints_one(self):
        self.write_request("1-proof", {"prompt": "A headline"})
        for token, reason in [((1, "", "ERROR: Reauthentication failed.\n"), "Reauthentication failed."),
                              ((0, "ya29.sec\nret\n", ""), "")]:
            with self.subTest(token=token):
                self.write_gcloud(token=token)

                result = self.generate_on_vertex()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("gcloud auth application-default login", result.stderr)
                self.assertIn(reason, result.stderr)
                self.assertNotIn("ya29", result.stdout + result.stderr)
                self.assertEqual(self.api.requests, [])

    def test_a_vertex_run_stops_before_any_request_without_a_usable_project_or_model(self):
        self.write_request("1-proof", {"prompt": "A headline"})
        self.write_gcloud(project=(0, "\n", "(unset)\n"))

        unset = self.generate_on_vertex()
        malformed = self.generate_on_vertex(project="Bad/Project")
        self.write_gcloud()
        traversal = self.generate_on_vertex("--model", "../models/other")

        for result in [unset, malformed]:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("GOOGLE_CLOUD_PROJECT", result.stderr)
        self.assertNotEqual(traversal.returncode, 0)
        self.assertIn("not a Vertex model name", traversal.stderr)
        self.assertEqual(self.api.requests, [])
        self.assertFalse((self.run_folder / "1-proof.png").exists())

    @needs_ffmpeg
    def test_composed_ads_set_the_brand_type_on_the_exact_background_inside_each_safe_zone(self):
        self.write_brand()
        self.write_ad("1-portrait", "928x1152")
        self.write_ad("2-story", "576x1024", size="1080x1920", headline="Read the sky\nyou were\nborn under",
                      subline="A written reading\nof your chart.")
        self.write_ad("3-square", "800x800", size="1080x1080")

        result = self.generate_on_vertex()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertLine(result.stdout, "1-portrait", "done 928x1152, composed")
        self.assertLine(result.stdout, "2-story", "done 576x1024, composed")
        self.assertLine(result.stdout, "3-square", "done 800x800, composed")
        for name, zone, band in [("1-portrait", (0.05, 0.05, 0.95, 0.95), 0.3),
                                 ("2-story", (0.06, 0.14, 0.94, 0.65), 0.2),
                                 ("3-square", (0.05, 0.05, 0.95, 0.95), 0.3)]:
            with self.subTest(ad=name):
                width, height, found = marks(self.run_folder / (name + ".png"))
                left, top, right, bottom = square(width, height)
                self.assertEqual(found.get(((left + right) // 2, (top + bottom) // 2)), ART_SQUARE)
                self.assertNotIn((left - 10, (top + bottom) // 2), found)
                self.assertNotIn((width // 2, (top + height) // 2), found)
                upper = [(y, color) for (x, y), color in found.items() if y < top]
                self.assertLess(min(y for y, color in upper if color == LOGO),
                                min(y for y, color in upper if color == WHITE))
                lower = {color for (x, y), color in found.items() if y >= bottom}
                self.assertLessEqual({WHITE, PILL}, lower)
                type_above = max(y for y, color in upper if color == WHITE)
                type_below = min(y for (x, y), color in found.items() if y >= bottom and color == WHITE)
                self.assertGreaterEqual(type_below - type_above, band * height)
                self.assertEqual([(x, y) for x, y in found if not (zone[0] * width <= x and x + 1 <= zone[2] * width
                                                                   and zone[1] * height <= y
                                                                   and y + 1 <= zone[3] * height)], [])
        settings = json.loads((self.run_folder / "1-portrait.type.json").read_text(encoding="utf-8"))
        self.assertEqual([(line["role"], line["text"], line["font"], line["weight"], line["width"])
                          for line in settings["text"]], [
            ("headline", "READ THE SKY YOU WERE", "fonts/headline.ttf", 500, 125),
            ("headline", "BORN UNDER", "fonts/headline.ttf", 500, 125),
            ("subline", "A written reading.", "fonts/subline.ttf", 450, 100),
            ("cta", "GET YOUR READING", "fonts/cta.ttf", 700, 100),
        ])

    @needs_ffmpeg
    def test_art_drawn_near_a_brand_accent_is_set_in_the_exact_accent_over_the_exact_background(self):
        self.write_brand()
        (self.run_folder / "layer.json").write_text(json.dumps({**BRAND, "accents": ["#ffd9c4"]}), encoding="utf-8")
        self.write_request("1-accent", {"prompt": "Gold wheel, no words, ACCENT, ART 928x1152", "size": "1472x1840",
                                        "compose": COPY})
        self.write_ad("2-other", "928x1152")

        result = self.generate_on_vertex()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        accent = bytes.fromhex("ffd9c4")
        for name, inner, edge in [
                ("1-accent", accent, bytes(round(t + (a - t) / 2) for t, a in zip(BACKGROUND, accent))),
                ("2-other", ART_SQUARE, bytes((a + b) // 2 for a, b in zip(ART_BACKGROUND, ART_SQUARE)))]:
            with self.subTest(ad=name):
                width, height, found = marks(self.run_folder / (name + ".png"))
                left, top, right, bottom = square(width, height)
                middle = (top + bottom) // 2
                self.assertEqual(found.get(((left + right) // 2, middle)), inner)
                drawn = found.get((left - 1, middle))
                self.assertTrue(drawn and all(abs(a - b) <= 1 for a, b in zip(drawn, edge)), drawn)
        settings = json.loads((self.run_folder / "1-accent.type.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["art_accents"], {"#ffd9c4": "#ffc5a0"})

    @needs_ffmpeg
    def test_a_rerun_composes_the_same_bytes_from_the_saved_art_without_rendering_again(self):
        self.write_brand()
        self.write_ad("1-proof", "928x1152")
        outside = self.home / "outside.json"
        outside.write_text("kept", encoding="utf-8")
        (self.run_folder / "art").mkdir()
        for planted in ["art/1-proof.png.partial", "1-proof.png.partial", "1-proof.type.json.partial",
                        "1-proof.res.json.partial", "1-proof.res.json"]:
            (self.run_folder / planted).symlink_to(outside)
        first = self.generate_on_vertex()
        composed = (self.run_folder / "1-proof.png").read_bytes()
        (self.run_folder / "1-proof.type.json").unlink()
        (self.run_folder / "1-proof.type.json").symlink_to(outside)

        again = self.generate_on_vertex()

        self.assertLine(first.stdout, "1-proof", "done 928x1152, composed")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertLine(again.stdout, "1-proof", "exists 928x1152, skip render, composed")
        self.assertEqual((self.run_folder / "1-proof.png").read_bytes(), composed)
        self.assertEqual(outside.read_text(encoding="utf-8"), "kept")
        self.assertEqual(json.loads((self.run_folder / "1-proof.type.json").read_text(encoding="utf-8"))["art"],
                         "art/1-proof.png")

        self.write_ad("1-proof", "928x1152", headline="Read the sky")
        edited = self.generate_on_vertex()

        self.assertEqual(edited.returncode, 0, edited.stdout + edited.stderr)
        self.assertLine(edited.stdout, "1-proof", "exists 928x1152, skip render, composed")
        settings = json.loads((self.run_folder / "1-proof.type.json").read_text(encoding="utf-8"))
        self.assertEqual([line["text"] for line in settings["text"] if line["role"] == "headline"], ["READ THE SKY"])
        self.assertNotEqual((self.run_folder / "1-proof.png").read_bytes(), composed)
        self.assertEqual(len(self.api.requests), 1)

    @needs_ffmpeg
    def test_a_symlinked_art_folder_fails_without_writing_through_it(self):
        self.write_brand()
        self.write_ad("1-proof", "928x1152")
        self.write_request("2-plain", {"prompt": "Fine"})
        elsewhere = self.home / "elsewhere"
        elsewhere.mkdir()
        (self.run_folder / "art").symlink_to(elsewhere)

        result = self.generate_on_vertex()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertLine(result.stdout, "1-proof", "FAILED", "art")
        self.assertLine(result.stdout, "2-plain", "done")
        self.assertEqual([prompt(request["body"]) for request in self.api.requests], ["Fine"])
        self.assertEqual(list(elsewhere.iterdir()), [])

    @needs_ffmpeg
    def test_type_stays_below_the_story_interface_with_marks_above_the_capitals_and_no_logo(self):
        self.write_brand()
        (self.run_folder / "layer.json").write_text(json.dumps({**BRAND, "logo": None}), encoding="utf-8")
        self.write_ad("1-story", "576x1024", size="1080x1920", headline="Éclat")

        result = self.generate_on_vertex()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        _, height, found = marks(self.run_folder / "1-story.png")
        self.assertGreaterEqual(min(y for x, y in found), 0.14 * height)

    @needs_ffmpeg
    def test_the_dry_run_art_band_is_where_the_art_shows_unfaded(self):
        self.write_brand()
        for name, size, art_size, headline in [("1-portrait", "1472x1840", "928x1152", COPY["headline"]),
                                               ("2-story", "1080x1920", "576x1024", "Read the sky")]:
            self.write_request(name, {"prompt": "Ruled sky, RULED, ART " + art_size, "size": size,
                                      "compose": {**COPY, "headline": headline}})

        checked = self.generate_on_vertex("--dry-run")
        result = self.generate_on_vertex()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in ["1-portrait", "2-story"]:
            with self.subTest(ad=name):
                low, high = map(int, re.search(r"(?m)^{}: compose .* art ([0-9]+)-([0-9]+)%$".format(name),
                                               checked.stdout).groups())
                width, height, found = marks(self.run_folder / (name + ".png"))
                shown = [y for (x, y), color in found.items() if x == column(width) and color == ART_SQUARE]
                self.assertAlmostEqual(100 * min(shown) / height, low, delta=1.5)
                self.assertAlmostEqual(100 * (max(shown) + 1) / height, high, delta=1.5)

    @needs_ffmpeg
    def test_art_whose_background_is_far_from_the_hex_is_composed_with_a_note(self):
        self.write_brand()
        self.write_ad("1-day", "928x1152", background="#5b86a8")
        self.write_ad("2-night", "928x1152")

        result = self.generate_on_vertex()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertLine(result.stdout, "1-day", "done 928x1152, composed, art background not filled")
        self.assertNotIn("not filled", [line for line in result.stdout.splitlines() if line.startswith("2-night:")][0])

    @needs_ffmpeg
    def test_a_font_file_that_ffmpeg_cannot_load_fails_instead_of_falling_back(self):
        self.write_brand()
        broken = font(500, 7).replace(b"hhea", b"xxxx", 1)
        (self.run_folder / "fonts/headline.ttf").write_bytes(broken)
        self.write_ad("1-proof", "928x1152")
        self.write_request("2-plain", {"prompt": "Fine"})

        checked = self.generate_on_vertex("--dry-run")
        result = self.generate_on_vertex()

        for run in [checked, result]:
            self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
            self.assertLine(run.stdout, "1-proof", "FAILED", "headline.ttf")
        self.assertEqual([prompt(request["body"]) for request in self.api.requests], ["Fine"])

    def test_a_compose_request_with_an_unusable_font_fails_before_any_image_request(self):
        self.write_request("2-plain", {"prompt": "Fine"})
        headline = self.run_folder / "fonts/headline.ttf"
        for case, data, copy, parts in [
                ("missing", None, {}, ["fonts/headline.ttf", "missing"]),
                ("variable", font(500, 7, variable=True), {}, ["fonts/headline.ttf", "variable font",
                                                               "weight 500 width 125"]),
                ("wrong weight", font(400, 7), {}, ["fonts/headline.ttf", "weight 400", "weight 500"]),
                ("missing glyph", font(500, 7), {"headline": "Read the sky → now"},
                 ["headline", "fonts/headline.ttf", "U+2192"])]:
            with self.subTest(case=case):
                self.write_brand()
                if data is None:
                    headline.unlink()
                else:
                    headline.write_bytes(data)
                self.write_ad("1-proof", "928x1152", **copy)
                (self.run_folder / "2-plain.png").unlink(missing_ok=True)
                sent = len(self.api.requests)

                checked = self.generate_on_vertex("--dry-run")
                result = self.generate_on_vertex()

                for run in [checked, result]:
                    self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
                    self.assertLine(run.stdout, "1-proof", "FAILED", *parts)
                self.assertLine(result.stdout, "2-plain", "done")
                self.assertEqual([prompt(request["body"]) for request in self.api.requests[sent:]], ["Fine"])
                self.assertFalse((self.run_folder / "art/1-proof.png").exists())

    @needs_ffmpeg
    def test_a_compose_dry_run_prints_the_type_layout_or_why_it_does_not_fit_without_calling_anything(self):
        self.write_brand()
        self.write_ad("1-proof", "928x1152", background="#5B86A8")
        self.write_ad("2-story", "576x1024", size="1080x1920", subline="A written reading\nof your chart.")
        self.write_ad("3-long", "928x1152", headline="Read the sky you were born under tonight and every night")
        before = sorted(path.name for path in self.run_folder.iterdir())

        checked = self.generate_on_vertex("--dry-run")

        self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)
        self.assertRegex(checked.stdout, r"(?m)^1-proof: compose 4:5 on #5b86a8, headline 2x[0-9]+px, "
                                         r"subline 1x[0-9]+px, cta [0-9]+px, logo lockup\.png, art [0-9]+-[0-9]+%$")
        self.assertRegex(checked.stdout, r"(?m)^2-story: compose 9:16 on #061521, headline 2x[0-9]+px, "
                                         r"subline 2x[0-9]+px, cta [0-9]+px, logo lockup\.png, art [0-9]+-[0-9]+%$")
        self.assertLine(checked.stdout, "3-long", "FAILED", "READ THE SKY YOU WERE BORN UNDER TONIGHT AND EVERY NIGHT",
                        "minimum")
        self.assertEqual(self.api.requests, [])
        self.assertEqual(self.gcloud_calls(), [])
        self.assertEqual(sorted(path.name for path in self.run_folder.iterdir()), before)

        self.generate_on_vertex()

        self.assertEqual(sorted(prompt(request["body"]) for request in self.api.requests),
                         ["Night sky, no words, ART 576x1024", "Night sky, no words, ART 928x1152"])

    @needs_ffmpeg
    def test_an_openai_compose_request_sends_only_the_image_fields_and_composes_the_reply(self):
        self.write_brand()
        self.write_request("1-proof", {"prompt": "Night sky ART 736x920", "size": "1472x1840", "quality": "high",
                                       "compose": COPY})

        result = self.generate()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([(request["path"], request["body"]) for request in self.api.requests], [
            ("/v1/images/generations", {"model": "gpt-image-2.6-sunburst", "prompt": "Night sky ART 736x920",
                                        "size": "1472x1840", "quality": "high"}),
        ])
        self.assertLine(result.stdout, "1-proof", "done 736x920, composed")
        self.assertEqual(marks(self.run_folder / "1-proof.png")[:2], (736, 920))


if __name__ == "__main__":
    unittest.main()
