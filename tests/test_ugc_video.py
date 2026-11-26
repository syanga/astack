from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlsplit

SCRIPT = Path(__file__).resolve().parents[1] / "skills/ugc-video/scripts/make_ugc.py"
TTS = "/fal/fal-ai/minimax/speech-02-hd"
CLONE = "/fal/fal-ai/minimax/voice-clone"
CREATE = "/kie/api/v1/jobs/createTask"
FAL_KEY, KIE_KEY = "fal-secret-key", "kie-secret-key"
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
TEXT = "Acme answers your phone 24/7. It books the call, sends the reminder, and texts you the details. Try it free for 14 days."
SPOKEN = ("Acme answers your phone twenty-four seven. It books the call, sends the reminder, and texts you the details. "
          "Try it free for fourteen days.")
SPEAKER, SETTING = "a woman in her 30s, short dark hair, navy cardigan", "a quiet home office with a bookshelf"
HOOK = """import json, sys
from pathlib import Path
served, base, log, path, key = sys.argv[1:]
Path(served, key).parent.mkdir(parents=True, exist_ok=True)
Path(served, key).write_bytes(Path(path).read_bytes())
with open(log, "a") as f:
    f.write(json.dumps({"key": key, "url": base + "/" + key}) + "\\n")
print("uploaded " + key)
print(base + "/" + key)
print()
"""


def storage_key(name, content):
    return "ugc-video/{}/{}".format(hashlib.sha256(content).hexdigest()[:16], name)


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *map(str, args)], check=True)


class FakeVendors:
    def __init__(self, test, served):
        self.requests, self.served = [], served
        self.refuse_fal, self.refuse_kie, self.kie_fail, self.poll_base = [], [], None, None
        self.poll_errors, self.short = [], set()
        self.jobs, self.polls = {}, {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fake.handle(self, None)

            def do_POST(self):
                fake.handle(self, json.loads(self.rfile.read(int(self.headers["Content-Length"]))))

            def do_PUT(self):
                fake.handle(self, self.rfile.read(int(self.headers["Content-Length"])))

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)
        self.url = "http://127.0.0.1:{}".format(self.server.server_address[1])

    def handle(self, handler, body):
        path = urlsplit(handler.path).path
        self.requests.append({"method": handler.command, "path": path,
                              "auth": handler.headers.get("Authorization"), "body": body})
        polls = self.polls[path] = self.polls.get(path, 0) + 1
        if path.startswith("/files/") and (self.served / path[len("/files/"):]).is_file():
            data = (self.served / path[len("/files/"):]).read_bytes()
            if path in self.short:
                return self.send(handler, 200, data[:len(data) // 2], length=len(data))
            return self.send(handler, 200, data)
        if path == "/falrest/storage/upload/initiate":
            name = "fal-{}-{}".format(len(self.requests), body["file_name"])
            return self.send(handler, 200, {"upload_url": self.url + "/put/" + name,
                                            "file_url": self.url + "/files/" + name})
        if handler.command == "PUT" and path.startswith("/put/"):
            (self.served / path[len("/put/"):]).write_bytes(body)
            return self.send(handler, 200, b"")
        if handler.command == "POST" and path.startswith("/fal/"):
            if self.refuse_fal:
                return self.send(handler, self.refuse_fal.pop(0), {"detail": "busy"})
            rid = "req-{}".format(len(self.jobs))
            self.jobs[rid] = path
            base = "{}/fal/requests/{}".format(self.poll_base or self.url, rid)
            return self.send(handler, 200, {"request_id": rid, "status_url": base + "/status", "response_url": base})
        if path.startswith("/fal/requests/"):
            rid = path.split("/")[3]
            if path.endswith("/status"):
                return self.send(handler, 200, {"status": "IN_PROGRESS" if polls == 1 else "COMPLETED"})
            if self.jobs.get(rid) == CLONE:
                return self.send(handler, 200, {"custom_voice_id": "voice-new"})
            return self.send(handler, 200, {"audio": {"url": self.url + "/files/tts.mp3"}})
        if path == CREATE:
            if self.refuse_kie:
                return self.send(handler, 200, {"code": self.refuse_kie.pop(0), "msg": "slow down", "data": None})
            return self.send(handler, 200, {"code": 200, "msg": "success", "data": {"taskId": "task-1"}})
        if path == "/kie/api/v1/jobs/recordInfo":
            if self.poll_errors:
                return self.send(handler, self.poll_errors.pop(0), {"msg": "unauthorized"})
            record = {"taskId": "task-1", "state": "generating"}
            if polls > 1 and self.kie_fail:
                record.update(state="fail", failCode="500", failMsg=self.kie_fail)
            elif polls > 1:
                record.update(state="success", creditsConsumed=140,
                              resultJson=json.dumps({"resultUrls": [self.url + "/files/render.mp4"]}))
            return self.send(handler, 200, {"code": 200, "msg": "success", "data": record})
        self.send(handler, 404, {})

    @staticmethod
    def send(handler, status, payload, length=None):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        handler.send_response(status)
        handler.send_header("Content-Length", str(len(data) if length is None else length))
        handler.end_headers()
        handler.wfile.write(data)


class MakeUgcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        media = tempfile.TemporaryDirectory(prefix="astack ugc media ")
        cls.addClassCleanup(media.cleanup)
        cls.media = Path(media.name)
        if HAS_FFMPEG:
            ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=4.5", "-c:a", "libmp3lame", cls.media / "tts.mp3")
            ffmpeg("-f", "lavfi", "-i", "testsrc=size=480x854:rate=12:duration=2", "-f", "lavfi", "-i",
                   "sine=duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                   cls.media / "render.mp4")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="astack ugc video ")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.served = self.home / "served"
        shutil.copytree(self.media, self.served)
        self.fake = FakeVendors(self, self.served)
        self.presenter = self.home / "brand" / "maya"
        self.presenter.mkdir(parents=True)
        self.meta = {"speaker": SPEAKER, "setting": SETTING, "voice_id": "voice-abc", "notes": "kept"}
        (self.presenter / "presenter.json").write_text(json.dumps(self.meta))
        (self.presenter / "presenter.jpg").write_bytes(b"still")
        (self.presenter / "voice-ref.mp3").write_bytes(b"voice sample")
        self.hook_log = self.home / "hook.log"
        (self.home / "hook.py").write_text(HOOK)
        self.hook = shlex.join([sys.executable, str(self.home / "hook.py"), str(self.served),
                                self.fake.url + "/files", str(self.hook_log)])
        self.out = self.home / "ugc-videos" / "test-ad"

    def make(self, *args, fal_key=FAL_KEY, hook=True):
        env = {name: value for name, value in os.environ.items()
               if not name.startswith(("FAL_", "KIE_", "UGC_")) and not name.lower().endswith("_proxy")}
        env.update(HOME=str(self.home), no_proxy="*", UGC_RETRY_SCALE="0.001", FAL_REST_URL=self.fake.url + "/falrest",
                   FAL_QUEUE_URL=self.fake.url + "/fal", KIE_API_URL=self.fake.url + "/kie", KIE_API_KEY=KIE_KEY)
        if hook:
            env["UGC_UPLOAD_CMD"] = self.hook
        if fal_key:
            env["FAL_KEY"] = fal_key
        return subprocess.run([sys.executable, str(SCRIPT), "--presenter", str(self.presenter), *args],
                              cwd=self.home, capture_output=True, text=True, env=env, timeout=300)

    def run_script(self, *args, **kwargs):
        return self.make("--script", TEXT, "--slug", "test-ad", "--no-fix", *args, **kwargs)

    def posts(self, path):
        return [r["body"] for r in self.fake.requests if r["method"] == "POST" and r["path"] == path]

    def uploads(self):
        if not self.hook_log.exists():
            return {}
        return {entry["key"]: entry["url"] for entry in map(json.loads, self.hook_log.read_text().splitlines())}

    def test_a_dry_run_prints_length_cost_and_prompt_without_any_request(self):
        result = self.make("--script", TEXT, "--caps", "Acme", "--dry-run", fal_key=None)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        words = len(SPOKEN.split())
        seconds = math.ceil(words / 3.6)
        cost = seconds * 0.14 + len(SPOKEN) / 1000 * 0.10
        self.assertIn("1 script: {} words spoken, read ~{:.1f}s -> ~{}s video, est ${:.2f}".format(
            words, words / 3.6, seconds, cost), result.stdout)
        prompt = result.stdout.split("prompt:\n", 1)[1]
        for part in (SPEAKER, SETTING, SPOKEN.replace("Acme", "ACME")):
            self.assertIn(part, prompt)
        self.assertIn("  FAL_KEY:", result.stdout)
        self.assertNotIn("  KIE_API_KEY:", result.stdout)
        self.assertNotIn(KIE_KEY, result.stdout + result.stderr)
        self.assertEqual(self.fake.requests, [])
        self.assertEqual(self.uploads(), {})
        self.assertFalse((self.home / "ugc-videos").exists())

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg and ffprobe")
    def test_a_run_sends_the_script_and_presenter_to_the_vendors_and_ends_at_1080x1920(self):
        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.posts(TTS), [{"text": SPOKEN, "output_format": "url", "voice_setting": {
            "voice_id": "voice-abc", "speed": 1, "vol": 1, "pitch": 0}}])
        uploads = self.uploads()
        [task] = self.posts(CREATE)
        self.assertEqual(task["model"], "bytedance/seedance-2-5")
        prompt = task["input"].pop("prompt")
        self.assertEqual(task["input"], {
            "reference_image_urls": [uploads[storage_key("presenter.jpg", b"still")]],
            "reference_audio_urls": [uploads[storage_key("voice.mp3", (self.out / "voice.mp3").read_bytes())]],
            "resolution": "480p", "aspect_ratio": "9:16", "duration": 5, "generate_audio": True,
            "output_format": "mp4"})
        self.assertIn(SPOKEN, prompt)
        self.assertIn(SPEAKER, prompt)
        self.assertEqual({(r["path"].split("/")[1], r["auth"]) for r in self.fake.requests},
                         {("fal", "Key " + FAL_KEY), ("kie", "Bearer " + KIE_KEY), ("files", None)})
        self.assertIn("/fal/requests/req-0/status", [r["path"] for r in self.fake.requests])
        size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=width,height", "-of", "csv=s=x:p=0", str(self.out / "test-ad.mp4")],
                              capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(size, "1080x1920")
        written = b"".join(path.read_bytes() for path in self.out.rglob("*") if path.is_file())
        for key in (FAL_KEY, KIE_KEY):
            self.assertNotIn(key.encode(), written)
            self.assertNotIn(key, result.stdout + result.stderr)

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg and ffprobe")
    def test_a_refused_submit_is_retried_until_accepted_and_never_after(self):
        self.fake.refuse_fal = [429]
        self.fake.refuse_kie = [429]

        result = self.run_script("--no-upscale")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.posts(TTS)), 2)
        self.assertEqual(len(self.posts(CREATE)), 2)
        self.assertTrue((self.out / "raw-480p.mp4").is_file())

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg and ffprobe")
    def test_a_gateway_error_on_submit_is_not_retried_because_the_job_may_exist(self):
        self.fake.refuse_fal = [504]

        result = self.run_script("--no-upscale")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("may have been accepted", result.stderr)
        self.assertEqual(len(self.posts(TTS)), 1)
        self.assertEqual(self.posts(CREATE), [])

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg and ffprobe")
    def test_a_take_left_unfinished_is_checked_again_on_the_next_run_not_resubmitted(self):
        self.fake.poll_errors = [401]

        first = self.run_script("--no-upscale")
        second = self.run_script("--no-upscale")

        self.assertNotEqual(first.returncode, 0)
        self.assertIn("task-1", first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(len(self.posts(CREATE)), 1)
        self.assertEqual(len(self.posts(TTS)), 1)
        self.assertTrue((self.out / "raw-480p.mp4").is_file())

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg and ffprobe")
    def test_a_folder_holding_another_script_stops_the_run_before_any_request(self):
        self.assertEqual(self.run_script("--no-upscale").returncode, 0)
        requests = len(self.fake.requests)

        result = self.make("--script", "A different ad for another brand.", "--slug", "test-ad", "--no-fix")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--slug", result.stderr)
        self.assertEqual(len(self.fake.requests), requests)
        self.assertEqual((self.out / "script.txt").read_text(), TEXT + "\n")

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg and ffprobe")
    def test_a_failed_take_is_reported_with_the_cost_of_another_and_not_resubmitted(self):
        self.fake.kie_fail = "Content flagged by moderation"

        result = self.run_script("--no-upscale")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Content flagged by moderation", result.stdout + result.stderr)
        self.assertIn("$0.70", result.stdout + result.stderr)
        self.assertEqual(len(self.posts(CREATE)), 1)
        self.assertTrue((self.out / "voice.mp3").is_file())
        self.assertFalse((self.out / "raw-480p.mp4").exists())

    def test_clone_voice_clones_the_uploaded_sample_and_saves_the_id_beside_the_other_fields(self):
        del self.meta["voice_id"]
        (self.presenter / "presenter.json").write_text(json.dumps(self.meta))

        result = self.make("--clone-voice")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        url = self.uploads()[storage_key("voice-ref.mp3", b"voice sample")]
        self.assertEqual(self.posts(CLONE), [{"audio_url": url}])
        self.assertEqual([body["voice_setting"]["voice_id"] for body in self.posts(TTS)], ["voice-new"])
        self.assertEqual(json.loads((self.presenter / "presenter.json").read_text()),
                         {**self.meta, "voice_id": "voice-new"})

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg and ffprobe")
    def test_a_run_without_fal_key_stops_before_any_request_or_upload(self):
        result = self.run_script(fal_key=None)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAL_KEY", result.stderr)
        self.assertEqual(self.fake.requests, [])
        self.assertEqual(self.uploads(), {})

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg and ffprobe")
    def test_a_status_url_outside_the_fal_base_is_never_polled(self):
        other = FakeVendors(self, self.served)
        self.fake.poll_base = other.url

        result = self.run_script("--no-upscale")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(other.requests, [])
        self.assertEqual([r["path"] for r in self.fake.requests], [TTS])


    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg and ffprobe")
    def test_without_a_hook_files_go_to_fal_storage_with_the_fal_key_only(self):
        result = self.run_script("--no-upscale", hook=False)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        initiated = [r for r in self.fake.requests if r["path"] == "/falrest/storage/upload/initiate"]
        self.assertEqual([(r["auth"], r["body"]["file_name"]) for r in initiated],
                         [("Key " + FAL_KEY, "voice.mp3"), ("Key " + FAL_KEY, "presenter.jpg")])
        puts = [r for r in self.fake.requests if r["method"] == "PUT"]
        self.assertEqual([r["auth"] for r in puts], [None, None])
        [task] = self.posts(CREATE)
        stored = {name: (self.served / name).read_bytes() for name in os.listdir(self.served) if name.startswith("fal-")}
        image = task["input"]["reference_image_urls"][0]
        self.assertEqual(stored[image.rsplit("/", 1)[1]], b"still")
        audio = task["input"]["reference_audio_urls"][0]
        self.assertEqual(stored[audio.rsplit("/", 1)[1]], (self.out / "voice.mp3").read_bytes())
        self.assertEqual(self.uploads(), {})

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg and ffprobe")
    def test_a_short_download_is_refused_and_the_next_run_fetches_the_same_take(self):
        self.fake.short = {"/files/render.mp4"}

        first = self.run_script("--no-upscale")
        self.fake.short = set()
        second = self.run_script("--no-upscale")

        self.assertNotEqual(first.returncode, 0)
        self.assertIn("download", first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(len(self.posts(CREATE)), 1)
        self.assertEqual((self.out / "raw-480p.mp4").read_bytes(), (self.served / "render.mp4").read_bytes())
        self.assertEqual([p.name for p in self.out.glob("*.partial")], [])

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg and ffprobe")
    def test_clone_voice_on_a_folder_holding_another_script_stops_before_the_clone(self):
        self.assertEqual(self.run_script("--no-upscale").returncode, 0)

        result = self.make("--clone-voice", "--script", "A different ad for another brand.", "--slug", "test-ad",
                           "--no-fix")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--slug", result.stderr)
        self.assertEqual(self.posts(CLONE), [])
        self.assertEqual(json.loads((self.presenter / "presenter.json").read_text()), self.meta)

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg and ffprobe")
    def test_a_rerender_after_a_crash_before_cleanup_submits_a_new_take(self):
        self.assertEqual(self.run_script("--no-upscale").returncode, 0)
        (self.out / "seedance.task.json").write_text(json.dumps({"taskId": "task-1"}))

        result = self.run_script("--no-upscale", "--rerender")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.posts(CREATE)), 2)
        self.assertTrue((self.out / "raw-480p-take1.mp4").is_file())
        self.assertFalse((self.out / "seedance.task.json").exists())

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg and ffprobe")
    def test_a_new_voice_on_a_finished_folder_needs_a_rerender_before_any_spend(self):
        self.assertEqual(self.run_script("--no-upscale").returncode, 0)

        result = self.run_script("--no-upscale", "--clone-voice")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--rerender", result.stderr)
        self.assertEqual(self.posts(CLONE), [])
        self.assertEqual(len(self.posts(TTS)), 1)

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg and ffprobe")
    def test_a_new_voice_with_rerender_makes_the_mp3_again_with_that_voice(self):
        self.assertEqual(self.run_script("--no-upscale").returncode, 0)

        result = self.run_script("--no-upscale", "--clone-voice", "--rerender")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        voices = [body["voice_setting"]["voice_id"] for body in self.posts(TTS) if body["text"] == SPOKEN]
        self.assertEqual(voices, ["voice-abc", "voice-new"])
        self.assertEqual(len(self.posts(CREATE)), 2)
        self.assertTrue((self.out / "raw-480p-take1.mp4").is_file())


class StutterCutTests(unittest.TestCase):
    SCRIPT = "our app never shares your data"

    def fix(self, transcripts):
        sys.path.insert(0, str(SCRIPT.parent))
        self.addCleanup(sys.path.remove, str(SCRIPT.parent))
        import fix_words

        def heard(text):
            return [{"w": w, "raw": w, "s": i * 0.5, "e": i * 0.5 + 0.4} for i, w in enumerate(text.split())]

        last = {}

        def plain(path, tag, start=None, end=None):
            return (transcripts[last["cut"]] if "cuttest" in str(path) else "our app app never shares your data").split()

        def jump_cut(src, cuts, out):
            if cuts:
                last["cut"] = (cuts[-1]["cs"], cuts[-1]["ce"])

        fakes = {"words": lambda audio, tag, *a: heard(self.SCRIPT if tag == "voice" else
                                                         "our app app never shares your data"),
                 "plain": plain, "jump_cut": jump_cut, "run": lambda *cmd: "",
                 "search_cut": lambda *a: list(transcripts)}
        for name, fake in fakes.items():
            original = getattr(fix_words, name)
            setattr(fix_words, name, fake)
            self.addCleanup(setattr, fix_words, name, original)
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "script.txt"
            script.write_text(self.SCRIPT)
            return fix_words.fix("render.mp4", "voice.mp3", script, Path(tmp) / "out.mp4", log=lambda line: None)

    def test_a_cut_that_also_removes_a_script_word_is_passed_over_for_one_that_does_not(self):
        report = self.fix({(1.0, 1.4): "our app shares your data", (2.0, 2.4): self.SCRIPT})

        self.assertEqual(report["cuts"], [{"heard": "app", "removed": [2.0, 2.4]}])
        self.assertEqual(report["unfixed"], [])

    def test_a_stutter_with_only_word_losing_cuts_is_reported_not_fixed(self):
        report = self.fix({(1.0, 1.4): "our app shares your data"})

        self.assertEqual(report["cuts"], [])
        self.assertEqual([(u["kind"], u["heard"]) for u in report["unfixed"]], [("extra words", "app")])

if __name__ == "__main__":
    unittest.main()
