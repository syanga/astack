#!/usr/bin/env python3
import argparse, fcntl, hashlib, json, math, os, re, shlex, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(HERE))
from spoken import speakable
import resize_grain, vendors
from vendors import VendorError

PROMPT = SKILL / "prompts" / "seedance.txt"
MODEL = os.path.expanduser(os.environ.get("WHISPER_MODEL", "~/whisper-models/ggml-small.bin"))
FAL_BASE = os.environ.get("FAL_QUEUE_URL") or vendors.FAL_BASE
KIE_BASE = os.environ.get("KIE_API_URL") or vendors.KIE_BASE
FAL_REST = os.environ.get("FAL_REST_URL") or vendors.FAL_REST

TTS = "fal-ai/minimax/speech-02-hd"
CLONE = "fal-ai/minimax/voice-clone"
SEEDANCE = "bytedance/seedance-2-5"
TARGET, MAX_TEMPO = 29.0, 1.10
WORDS_PER_SEC = 3.6           # the clone at speed 1.0, measured on 6 scripts
SEEDANCE_USD_PER_SEC = 0.14
TTS_USD_PER_1K_CHARS = 0.10
CLONE_USD = 1.50
CLONE_LINE = "This is the new presenter voice."
WAIT_FOR_INSTALL = "Show these install commands to the user and wait. Do not add --no-fix to skip them."


def log(msg):
    print(msg, flush=True)


def sh(cmd, check=True):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd[:4]))} failed:\n{(r.stderr or r.stdout).strip()[-800:]}")
    return r


def dur(p):
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).stdout)


def install(mac, linux):
    return mac if sys.platform == "darwin" else linux


def missing_tools(need_whisper):
    ffmpeg = install("brew install ffmpeg", "sudo apt install ffmpeg")
    missing = [f"  {tool}: {ffmpeg}" for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
    if need_whisper:
        if not shutil.which("whisper-cli"):
            missing.append("  whisper-cli: " + install(
                "brew install whisper-cpp",
                "sudo apt install whisper.cpp on Ubuntu 26.04 or later; elsewhere, build it with the steps "
                "under Requirements in SKILL.md"))
        if not Path(MODEL).is_file():
            missing.append(f"  whisper model {MODEL}: mkdir -p ~/whisper-models && curl -L -o ~/whisper-models/ggml-small.bin "
                           "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin")
    return missing


def api_key(name):
    key = os.environ.get(name, "").strip()
    return key if key.isprintable() else ""


def hook_runs(cmd):
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return False
    return bool(argv and shutil.which(argv[0]))


def missing_setup(a, d):
    names = ["FAL_KEY"] + (["KIE_API_KEY"] if a.script else [])
    missing = [f"  {name}: not set, or not on one line" for name in names if not api_key(name)]
    if a.upload_cmd and not hook_runs(a.upload_cmd):
        missing.append("  upload hook: its program is not found or not executable")
    if a.clone_voice and not (d / "voice-ref.mp3").is_file():
        missing.append(f"  {d / 'voice-ref.mp3'}: 10 s or more of the voice to clone")
    return missing


def upload(cmd, path, name):
    if not cmd:
        try:
            return vendors.fal_upload(path, name, key=api_key("FAL_KEY"), rest=FAL_REST)
        except VendorError as e:
            sys.exit(f"Upload failed: {e}")
    key = f"ugc-video/{hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]}/{name}"
    r = subprocess.run(shlex.split(cmd) + [str(path), key], capture_output=True, text=True, errors="replace")
    lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    if r.returncode != 0 or not lines or not lines[-1].startswith(("http://", "https://")):
        sys.exit(f"The upload hook failed for {key} (exit {r.returncode}; its last output line must be a URL):\n"
                 f"{r.stderr.strip()[-800:]}")
    return lines[-1]


def archive(raw_v):
    n = 1
    while (raw_v.parent / f"raw-480p-take{n}.mp4").exists():
        n += 1
    raw_v.rename(raw_v.parent / f"raw-480p-take{n}.mp4")
    log(f"  kept the old take as raw-480p-take{n}.mp4")


LOCKS = []


def lock(folder):
    fd = os.open(folder / ".lock", os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(f"Another run is using {folder}. Let it finish.")
    LOCKS.append(fd)


def save(path, text):
    partial = path.with_name(path.name + ".partial")
    partial.write_text(text)
    partial.replace(path)


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def load_presenter(path, need_still):
    d = Path(path).expanduser().resolve()
    meta_path = d / "presenter.json"
    if not meta_path.is_file():
        sys.exit(f"No {meta_path}. A presenter folder holds presenter.json and presenter.jpg or presenter.png "
                 "(see Presenters in SKILL.md).")
    try:
        meta = json.loads(meta_path.read_text())
    except ValueError:
        meta = None
    if not isinstance(meta, dict):
        sys.exit(f"{meta_path} must be a JSON object with speaker and setting (see Presenters in SKILL.md).")
    image = next((p for p in sorted(d.glob("presenter.*")) if p.suffix.lower() in (".jpg", ".jpeg", ".png")), None)
    if need_still:
        if not image:
            sys.exit(f"{d} has no presenter.jpg or presenter.png, the still Seedance animates.")
        absent = [field for field in ("speaker", "setting") if not meta.get(field)]
        if absent:
            sys.exit(f"{meta_path} needs {' and '.join(absent)} for the prompt.")
    return d, meta, image


def voice_of(out):
    try:
        return json.loads((out / "tts.body.json").read_text())["voice_setting"]["voice_id"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def tts_body(text, voice_id):
    return {"text": text, "output_format": "url",
            "voice_setting": {"voice_id": voice_id, "speed": 1, "vol": 1, "pitch": 0}}


def clone_voice(a, d, meta):
    key = api_key("FAL_KEY")
    lock(d)
    saved = d / ".clone-request.json"
    url = None if saved.exists() else upload(a.upload_cmd, d / "voice-ref.mp3", "voice-ref.mp3")
    log(f"clone: {CLONE}, about ${CLONE_USD:.2f}" if url else f"clone: checking the request in {saved} again")
    try:
        res = vendors.fal_run(CLONE, {"audio_url": url}, key=key, base=FAL_BASE, timeout=600, saved=saved)
    except vendors.TakeFailed as e:
        saved.unlink(missing_ok=True)
        sys.exit(f"The voice clone failed: {e}")
    except VendorError as e:
        if not saved.exists() or "status_url" not in saved.read_text():
            sys.exit(f"The voice clone did not finish: {e}")
        sys.exit(f"The voice clone did not finish: {e}\nfal may still finish it. Run --clone-voice again to check that "
                 f"request; that does not pay for another clone. If fal no longer knows the request, delete {saved} "
                 "first; the next clone is paid for again.")
    vid = res.get("custom_voice_id")
    if not vid:
        sys.exit(f"fal's clone reply has no custom_voice_id (fields: {', '.join(sorted(res))})")
    meta["voice_id"] = vid
    save(d / "presenter.json", json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    saved.unlink(missing_ok=True)
    log(f"new voice id {vid} saved to {d / 'presenter.json'}")
    try:
        vendors.fal_run(TTS, tts_body(CLONE_LINE, vid), key=key, base=FAL_BASE, timeout=300)
    except VendorError as e:
        sys.exit(f"The voice id is saved but not yet used: the first TTS line failed ({e}).\n"
                 "fal deletes a clone that no TTS call uses within 7 days, so run a script with this presenter before then.")
    log("  read one TTS line with the new voice, so fal keeps the clone")


def main():
    ap = argparse.ArgumentParser(description="Script in, vertical presenter video ad out (fal + kie, local ffmpeg/whisper).")
    ap.add_argument("--script", help="path to a .txt file, or the script text itself")
    ap.add_argument("--presenter", required=True,
                    help="presenter folder: presenter.json, presenter.jpg or .png, and voice-ref.mp3 for --clone-voice")
    ap.add_argument("--caps", default="", help="comma list of brand names to write in caps in the Seedance dialogue")
    ap.add_argument("--slug", help="output folder name")
    ap.add_argument("--out-dir", default="ugc-videos", help="parent folder for output (default ./ugc-videos)")
    ap.add_argument("--upload-cmd", default=os.environ.get("UGC_UPLOAD_CMD", ""),
                    help="command run as <cmd> <path> <key> that prints a public URL last (default $UGC_UPLOAD_CMD; "
                         "without one, files go to fal storage)")
    ap.add_argument("--dry-run", action="store_true", help="length, cost and prompt only; no upload, no spend")
    ap.add_argument("--rerender", action="store_true", help="new Seedance take with the same MP3 (only after the person says yes)")
    ap.add_argument("--no-fix", action="store_true", help="skip the whisper check and fixes; keep the raw take")
    ap.add_argument("--no-upscale", action="store_true", help="stop at 480p")
    ap.add_argument("--clone-voice", action="store_true", help="make a new voice id from the presenter's voice-ref.mp3")
    a = ap.parse_args()
    if not a.script and not a.clone_voice:
        ap.error("--script is required, except with --clone-voice alone")

    d, meta, image = load_presenter(a.presenter, need_still=bool(a.script))
    if not a.script:
        missing = missing_setup(a, d)
        if a.dry_run:
            log(f"clone: {d / 'voice-ref.mp3'} with {CLONE}, about ${CLONE_USD:.2f}")
            if missing:
                log("Missing before a real run:\n" + "\n".join(missing))
            return
        if missing:
            sys.exit("Missing before a real run:\n" + "\n".join(missing))
        return clone_voice(a, d, meta)

    original = Path(a.script).read_text().strip() if os.path.isfile(a.script) else a.script.strip()
    no_quotes = re.sub(r'["“”]', "", original)  # quotes confuse the Seedance Dialogue line
    if no_quotes != original:
        log("removed quotation marks from the script")
    spoken = " ".join(speakable(no_quotes).split())
    words = len(spoken.split())
    est_read = words / WORDS_PER_SEC
    est_secs = min(30, max(4, math.ceil(min(est_read, TARGET))))
    est_usd = est_secs * SEEDANCE_USD_PER_SEC + len(spoken) / 1000 * TTS_USD_PER_1K_CHARS
    log(f"1 script: {words} words spoken, read ~{est_read:.1f}s -> ~{est_secs}s video, est ${est_usd:.2f}")
    if est_read > TARGET * MAX_TEMPO:
        sys.exit(f"Too long: ~{est_read:.0f}s of speech fits 29 s only above {MAX_TEMPO}x. "
                 f"Cut about {math.ceil(words - TARGET * MAX_TEMPO * WORDS_PER_SEC)} words.")
    if words > 110:
        log("  note: over 110 words; the read may need a speed-up close to the 1.10x limit")

    dialogue = spoken
    for w in [x.strip() for x in a.caps.split(",") if x.strip()]:
        dialogue = re.sub(rf"\b{re.escape(w)}\b", w.upper(), dialogue)
    slug = a.slug or slugify(" ".join(spoken.split()[:6]))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", slug):
        ap.error("the slug may hold only letters, digits, dots, dashes, and underscores; pass --slug")
    if re.fullmatch(r"(raw|fixed)-480p(-take\d+)?", slug, re.IGNORECASE):
        ap.error(f"the slug {slug} names a working file; pass another --slug")
    out = Path(a.out_dir).expanduser().resolve() / slug
    template = PROMPT.read_text()

    def prompt(seconds):
        return template.format(speaker=meta["speaker"], setting=meta["setting"], dialogue=dialogue, duration=seconds)

    if a.dry_run:
        log(f"presenter: {d}")
        if a.clone_voice:
            log(f"voice: a new clone from {d / 'voice-ref.mp3'} (about ${CLONE_USD:.2f})")
        elif meta.get("voice_id"):
            log(f"voice: {meta['voice_id']}")
        else:
            log(f"voice: none yet: run --clone-voice first (about ${CLONE_USD:.2f})")
        log(f"output: {out / (slug + '.mp4')}")
        missing = missing_tools(True)
        if missing:
            log("Install these before a real run:\n" + "\n".join(missing))
            log(WAIT_FOR_INSTALL)
        setup = missing_setup(a, d)
        if setup:
            log("Missing before a real run:\n" + "\n".join(setup))
        log("prompt:\n" + prompt(est_secs))
        return

    required = missing_tools(not a.no_fix)
    if required:
        sys.exit("Install these before a real run:\n" + "\n".join(required) + "\n" + WAIT_FOR_INSTALL)
    if a.no_fix:
        skipped = [line for line in missing_tools(True) if line not in required]
        if skipped:
            log("Whisper is not installed, so --no-fix skips the word check:\n" + "\n".join(skipped))
    missing = missing_setup(a, d)
    if not a.clone_voice and not meta.get("voice_id"):
        missing.append(f"  voice_id in {d / 'presenter.json'}: run --clone-voice first (about ${CLONE_USD:.2f}), "
                       "only after the user says yes")
    if missing:
        sys.exit("Missing before a real run:\n" + "\n".join(missing))
    run = {"spoken": spoken, "presenter": str(d)}
    record = out / "run.json"
    out.mkdir(parents=True, exist_ok=True)
    lock(out)
    if any(p.name != ".lock" and not p.name.endswith(".partial") for p in out.iterdir()):
        try:
            earlier = json.loads(record.read_text())
        except (OSError, ValueError):
            earlier = None
        if earlier != run:
            sys.exit(f"{out} holds a run for another script or presenter. Pass a new --slug.")
    voice, raw_v, pending = out / "voice.mp3", out / "raw-480p.mp4", out / "seedance.task.json"
    again = "Run the command again without --rerender or --clone-voice"
    voice_changed = voice.exists() and voice_of(out) != meta.get("voice_id")
    if raw_v.exists() and pending.exists() and (a.clone_voice or (a.rerender and not voice_changed)):
        sys.exit(f"The last run downloaded its take but stopped before finishing. {again} to finish it.")
    if pending.exists() and (a.rerender or a.clone_voice):
        sys.exit(f"A kie take is still pending. {again} to check it. To give it up, delete {pending} first; "
                 "the next take is paid for again.")
    new_take = (f"{out} has a take made with the old voice, so a new voice needs a new take. Pass --rerender "
                f"(about ${est_secs * SEEDANCE_USD_PER_SEC:.2f}) only after the user says yes.")
    if a.clone_voice and raw_v.exists() and not a.rerender:
        sys.exit(new_take)
    if a.clone_voice:
        clone_voice(a, d, meta)
    fal_key, kie_key = api_key("FAL_KEY"), api_key("KIE_API_KEY")
    out.mkdir(parents=True, exist_ok=True)
    save(record, json.dumps(run, indent=1, ensure_ascii=False) + "\n")
    (out / "script.txt").write_text(original + "\n")
    (out / "script-spoken.txt").write_text(spoken + "\n")

    if voice.exists() and voice_of(out) != meta["voice_id"]:
        if pending.exists() and not raw_v.exists():
            sys.exit(f"A take made with voice {voice_of(out)} is still pending, but {d / 'presenter.json'} now names "
                     f"{meta['voice_id']}. Put the old voice_id back and {again[0].lower() + again[1:]} to collect it, "
                     f"or delete {pending} to give it up.")
        if raw_v.exists() and not a.rerender:
            sys.exit(new_take)
        if raw_v.exists():
            archive(raw_v)
            pending.unlink(missing_ok=True)
        for name in ("voice.mp3", "voice-raw.mp3", "voice-trim.wav"):
            (out / name).unlink(missing_ok=True)
        log("2 MP3: the voice id changed, so the MP3 is made again")
    if not voice.exists():
        body = tts_body(spoken, meta["voice_id"])
        (out / "tts.body.json").write_text(json.dumps(body, indent=1))
        try:
            res = vendors.fal_run(TTS, body, key=fal_key, base=FAL_BASE, timeout=300)
        except VendorError as e:
            sys.exit(f"TTS failed: {e}\nIf fal rejected the voice id, the clone may have expired: fal deletes a clone that no "
                     f"TTS call uses within 7 days. --clone-voice makes a new one (about ${CLONE_USD:.2f}); run it only after "
                     "the user says yes.")
        (out / "tts.result.json").write_text(json.dumps(res, indent=1))
        url = (res.get("audio") or {}).get("url")
        if not url:
            sys.exit(f"fal's TTS reply has no audio url. See {out / 'tts.result.json'}")
        try:
            vendors.fetch(url, out / "voice-raw.mp3")
        except VendorError as e:
            sys.exit(f"Could not download the MP3: {e}")
        raw = dur(out / "voice-raw.mp3")
        sh(["ffmpeg", "-y", "-i", out / "voice-raw.mp3", "-af",
            "silenceremove=start_periods=1:start_threshold=-45dB:stop_periods=-1:stop_duration=0.3:"
            "stop_threshold=-45dB:stop_silence=0.25", "-ar", "44100", out / "voice-trim.wav"])
        trim = dur(out / "voice-trim.wav")
        tempo = max(1.0, trim / TARGET)
        if tempo > MAX_TEMPO:
            sys.exit(f"The read is {trim:.1f}s; fitting 29 s needs {tempo:.3f}x, over the {MAX_TEMPO}x limit. "
                     f"Cut about {math.ceil((trim - TARGET * MAX_TEMPO) * WORDS_PER_SEC)} words and run again.")
        sh(["ffmpeg", "-y", "-i", out / "voice-trim.wav", "-af", f"atempo={tempo:.4f}", "-c:a", "libmp3lame",
            "-b:a", "192k", out / "voice.partial.mp3"])
        (out / "voice.partial.mp3").replace(voice)
        log(f"2 MP3: {raw:.2f}s -> pause trim {trim:.2f}s -> atempo {tempo:.3f} -> {dur(voice):.2f}s")
    else:
        log(f"2 MP3: reusing {voice.name} ({dur(voice):.2f}s)")
    if dur(voice) < 2:
        sys.exit("The MP3 is under 2 s, and kie needs 2 s or more of reference audio. Lengthen the script.")
    seconds = min(30, max(4, math.ceil(dur(voice))))
    take_usd = seconds * SEEDANCE_USD_PER_SEC

    if raw_v.exists() and a.rerender:
        archive(raw_v)
    if not raw_v.exists():
        if pending.exists():
            try:
                task = json.loads(pending.read_text()).get("taskId")
            except ValueError:
                task = None
            if not task:
                sys.exit("An earlier submit to kie got no clear answer, so kie may have accepted a paid take. Check "
                         f"the kie dashboard. To submit again, delete {pending} first; that pays for a new take.")
            log(f"4 Seedance: checking kie task {task} again, not resubmitted")
        else:
            for path, limit in ((voice, 15), (image, 30)):
                if path.stat().st_size > limit * 1024 * 1024:
                    sys.exit(f"{path} is over kie's {limit} MB limit for a reference file.")
            au = upload(a.upload_cmd, voice, "voice.mp3")
            im = upload(a.upload_cmd, image, f"presenter{image.suffix.lower()}")
            body = {"prompt": prompt(seconds), "reference_image_urls": [im], "reference_audio_urls": [au],
                    "resolution": "480p", "aspect_ratio": "9:16", "duration": seconds, "generate_audio": True,
                    "output_format": "mp4"}
            (out / "seedance.body.json").write_text(json.dumps(body, indent=1))
            log(f"4 Seedance: {seconds}s at 480p, about ${take_usd:.2f}, usually 5-10 min")
            try:
                with open(pending, "x") as marker:
                    marker.write(json.dumps({"taskId": None}) + "\n")
            except FileExistsError:
                sys.exit(f"Another run is submitting this take ({pending} appeared). Let it finish.")
            try:
                task = vendors.kie_submit(SEEDANCE, body, key=kie_key, base=KIE_BASE)
            except vendors.Refused as e:
                pending.unlink()
                sys.exit(f"Seedance submit failed: {e}")
            except VendorError as e:
                sys.exit(f"Seedance submit failed: {e}\nkie may have accepted it. Check the kie dashboard before "
                         f"you delete {pending} and submit again.")
            save(pending, json.dumps({"taskId": task}) + "\n")
        try:
            rec = vendors.kie_wait(task, key=kie_key, base=KIE_BASE, timeout=1800)
        except vendors.TakeFailed as e:
            pending.unlink()
            sys.exit(f"Seedance failed: {e}\nNot resubmitted. A new take costs about ${take_usd:.2f}. Ask the person first; "
                     f"if they say yes, {again[0].lower() + again[1:]}. It reuses voice.mp3.")
        except VendorError as e:
            sys.exit(f"{e}\nkie may still finish task {task}. {again} to check it; that does not pay for a new take.")
        (out / "seedance.result.json").write_text(json.dumps(rec, indent=1))
        if rec.get("creditsConsumed") is not None:
            log(f"  kie charged {rec['creditsConsumed']} credits")
        urls = (rec.get("result") or {}).get("resultUrls") or []
        if not urls:
            sys.exit(f"kie finished the take with no resultUrls. See {out / 'seedance.result.json'}")
        try:
            vendors.fetch(urls[0], raw_v, check=dur)
            length = dur(raw_v)
        except (VendorError, RuntimeError, ValueError) as e:
            raw_v.unlink(missing_ok=True)
            sys.exit(f"The take is done and paid for, but the download failed: {e}\n"
                     f"{again} to download it without paying again.")
        log(f"4 Seedance: done, {length:.2f}s")
    else:
        log("4 Seedance: reusing raw-480p.mp4 (use --rerender for a new take, only after the person says yes)")

    fixed = out / "fixed-480p.mp4"
    report = {"splices": [], "cuts": [], "unfixed": []}
    if a.no_fix:
        shutil.copy(raw_v, fixed)
        log("5-6 check/fix: skipped (--no-fix)")
    else:
        import fix_words
        log("5-6 check/fix:")
        try:
            report = fix_words.fix(raw_v, voice, out / "script-spoken.txt", fixed, log=lambda m: log("  " + m))
        except (RuntimeError, OSError, ValueError) as e:
            shutil.copy(raw_v, fixed)
            report = {"splices": [], "cuts": [], "unfixed": [
                {"kind": "check", "heard": "", "at": 0.0, "why": f"the fixer stopped, so nothing was checked: {e}"}]}
        (out / "check.json").write_text(json.dumps(report, indent=1))

    final = fixed
    if not a.no_upscale:
        final = out / f"{slug}.mp4"
        resize_grain.upscale(fixed, final)
    pending.unlink(missing_ok=True)
    log(f"\ndone -> {final} ({dur(final):.1f}s)")
    for s in report["splices"]:
        log(f"  fixed: '{s['heard']}' -> '{s['script']}' at {s['at']:.1f}s (spliced from the MP3)")
    for c in report["cuts"]:
        log(f"  cut: stutter '{c['heard']}' {c['removed'][0]:.2f}-{c['removed'][1]:.2f}s (jump cut)")
    if report["unfixed"]:
        log("NOT FIXED (listen to these spots):")
        for u in report["unfixed"]:
            log(f"  {u['at']:.1f}s  {u['kind']}: heard '{u['heard']}'" + (f", script '{u['script']}'" if u.get("script") else "")
                + f": {u['why']}")
        log(f"A new Seedance take costs about ${take_usd:.2f}. Ask the person first; "
            f"if they say yes: make_ugc.py --script ... --presenter ... --slug {slug} --rerender")


if __name__ == "__main__":
    main()
