---
name: static-ads
description: Make five on-brand static Meta ads for a brand from its website or brand directory, with GPT Image through the OpenAI API or Gemini on Vertex.
argument-hint: "<brand url or brand directory> [gemini]"
disable-model-invocation: true
---

# static-ads

Brand URL or brand directory → style guide → 5 ideas → GPT Image or Gemini → 4:5 PNGs in `~/Downloads/<slug>-ads-<date>/`. When the brand directory has a layer file, `layer.json`, the image model draws only the art, and the script sets the brand's type and background over it.

Put every file from this run in that folder: the style guide, the reference images, the layer files, the request files, and the ads. `<date>` is today's date as `YYYY-MM-DD`. Use the backend the user names. Otherwise use OpenAI, which needs `OPENAI_API_KEY` in the environment. Gemini needs Google Cloud application default credentials, as described in step 4. Never print a key or token.

## 1. Their current ads

If the user pasted the text of the ads they run, or attached screenshots, note what those ads lead with: proof, pain, or offer. The new ads should target the angles they don't cover yet. If the user gave no ads, skip this step.

## 2. Brand style

### From a brand directory

A brand directory holds `brand_guide.md` (fonts, colors, logo, voice, and rules for ads), `claims.json`, and an optional `references/` folder of images. `claims.json` reads `{"brand": "<slug>", "claims": [{"id": 1, "text": "...", "source": "..."}]}` and lists the only facts an ad may state.

- Name the run folder after the directory's `brand` slug.
- Copy `brand_guide.md` into the run folder as `ad-style-guide.md`. It replaces the site scrape below.
- Copy each PNG, JPEG, or WebP from `references/` into the run folder. They are the style references for every render. With no such images, take the og:image from the brand's site, as below.
- Every rule in the guide's rules for ads applies to every idea.
- If the directory has `layer.json`, copy it into the run folder, with the `fonts/` folder and the logo it names, keeping their paths. Every ad then uses the compose step in step 4.

`layer.json` is the guide's type, colors, and logo in a form the script reads. Paths are relative to the directory:

```json
{
  "backgrounds": ["#061521", "#5b86a8"],
  "accents": ["#ffd9c4", "#ffffff"],
  "logo": "lockup.png",
  "button": {"color": "#026692", "opacity": 0.82},
  "type": {
    "headline": {"font": "fonts/archivo-500-125.ttf", "weight": 500, "width": 125, "case": "upper", "color": "#ffffff"},
    "subline": {"font": "fonts/archivo-450-100.ttf", "weight": 450, "width": 100, "color": "#ffffff"},
    "cta": {"font": "fonts/archivo-700-100.ttf", "weight": 700, "width": 100, "case": "upper", "color": "#ffffff"}
  }
}
```

- `backgrounds` lists the guide's background hexes. The first is the default.
- `accents` is optional: the guide's colors for lines and shapes in the art. The script resets art drawn close to one of them, and its anti-aliased edges, to the exact hex.
- `logo` is optional: a PNG of the logo or lockup with a transparent background.
- `button` is the call-to-action pill's fill and opacity.
- `type` names one font file per role. Each file is a static TTF or OTF at the role's `weight` and `width` (a percent, such as 125 for Expanded). A variable font renders only its default instance, so the script refuses one. `case: "upper"` sets the role in capitals.

### From the website

Use curl only, never the browser.

- `curl -sL <url>` to fetch the page. Then grep the HTML for:
  - Hex and rgb colors, plus Framer `--token-*` values
  - `font-family`, and the size/weight/letter-spacing combos
  - `<h1>`–`<h3>` text
  - `og:image`
- The `og:image` URL is the style reference for every render.
- Download the og image into the run folder and view it to confirm the look.
- Write `ad-style-guide.md` in the run folder. Keep it short: colors, fonts, layout, copy rules and imagery, about 40 lines.

## 3. Five static ideas

Use one line each: the visual, the headline (3–8 words, caps), the sub-line and the CTA. Aim each one at a pain their current ads don't cover. With no ads, aim each one at a different pain or claim from the site's own copy. With a brand directory, every number, price, and other fact in an ad must match a claim in `claims.json`. When it lists no claims, ask the user which lines the ads may state. Show the user the list before generating unless they already said to make them.

## 4. Generate

Write one `<n>-<name>.req.json` per ad in the run folder:

```json
{
  "prompt": "<scene: layout, UI elements, exact headline/sub/CTA text> <style block>",
  "size": "1472x1840",
  "quality": "high",
  "images": ["<og image file>"]
}
```

`images` names reference files in the run folder. When there is no reference image, leave `images` out.

The style block, filled from the guide:
- Brand colors with hex values
- Headline font description and case
- Small wordmark at the top "exactly like the reference"
- The CTA pill
- "No drop shadows, no robots, no glowing AI imagery"
- "Use ONLY the wordmark and look from the reference, do not copy its headline or logos"
- "Render all text exactly as written"

Then run, adding `--backend gemini` to both commands for Gemini:

```bash
python3 <skill-dir>/scripts/generate.py <run-folder> --dry-run   # local check, no image call
python3 <skill-dir>/scripts/generate.py <run-folder>             # about 7 cents per ad on OpenAI
```

`<skill-dir>` is the folder this SKILL.md is in.

`--dry-run` reads the request files and prints each ad's output shape, prompt length, and reference count. It does not call the image API and does not spend money. That is the point: catch a bad request before the image run.

The real run generates every ad in parallel. OpenAI uses `gpt-image-2.6-sunburst`, or `gpt-image-2.6-flare` for fast drafts. Gemini uses `gemini-3-pro-image` on Vertex, or `gemini-3.1-flash-image` for fast drafts. Pass a draft model with `--model`. A busy reply (HTTP 429, 500, 502, 503, or 504) gets up to two retries. An ad that still fails prints `FAILED` with its error, so the rest of the batch can finish. For each finished ad, the script prints `done` with the image size and saves `<n>-<name>.png`. It also saves the API's response, without the image, as `<n>-<name>.res.json`. It skips an ad that already has a PNG and prints `exists` with that file's size. A composed ad works differently, as described under [With a layer file](#with-a-layer-file).

Gemini reads its token from `gcloud auth application-default print-access-token` and its project from `GOOGLE_CLOUD_PROJECT`, else from `gcloud config get-value project`. When the token step fails, ask the user to run `gcloud auth application-default login`, adding `--impersonate-service-account=<account>` when their project grants Vertex access through a service account. Gemini takes an aspect ratio and a size tier instead of pixels. The script turns `1472x1840` into 4:5 at 2K, and the dry run prints `4:5 2K`. A size whose ratio Gemini does not offer fails in the dry run.

### With a layer file

Add a `compose` block to each request, and write the prompt for the art alone:

```json
{
  "prompt": "<art: subject, layout, palette, no text> <style block>",
  "size": "1472x1840",
  "quality": "high",
  "compose": {
    "headline": "READ THE SKY YOU\nWERE BORN UNDER",
    "subline": "A written reading of your birth chart.",
    "cta": "Get your reading",
    "background": "#061521"
  }
}
```

- `headline` takes 1 to 3 lines, `subline` 1 or 2, and `cta` 1. Break lines with `\n` where a reader would pause. The script picks the largest size that fits each line.
- `background` is one of the layer file's `backgrounds`. Leave it out for the first.
- The prompt asks for no text of any kind: no letters, words, numbers, logos, buttons, or frames. It names the background hex as one flat color, edge to edge, and centers the subject. On 4:5 and 1:1, it leaves the top and bottom 30% as flat background. On 9:16, it keeps the subject between 30% and 55% of the height.
- Leave the headline, wordmark, font, CTA pill, and "Render all text exactly as written" out of the style block.

The layout puts the logo and headline at the top, the art in the middle, and the sub-line and CTA pill at the bottom, inside the placement's safe zone. On 4:5 and 1:1, type stays 7% from every edge. On 9:16, it stays out of the top 15%, the bottom 36%, and 8% at each side. The size must be 4:5, 1:1, or 9:16.

The compose step needs `ffmpeg` 6.1 or later with the `drawtext` filter. The dry run checks the fonts and lays out the type, then prints one more line per ad, with sizes in pixels at 1080 wide:

```
1-proof: compose 4:5 on #061521, headline 2x72px, subline 1x46px, cta 42px, logo lockup.png
```

A missing or wrong font file, a character the font lacks, or a line too long to stay 28 px tall at 1080 wide fails that ad in the dry run, before any image call. Shorten or split the line it names.

The real run saves the art as `<n>-<name>.art.png`. It then fills the art's flat background with the exact hex, resets accent lines to their exact hex, lays the type over it, and writes the ad as `<n>-<name>.png` and its type settings as `<n>-<name>.type.json`. The type settings name each line's font file, weight, width, size, and box. The ad's output line reads `done <size>, composed`.

The script composes the ad again on every run. It skips the image call while `<n>-<name>.art.png` exists, and the same art and request give the same PNG bytes. To change only the type, edit the `compose` block and rerun. To draw the art again, delete the `.art.png` and rerun.

## 5. Review

View every PNG. Check for:
- Misspelled text
- Made-up handles or URLs, such as a fake `@brand` or `shop.brand.ai`. Flag those to the user.
- A wordmark that doesn't match
- Copied customer logos
- A size on the ad's `done` or `exists` line other than 1472x1840 from OpenAI or 1856x2304 from Gemini
- On a composed ad, words or a logo drawn into the art, or art cut off at the top or bottom of its band. Delete the `.art.png` and rerun to draw it again.

Read the script output for `FAILED`. A failed ad can still leave the script looking finished.

Without a layer file, the script treats any existing PNG as done and skips generation. To generate such an ad again, delete its `.png` and `.res.json`, then rerun.

## Gotchas

- **Size:** 1472×1840 is exactly 4:5 and just above the 1440×1800 Meta recommends for feed images. OpenAI needs both sides in multiples of 16, which 1800 isn't. Gemini's 4:5 at 2K is 1856×2304, about 4:5. Meta takes either file as it is, so don't resize it.
- **Deliverables:** text-only answers when the user asks for just the text. Short md files, not HTML pages.
