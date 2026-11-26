---
name: ugc-video
description: Turn a script into a vertical presenter video ad with fal MiniMax TTS and kie Seedance 2.5, checked and fixed locally with whisper.cpp and ffmpeg.
argument-hint: "<script text or .txt> --presenter <dir> [--caps Brand1,Brand2]"
disable-model-invocation: true
---

# ugc-video

Script in, finished vertical UGC-style presenter ad out (1080x1920 MP4, no captions). The paid steps call fal and kie directly. Everything else runs on the user's machine (ffmpeg, whisper.cpp).

```bash
python3 <skill-dir>/scripts/make_ugc.py --script script.txt --presenter <dir> --dry-run   # length, cost, prompt; no spend
python3 <skill-dir>/scripts/make_ugc.py --script script.txt --presenter <dir>             # full run, 5 to 10 min
```

`<skill-dir>` is the folder this SKILL.md is in. `<dir>` is a presenter folder in the brand's directory, described in [Presenters](#presenters). Output: `./ugc-videos/<slug>/<slug>.mp4`.

A real run needs `FAL_KEY` and `KIE_API_KEY`. Never print the keys. Input files go to fal storage unless an [upload hook](#uploads) is set.

## Workflow

1. **Write the script as the brand's presenter.** The presenter explains or demonstrates the product and speaks for the brand, to the viewer as "you". The presenter never claims to be a customer, describes results of their own, or reads a review as their own. The FTC treats those as fake testimonials. State only claims the brand can back, from its claims file when it has one.
2. **Write the script clean.** Periods, commas, question marks and apostrophes only. **No quotation marks** (the script removes any it finds). Numbers can be digits. They are spelled out for speech (`24/7` becomes twenty-four seven, `2am` becomes two a.m., `9:45 p.m.` becomes nine forty-five p.m.). Write `twelve hundred dollars` if that is how it should be said. `$1,200` is read "one thousand two hundred dollars".
3. **Dry run first, always.** Tell the user the word count, the expected length and the cost. Over about 110 words is tight. Over about 115 does not fit 30 s, so show them what to cut. If `python3` is missing, show `brew install python` on macOS or `sudo apt install python3` on Linux, and wait. The dry run cannot start without it. If ffmpeg or whisper is missing, the dry run lists those install commands first. Show those to the user and wait. Do not add `--no-fix` yourself. That flag is only after the user says to skip the word check. The dry run also names any missing key, and an upload hook it cannot run.
4. **Run for real in the background** (5 to 10 min).
5. **Read the end of the output.** It lists every fix (`fixed:` or `cut:`) and anything **NOT FIXED** with its time in the video. Tell the user exactly where to listen.
6. **Never re-render on your own.** If something is not fixed, tell the user the spot, the reason, and the cost of a new take (about $0.14 per second of video). Run `--rerender` **only if they say yes**. It keeps the old take and reuses the same MP3, unless the presenter's voice id changed.
7. Hand over `ugc-videos/<slug>/<slug>.mp4` and `check.json`. The caller stores and records the video. This skill keeps no record of it.

## What the script does

| # | Step | Where | Cost |
|---|---|---|---|
| 0 | Preflight: `ffmpeg`, `whisper-cli`, the whisper model, `FAL_KEY`, `KIE_API_KEY`, the upload hook when one is set. Prints install commands for anything missing | local | free |
| 1 | Quotes removed, numbers spelled out, word count, length and cost estimate | local | free |
| 2 | MP3: `fal-ai/minimax/speech-02-hd` with the presenter's cloned voice, speed 1.0 | fal | about $0.10 per 1,000 characters |
| 3 | Fit: pauses over 0.3 s trimmed to 0.25 s, then `atempo` to 29.0 s or less (limit 1.10x). Over the limit it stops and says how many words to cut | local ffmpeg | free |
| 4 | Seedance: `bytedance/seedance-2-5`, 480p, 9:16, `[Image1]` = presenter still, `[Audio1]` = the MP3, `generate_audio: true`, duration = MP3 length rounded up. The still and the MP3 go to fal storage, or the upload hook, for public URLs that kie fetches. Prompt: `prompts/seedance.txt` | kie | about $0.14 per second |
| 5 | Check: local whisper on the render and on our MP3, compared with the script | local whisper | free |
| 6 | Fix: wrong word, so the same word is spliced from the MP3 (video untouched). Stutter or extra words, so they are cut from video and audio as a jump cut. Each fix is tried at several timings and kept only if a whole-file transcript then reads right. A cut that also removes a script word is never kept. When every cut would remove one, the spot is reported as NOT FIXED | local | free |
| 7 | Upscale: `resize_grain.py` (Lanczos, light sharpen, phone grain) to 1080x1920 | local ffmpeg | free |

Steps are resumable. An existing `voice.mp3` or `raw-480p.mp4` in the output folder is reused. A new voice id makes a new MP3, and a finished take then needs `--rerender`. While a take is pending, a new voice id stops the run. A folder that holds a run for another script or presenter stops the run, so pass a new `--slug`. A run locks its output folder with a `.lock` file, so a second run on that folder stops. `--clone-voice` also locks the presenter folder, so a second clone of that presenter stops. `seedance.task.json` is created before the submit and holds the kie task id. If polling times out or the process stops, rerun the command without `--rerender` or `--clone-voice`. That run checks the same task and pays for no new take. If the submit itself got no clear answer, the next run stops and asks you to check the kie dashboard first. To give up on that task, delete `seedance.task.json`. The next run then pays for a new take, so ask the user first.

The script retries a submit that fal answers with HTTP 429 or kie with code 429 or 455, because a refused submit costs nothing. It never resubmits after a 5xx, a dropped connection, a failed take, or a timeout, because the vendor may have accepted the job. It prints the vendor's error and stops.

## Rules (learned from test runs, Oct 2026)

- **Seedance never plays back the MP3.** It makes its own read, guided by the MP3's voice and timing, and the lips match its own read. That is why the final audio is Seedance's, patched, and not the MP3 laid on top.
- **No pronunciation hints in the prompt.** A spelled hint ("S-L-ack") was read aloud as a stutter, "SL Slack". Hints did not reduce mistakes either. The prompt only says to say every word as written.
- **No quotation marks in the script.** The dialogue is already inside quotes in the prompt.
- `--caps Brand1,Brand2` writes those names in caps in the Seedance dialogue only (the MP3 text stays normal). Optional. It was used in the clean test runs.
- **Whisper spellings are not errors.** Whisper writes "Klaviyo" as "Clavio" and "focaccia" as "facaccia" in our own MP3 too. The check only flags a word when the render is heard differently from the MP3.
- **Cut times come from short clips, never the full file.** whisper.cpp word times drift up to about 0.5 s over a long file, by a different amount in each track. The fixer times every cut on clips of about 3.5 s and tries several.
- **Run locally.** ffmpeg and whisper run on the user's machine. If either is missing, the dry run and a real run print the install command. Show that to the user and let them install it. `--no-fix` skips the word check only after they ask.

## Presenters

A presenter is a synthetic person made for one brand. Each one is a folder in the brand's directory, passed with `--presenter`:

| File | Purpose |
|---|---|
| `presenter.jpg` or `presenter.png` | The still, Seedance `[Image1]` |
| `presenter.json` | `speaker`: who is in the still, for the prompt. `setting`: where they film. `voice_id`: the fal MiniMax clone, written by `--clone-voice` |
| `voice-ref.mp3` | 10 s or more of the voice to clone |

```json
{
  "speaker": "a woman in her 30s, short dark hair, navy cardigan",
  "setting": "a quiet home office with a bookshelf"
}
```

**Voice clone.** `make_ugc.py --clone-voice --presenter <dir>` uploads `voice-ref.mp3`, clones it with `fal-ai/minimax/voice-clone` (about $1.50), reads one short line with the new voice, and saves `voice_id` in `presenter.json`. fal deletes a clone that no TTS call uses within 7 days of cloning, so the short line keeps it. The clone request is kept in `.clone-request.json` in the presenter folder until the voice id is saved. If polling fails, rerun `--clone-voice` to check that request without paying again. If the submit got no clear answer, the rerun stops and points to the fal dashboard instead. A failed or refused clone clears the file. Run it for a new presenter, or when the TTS step fails on the voice id. Run it only after the user says yes, then commit `presenter.json` in the brand's repository.

## Uploads

kie and fal fetch the MP3, the still, and the voice sample from URLs. By default `make_ugc.py` uploads each file to fal storage with `FAL_KEY` (`rest.fal.ai/storage/upload/initiate`, then a PUT of the bytes). Anyone with a fal storage URL can fetch the file, and fal keeps it for the account's retention period.

To keep the files in your own storage, set an upload hook. The upload hook is a command that uploads one file and prints a URL that anyone can fetch for at least an hour. `make_ugc.py` runs it with two arguments, the local path and a storage key such as `ugc-video/<hash>/voice.mp3`, and reads the URL from the last line of its output. `<hash>` is the start of the file's SHA-256, so two runs never share a key for different files. Set it with `--upload-cmd` or `UGC_UPLOAD_CMD`. A hook for S3:

```sh
#!/bin/sh
set -e
aws s3 cp "$1" "s3://<bucket>/$2" --quiet
aws s3 presign "s3://<bucket>/$2" --expires-in 3600
```

## Flags

| Flag | Default | Notes |
|---|---|---|
| `--script` | required | A .txt path or the text itself |
| `--presenter` | required | Presenter folder, see [Presenters](#presenters) |
| `--dry-run` | off | Length, cost and the full prompt. No upload, no network call, no spend |
| `--caps` | none | Brand names to write in caps in the Seedance dialogue |
| `--slug` / `--out-dir` | first words / `./ugc-videos` | |
| `--upload-cmd` | `UGC_UPLOAD_CMD`, else fal storage | See [Uploads](#uploads) |
| `--rerender` | off | New Seedance take, with the same MP3 unless the voice id changed. **Only after the user says yes** |
| `--no-fix` | off | Skip the whisper check. Only after the user asks |
| `--no-upscale` | off | Stop at 480p |
| `--clone-voice` | off | New voice id from the presenter's `voice-ref.mp3` |

## Output (`ugc-videos/<slug>/`)

`<slug>.mp4` (final, 1080x1920), `raw-480p.mp4` (Seedance take), `fixed-480p.mp4`, `voice.mp3` (the fitted MP3), `script.txt`, `script-spoken.txt`, `check.json` (splices, cuts, and anything not fixed), `run.json` (the script and presenter this folder belongs to), `seedance.task.json` (the kie task id while a take is unfinished), `tts.body.json`, `tts.result.json`, `seedance.body.json`, and `seedance.result.json` (kie's task record, with the credits it charged). A re-render keeps the old take as `raw-480p-take<n>.mp4`.

## Requirements

| Need | macOS | Linux |
|---|---|---|
| Python 3.10 or later, no extra packages | `brew install python` | `sudo apt install python3` |
| ffmpeg and ffprobe | `brew install ffmpeg` | `sudo apt install ffmpeg` |
| whisper.cpp (`whisper-cli`) | `brew install whisper-cpp` | `sudo apt install whisper.cpp` on Ubuntu 26.04 or later. Elsewhere, build it as below |
| whisper small model | the command below | the command below |
| fal and kie API keys | `FAL_KEY`, `KIE_API_KEY` | `FAL_KEY`, `KIE_API_KEY` |

The whisper small model (466 MB). Set `WHISPER_MODEL` to use another path:

```sh
mkdir -p ~/whisper-models && curl -L -o ~/whisper-models/ggml-small.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin
```

To build `whisper-cli` on a Linux without the package, install a C++ compiler, CMake, and Git, then:

```sh
git clone --depth 1 --branch v1.8.3 https://github.com/ggml-org/whisper.cpp
cmake -S whisper.cpp -B whisper.cpp/build -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build whisper.cpp/build -j --target whisper-cli
sudo install whisper.cpp/build/bin/whisper-cli /usr/local/bin/
```

## Known limits

- Rare brand names are the weak spot (Seedance said "Calendly" wrong 3 times out of 3 in tests). The fixer usually splices them. Check `check.json` and listen.
- About 1 take in 4 needs a fix. A fix that does not read right is not used. That spot is reported instead.
- A jump cut removes up to about 1 s of video. Listen to the join.
- Cost estimate: kie lists Seedance 2.5 at 480p without a reference video at 28 credits per second ($0.14), about $4.20 for 30 s (kie's price page, Nov 2026). Not yet checked against billed runs. `seedance.result.json` records the credits kie charged.
- Planned: WhisperX (forced alignment) for exact word times.
