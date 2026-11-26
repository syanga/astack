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

These steps are for Linux on `x86_64` with glibc 2.35 or newer. Check with `uname -m` and `getconf GNU_LIBC_VERSION`. On other hosts, download `tesseract.zip` and `tesseract.zip.sha256` from the release below into a new empty directory and verify the checksum. Then follow `tesseract/skills/tesseract-motion/references/installation.md` from the extracted archive instead of this section. Use that `tesseract-motion` skill and the executable path its guide names in place of the paths below.

The pin is 0.3.1, because Mirage's skills from a release expect the CLI from the same release. Shell variables do not carry between shell calls, so each block below sets the variables it uses.

1. Run this block in one shell call. It downloads each archive into its own new empty directory, verifies both checksums, and installs the CLI and Mirage's skills under Mirage's data folder. It stops at the first failed command, including a failed checksum, and deletes its download directories when it exits. The installer needs no root.

   ```sh
   (
     set -eu
     v=0.3.1
     base=https://github.com/mirage-hq/Tesseract/releases/download/v$v
     data=${XDG_DATA_HOME:-$HOME/.local/share}/Tesseract
     skills_dl=$(mktemp -d)
     cli_dl=$(mktemp -d)
     cli_x=$(mktemp -d)
     trap 'rm -rf "$skills_dl" "$cli_dl" "$cli_x"' EXIT
     cd "$skills_dl"
     curl -fL --remote-name-all "$base/tesseract.zip" "$base/tesseract.zip.sha256"
     sha256sum -c tesseract.zip.sha256
     cd "$cli_dl"
     curl -fL --remote-name-all "$base/tesseract-$v-linux-x86_64.zip" "$base/tesseract-$v-linux-x86_64.zip.sha256"
     sha256sum -c "tesseract-$v-linux-x86_64.zip.sha256"
     unzip -q "tesseract-$v-linux-x86_64.zip" -d "$cli_x"
     bash "$cli_x/tesseract-$v-linux-x86_64/install.sh"
     mkdir -p "$data/skills-$v"
     unzip -qo "$skills_dl/tesseract.zip" -d "$data/skills-$v"
   )
   ```

2. Check that `"${XDG_DATA_HOME:-$HOME/.local/share}/Tesseract/bin/tsrct" --version` prints `tsrct 0.3.1`. Stop if it prints another version.

Mirage's motion skill is now at `${XDG_DATA_HOME:-$HOME/.local/share}/Tesseract/skills-0.3.1/tesseract/skills/tesseract-motion/SKILL.md`. Skip `npx skills add mirage-hq/Tesseract`. That command installs Mirage's latest skills, which expect a newer CLI, into the agent's skill folder, where Mirage's `tesseract-motion` replaces this skill.

## Turn telemetry off

Telemetry is on by default in Mirage's releases. The CLI sends it only from `project create`, `preview`, `filmstrip`, and `export`. Turn it off right after the install, before any of those commands:

```sh
tsrct=${XDG_DATA_HOME:-$HOME/.local/share}/Tesseract/bin/tsrct
"$tsrct" telemetry disable
"$tsrct" telemetry status
```

Continue only when the status prints `"enabled":false`. The setting persists in `${XDG_CONFIG_HOME:-$HOME/.config}/tesseract/telemetry/settings.json`, so check the status once per session. Leave the `TESSERACT_*` attribution variables that Mirage's skill describes unset.

## Set up rendering on Linux

The CLI renders through Vulkan. Its Linux bundle has no H.264 encoder.

1. Install Mesa's Vulkan drivers, the Vulkan loader, and an ffmpeg built with libx264. Ask the user before you run `sudo`. On Debian or Ubuntu, the command is `sudo apt install mesa-vulkan-drivers libvulkan1 ffmpeg`.
2. Start every later shell call that runs `tsrct` with these two lines. The first selects Mesa's lavapipe driver, which renders on the CPU and works on hosts without a GPU. The loader skips a listed driver built for another architecture. The second sets `$tsrct`.

   ```sh
   export VK_ICD_FILENAMES=$(ls /usr/share/vulkan/icd.d/lvp_icd*.json | paste -sd: -)
   tsrct=${XDG_DATA_HOME:-$HOME/.local/share}/Tesseract/bin/tsrct
   ```

   To use a working GPU driver instead, leave out the first line.

## Author, export, and check

1. Read Mirage's `tesseract-motion` skill at the path above and follow it, with `"$tsrct"` in place of `tsrct` in its commands. Keep telemetry off.
2. Export every MP4 through the system ffmpeg. Without these flags, `tsrct export` fails with `No usable H.264 encoder`. A ProRes overlay export (`--format prores`) needs no extra flags.

   ```sh
   export VK_ICD_FILENAMES=$(ls /usr/share/vulkan/icd.d/lvp_icd*.json | paste -sd: -)
   tsrct=${XDG_DATA_HOME:-$HOME/.local/share}/Tesseract/bin/tsrct
   "$tsrct" export --project project.tsrct --output ad.mp4 \
     --encoder-backend external-ffmpeg-command --ffmpeg-path "$(command -v ffmpeg)"
   ```

3. Check each export with `ffprobe -v error -show_entries stream=codec_name,pix_fmt,width,height:format=duration -of compact ad.mp4`, naming the file you exported. Expect the canvas's aspect ratio and the planned duration. Expect `h264` for an MP4, or `prores` with a `yuva` pixel format for an overlay MOV.
4. Deliver the `.tsrct` project with the checked export.
