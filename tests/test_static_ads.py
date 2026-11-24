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
                if "REJECT" in body["prompt"]:
                    status, reply = 400, {"error": {"message": "Your request was rejected."}}
                else:
                    status, reply = 200, {"created": 1, "data": [{"b64_json": base64.b64encode(RENDER).decode()}],
                                          "usage": {"input_tokens": 10, "output_tokens": 6240}}
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

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
        env = {name: value for name, value in os.environ.items() if not name.startswith("OPENAI_")}
        env.update(HOME=str(self.home), OPENAI_BASE_URL=self.api.url)
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

    def test_an_ad_that_already_has_an_image_is_not_generated_again(self):
        self.write_request("1-proof", {"prompt": "Kept"})
        (self.run_folder / "1-proof.png").write_bytes(b"earlier render")
        self.write_request("2-pain", {"prompt": "New"})

        result = self.generate()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([request["body"]["prompt"] for request in self.api.requests], ["New"])
        self.assertEqual((self.run_folder / "1-proof.png").read_bytes(), b"earlier render")
        self.assertIn("1-proof: exists, skip", result.stdout)

    def test_a_rejected_ad_fails_while_the_others_finish(self):
        self.write_request("1-proof", {"prompt": "REJECT this one"})
        self.write_request("2-pain", {"prompt": "Fine"})

        result = self.generate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertLine(result.stdout, "1-proof", "FAILED", "Your request was rejected.")
        self.assertLine(result.stdout, "2-pain", "done", "1472x1840")
        self.assertFalse((self.run_folder / "1-proof.png").exists())
        self.assertEqual((self.run_folder / "2-pain.png").read_bytes(), RENDER)

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

    def test_an_ad_whose_files_cannot_be_saved_fails_while_the_others_finish(self):
        self.write_request("1-proof", {"prompt": "Unsaved"})
        (self.run_folder / "1-proof.res.json").mkdir()
        self.write_request("2-pain", {"prompt": "Fine"})

        result = self.generate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertLine(result.stdout, "1-proof", "FAILED")
        self.assertLine(result.stdout, "2-pain", "done", "1472x1840")

    def test_a_real_run_without_an_api_key_stops_before_any_request(self):
        self.write_request("1-proof", {"prompt": "A headline"})

        result = self.generate(key=None)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OPENAI_API_KEY", result.stderr)
        self.assertEqual(self.api.requests, [])
        self.assertFalse((self.run_folder / "1-proof.png").exists())


if __name__ == "__main__":
    unittest.main()
