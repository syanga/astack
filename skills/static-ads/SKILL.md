---
name: static-ads
description: Make five on-brand static Meta ads for a brand from its website, with GPT Image through the OpenAI API.
argument-hint: "<brand url>"
disable-model-invocation: true
---

# static-ads

Brand URL → style guide → 5 ideas → GPT Image → 1472×1840 PNGs in `~/Downloads/<slug>-ads-<date>/`.

Put every file from this run in that folder: the style guide, the reference image, the request files, and the ads. `<date>` is today's date as `YYYY-MM-DD`. The real run needs `OPENAI_API_KEY` in the environment. Never print the key.

## 1. Their current ads

If the user pasted the text of the ads they run, or attached screenshots, note what those ads lead with: proof, pain, or offer. The new ads should target the angles they don't cover yet. If the user gave no ads, skip this step.

## 2. Brand style from the site

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

Use one line each: the visual, the headline (3–8 words, caps), the sub-line and the CTA. Aim each one at a pain their current ads don't cover. With no ads, aim each one at a different pain or claim from the site's own copy. Show the user the list before generating unless they already said to make them.

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

`images` names reference files in the run folder. When the site has no og:image, leave `images` out.

The style block, filled from the guide:
- Brand colors with hex values
- Headline font description and case
- Small wordmark at the top "exactly like the reference"
- The CTA pill
- "No drop shadows, no robots, no glowing AI imagery"
- "Use ONLY the wordmark and look from the reference, do not copy its headline or logos"
- "Render all text exactly as written"

Then run:

```bash
python3 <skill-dir>/scripts/generate.py <run-folder> --dry-run   # local check, no OpenAI call
python3 <skill-dir>/scripts/generate.py <run-folder>             # about $0.07 per ad
```

`<skill-dir>` is the folder this SKILL.md is in.

`--dry-run` reads the request files and prints each ad's size, quality, prompt length, and reference count. It does not call OpenAI and does not spend money. That is the point: catch a bad request before the image run.

The real run generates every ad in parallel with `gpt-image-2.6-sunburst`. For fast drafts, pass `--model gpt-image-2.6-flare`. An ad that fails prints `FAILED` with its error, so the rest of the batch can finish. For each finished ad, the script prints `done` with the image size and saves `<n>-<name>.png`. It also saves OpenAI's response, without the image, as `<n>-<name>.res.json`. It skips an ad that already has a PNG and prints `exists` with that file's size.

## 5. Review

View every PNG. Check for:
- Misspelled text
- Made-up handles or URLs, such as a fake `@brand` or `shop.brand.ai`. Flag those to the user.
- A wordmark that doesn't match
- Copied customer logos
- A size other than 1472x1840 on the ad's `done` or `exists` line

Read the script output for `FAILED`. A failed ad can still leave the script looking finished.

The script treats any existing PNG as done and skips generation. To generate an ad again, delete its `.png` and `.res.json`, then rerun.

## Gotchas

- **Size:** 1472×1840 is exactly 4:5 and just above the 1440×1800 Meta recommends for feed images. OpenAI needs both sides in multiples of 16, which 1800 isn't. Meta takes the file as it is, so don't resize it.
- **Deliverables:** text-only answers when the user asks for just the text. Short md files, not HTML pages.
