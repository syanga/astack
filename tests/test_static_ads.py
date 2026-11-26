import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zlib

SCRIPT = Path(__file__).resolve().parents[1] / "skills/static-ads/scripts/generate.py"


def png_header(width, height):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IEND", b""))


RENDER = png_header(1472, 1840)


class FakeImagesAPI:
    def __init__(self, test):
        self.requests = []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                api.requests.append({"path": self.path, "authorization": self.headers["Authorization"],
                                     "body": body})
                if "REDIRECT" in body["prompt"]:
                    self.send_response(302)
                    self.send_header("Location", "/v1/elsewhere")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if "REJECT" in body["prompt"]:
                    message = "Your request was rejected."
                    if "UNPRINTABLE" in body["prompt"]:
                        message += " \ud800"
                    status, reply = 400, {"error": {"message": message}}
                else:
                    image = RENDER[:16] if "SHORT" in body["prompt"] else RENDER
                    status, reply = 200, {"created": 1, "data": [{"b64_json": base64.b64encode(image).decode()}],
                                          "usage": {"input_tokens": 10, "output_tokens": 6240}}
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data[: len(data) // 2] if "CUT OFF" in body["prompt"] else data)

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

    def write_request(self, name, request):
        (self.run_folder / "{}.req.json".format(name)).write_text(json.dumps(request), encoding="utf-8")

    def assertLine(self, output, name, *parts):
        lines = [line for line in output.splitlines() if line.startswith(name + ":")]
        self.assertTrue(any(all(part in line for part in parts) for line in lines), output)

    def generate(self, *arguments, key="sk-test"):
        env = {name: value for name, value in os.environ.items()
               if not name.startswith("OPENAI_") and not name.lower().endswith("_proxy")}
        env.update(HOME=str(self.home), OPENAI_BASE_URL=self.api.url, no_proxy="*")
        if key:
            env["OPENAI_API_KEY"] = key
        return subprocess.run([sys.executable, str(SCRIPT), str(self.run_folder), *arguments],
                              capture_output=True, text=True, env=env, timeout=60)

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


if __name__ == "__main__":
    unittest.main()
