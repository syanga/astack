import base64
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib

SCRIPT = Path(__file__).resolve().parents[1] / "skills/static-ads/scripts/generate.py"


def png_header(width, height):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IEND", b""))


RENDER = png_header(1472, 1840)
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
                {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(RENDER).decode()},
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
                    image = RENDER[:16] if "SHORT" in asked else RENDER
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

    def write_gcloud(self, token=(0, TOKEN + "\n", ""), project=(0, "brand-ads-test\n", "")):
        gcloud = self.bin / "gcloud"
        gcloud.write_text(FAKE_GCLOUD.format(python=sys.executable, log=str(self.gcloud_log), replies={
            "auth application-default print-access-token": token, "config get-value project": project}))
        gcloud.chmod(0o755)

    def gcloud_calls(self):
        return self.gcloud_log.read_text().splitlines() if self.gcloud_log.exists() else []

    def write_request(self, name, request):
        (self.run_folder / "{}.req.json".format(name)).write_text(json.dumps(request), encoding="utf-8")

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
                 "5-huge": "8192x8192", "7-odd": "1000x1001", "8-empty": "0x0"}
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
                     "6-auto: auto, 6 chars, 0 refs"]:
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


if __name__ == "__main__":
    unittest.main()
