---
name: tesseract-motion
description: Add optional motion graphics, such as a lower third, to a video ad with Mirage's Tesseract CLI, installed from Mirage's 0.3.1 release with telemetry off.
disable-model-invocation: true
---

# Add motion graphics with Tesseract

Tesseract is Mirage's local motion-graphics tool. Its `tsrct` CLI edits and renders `.tsrct` projects, and Mirage's own `tesseract-motion` skill teaches the authoring. This skill installs both from Mirage's release, keeps telemetry off, and sets up rendering on Linux.

Motion graphics are optional. If the business is not eligible, Tesseract cannot be installed, or a render fails, deliver the video ad without them and report the step that stopped.

## Confirm eligibility

Downloading the CLI or Mirage's skills accepts [Mirage's terms](https://github.com/mirage-hq/Tesseract/blob/v0.3.1/TERMS.md) for the business. Under Section 2.2.3, a for-profit business whose group revenue over the last 12 months is US$1M or more needs a written agreement with Mirage for commercial use, which includes marketing, testing, and evaluation. A group that reaches US$1M must stop using Tesseract until it has that agreement. Before the first download, ask the user to confirm that the business is eligible, unless they already have. Mirage's terms forbid copying its skills or CLI, so keep them out of any repository.

## Install the pinned release

The pin is 0.3.1, because Mirage's skills from a release expect the CLI from the same release. These steps are for Linux on `x86_64` with glibc 2.35 or newer. Check with `uname -m` and `getconf GNU_LIBC_VERSION`. On macOS or Windows, download `tesseract.zip` the same way and follow `references/installation.md` in its `tesseract-motion` skill.

1. Download both archives into a new empty directory and verify their checksums. Stop if a check fails.

   ```sh
   v=0.3.1
   base=https://github.com/mirage-hq/Tesseract/releases/download/v$v
   cd "$(mktemp -d)"
   curl -fL --remote-name-all "$base/tesseract.zip" "$base/tesseract.zip.sha256" \
     "$base/tesseract-$v-linux-x86_64.zip" "$base/tesseract-$v-linux-x86_64.zip.sha256"
   sha256sum -c tesseract.zip.sha256 "tesseract-$v-linux-x86_64.zip.sha256"
   ```

2. Install the CLI and Mirage's skills under Mirage's data folder. The installer needs no root.

   ```sh
   data=${XDG_DATA_HOME:-$HOME/.local/share}/Tesseract
   unzip -q "tesseract-$v-linux-x86_64.zip"
   bash "tesseract-$v-linux-x86_64/install.sh"
   mkdir -p "$data/skills-$v" && unzip -qo tesseract.zip -d "$data/skills-$v"
   tsrct=$data/bin/tsrct
   "$tsrct" --version
   ```

   Mirage's motion skill is now at `$data/skills-0.3.1/tesseract/skills/tesseract-motion/SKILL.md`. Skip `npx skills add mirage-hq/Tesseract`. It installs Mirage's latest skills, which expect a newer CLI, into the agent's skill folder, where its `tesseract-motion` replaces this skill.

## Turn telemetry off

Telemetry is on by default in Mirage's releases. Turn it off right after the install, before any other `tsrct` command:

```sh
"$tsrct" telemetry disable
"$tsrct" telemetry status
```

Continue only when the status prints `"enabled":false`. The setting persists in `${XDG_CONFIG_HOME:-$HOME/.config}/tesseract/telemetry/settings.json`, so check the status once per session. Leave the `TESSERACT_*` attribution variables that Mirage's skill describes unset.

## Set up rendering on Linux

The CLI renders through Vulkan. Its Linux bundle has no H.264 encoder.

1. Install Mesa's Vulkan drivers, the Vulkan loader, and an ffmpeg built with libx264. On Debian or Ubuntu, that is `sudo apt install mesa-vulkan-drivers libvulkan1 ffmpeg`. Ask the user before you run `sudo`.
2. Render on the CPU with Mesa's lavapipe driver, which works on hosts without a GPU. Set it in each shell that runs `tsrct`:

   ```sh
   export VK_ICD_FILENAMES=$(ls /usr/share/vulkan/icd.d/lvp_icd*.json | head -n 1)
   ```

   To use a working GPU driver instead, leave `VK_ICD_FILENAMES` unset.
3. Export every MP4 through the system ffmpeg. Without these flags, `tsrct export` fails with `No usable H.264 encoder`:

   ```sh
   "$tsrct" export --project project.tsrct --output ad.mp4 \
     --encoder-backend external-ffmpeg-command --ffmpeg-path "$(command -v ffmpeg)"
   ```

## Author the graphic

Read Mirage's `tesseract-motion/SKILL.md` at the path above and follow it, with `$tsrct` as the executable. Keep telemetry off and the Linux export flags from this skill.

Deliver the `.tsrct` project and the MP4. Before you hand them back, check the MP4 with `ffprobe -v error -show_entries stream=codec_name,width,height:format=duration -of compact ad.mp4`. Expect `h264`, the canvas size, and the planned duration.
