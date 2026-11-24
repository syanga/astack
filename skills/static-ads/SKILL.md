---
name: static-ads
description: Make five on-brand static Meta ads for a brand from its website and, optionally, its repo, as HTML screenshots or GPT Image renders.
argument-hint: "<brand url> [repo]"
disable-model-invocation: true
---

# static-ads

Brand URL and repo → style guide → 5 ideas → HTML pages, with GPT Image when there's no repo → Feed and Story PNGs in `~/Downloads/<slug>-ads-<date>/`.

Put every file from this run in that folder: the style guide, the reference image, the ad pages and their files, and the ads. `<slug>` is the brand's name in lowercase with hyphens, such as `acme-coffee`. `<date>` is today's date as `YYYY-MM-DD`. A GPT Image run needs `OPENAI_API_KEY` in the environment. Never print the key.

## 1. Their current ads

If the user pasted the text of the ads they run, or attached screenshots, note what those ads lead with: proof, pain, or offer. The new ads should target the angles they don't cover yet. If the user gave no ads, skip this step.

## 2. Brand style

Read the page with curl. The browser is only for the page screenshot at the end of this step.

### With a repo

Use a repo only when the user passes one. `[repo]` is a local path when that folder exists, and `owner/name` otherwise. Clone `owner/name` into a temp folder with `git clone --depth 1 https://github.com/<owner>/<name> <folder>`, or `gh repo clone <owner>/<name> <folder> -- --depth 1` when it is private. Only read the repo. Install, build, and run nothing from it.

Read from it:
- Color values, such as CSS variables or a Tailwind theme
- Font files and their `@font-face` rules. When the repo loads a font from a service such as Google Fonts, download the font's files.
- The logo, as an SVG when there is one
- Product images
- Product names and prices, as the site shows them

Copy the font files, the logo, and the product images the ads will use into `pages/assets/` in the run folder.

### Without a repo

- `curl -sL <url>` to fetch the page, and curl each stylesheet it links. Then grep the HTML and CSS for:
  - Hex and rgb colors, plus Framer `--token-*` values
  - `font-family`, and the size/weight/letter-spacing combos
  - `<h1>`–`<h3>` text
  - `og:image`
- Download the og image into the run folder. It is the style reference for every GPT Image call. When the page has no og:image, `page.png` from the next section is the reference.

### Both

- Screenshot the page's first screen into the run folder as `page.png`, and view it to confirm the look. `<skill-dir>` is the folder this SKILL.md is in.

  ```bash
  python3 <skill-dir>/scripts/render.py <run-folder> --page <url>
  ```

  The screenshot needs Google Chrome or Chromium, and so does every Feed and Story file in step 4. When `render.py` finds neither, tell the user. With a repo, stop there, because no ad can be made. Without a repo, go on with GPT Image, and ask the user for a reference image when the page has no og:image.
- Write `ad-style-guide.md` in the run folder. Keep it short: colors, fonts, layout, product names and prices, copy rules and imagery, about 40 lines.

## 3. Five static ideas

Use one line each: a name, the visual, the headline (3–8 words, caps), the sub-line and the CTA. The name has 12 characters or fewer, such as `Proof`, because Meta's media library shows 12. Aim each one at a pain their current ads don't cover. With no ads, aim each one at a different pain or claim from the site's own copy. Show the user the list before generating unless they already said to make them.

## 4. Make the ads

Every ad is a page, `pages/<Name>.html` in the run folder, and `render.py` screenshots each page twice: `<Name> Feed.png` at 1440x1800 and `<Name> Story.png` at 1440x2560. With a repo, build the whole ad in HTML. Without one, GPT Image draws the ad and the page frames it.

### HTML ads, with a repo

Copy `<skill-dir>/starter/ad.html` to `pages/<Name>.html` for each ad. The starter lays out one ad for both sizes: a CSS media query moves the content into the part of a Story that Instagram leaves uncovered. Then:
- Set the `:root` colors and fonts to the repo's values, with an `@font-face` for each font file in `pages/assets/`.
- Put in the brand's logo, the product image, and the idea's headline, sub-line, CTA, and URL.
- Replace the footer's notice line with the site's own notice, or delete the line when the site shows none.
- Change the layout when the idea needs it, such as a product shot that fills the frame.
- Use only files in `pages/`. `render.py` blocks the network, so a remote font or image never loads.

When an idea needs imagery the repo doesn't have, generate it without text. Write `pages/<image>.req.json` in the JSON shape the next section shows, with these changes:
- The prompt describes only the scene in the brand's colors and ends "No text, no letters, no logos". Leave out the style block.
- `images` is left out, so no page text gets copied.
- `size` fits the slot in the page, with both sides in multiples of 16.

Run `generate.py` on `pages/`, first with `--dry-run`, and use the PNG in the page. Each image costs about $0.07.

### GPT Image ads, without a repo

Copy the reference image into `pages/`. Write one `pages/<Name>.req.json` per ad:

```json
{
  "prompt": "<scene: layout, UI elements, exact headline/sub/CTA text> <style block>",
  "size": "1472x1840",
  "quality": "high",
  "images": ["<og image file>"]
}
```

`images` names reference files in `pages/`: the og image, or `page.png` when the page has no og:image.

The style block, filled from the guide:
- Brand colors with hex values
- Headline font description and case
- Small wordmark at the top "exactly like the reference". When the reference shows no wordmark, describe it in words: the brand name, its font, and its color.
- The CTA pill
- "No drop shadows, no robots, no glowing AI imagery"
- "Use ONLY the wordmark and look from the reference, do not copy its headline or logos"
- "Render all text exactly as written"
- "Show no text other than the text in this prompt"

Then run:

```bash
python3 <skill-dir>/scripts/generate.py <run-folder>/pages --dry-run   # local check, no OpenAI call
python3 <skill-dir>/scripts/generate.py <run-folder>/pages             # about $0.07 per ad
```

`--dry-run` reads the request files and prints each ad's size, quality, prompt length, and reference count. It does not call OpenAI and does not spend money. That is the point: catch a bad request before the image run.

The real run generates every ad in parallel with `gpt-image-2.6-sunburst`. For fast drafts, pass `--model gpt-image-2.6-flare`. An ad that fails prints `FAILED` with its error, so the rest of the batch can finish. For each finished ad, the script prints `done` with the image size and saves `pages/<Name>.png`. It also saves OpenAI's response, without the image, as `pages/<Name>.res.json`. It skips an ad that already has a PNG and prints `exists` with that file's size.

Then copy `<skill-dir>/starter/image-ad.html` to `pages/<Name>.html` for each ad, point its `img` at `<Name>.png`, and set `--background` to the brand's background color. The Feed shows the image edge to edge. The Story fits the same image inside the part Instagram leaves uncovered, so the Story needs no second GPT Image call.

### Screenshot the pages

```bash
python3 <skill-dir>/scripts/render.py <run-folder>
```

It screenshots every `pages/<Name>.html` with headless Chrome and prints each ad's sizes and a line for each problem:
- A name over 12 characters
- Text cut off at an edge
- Story text in the top 320 px or the bottom 693 px, which Instagram covers

It reads the text in the page, not text drawn inside an image, so place an image that holds text by eye. Fix the page and run `render.py` again until it prints no problem. It overwrites the PNGs each time. Without Chrome or Chromium, hand over each GPT Image ad's `pages/<Name>.png` as its Feed image, and tell the user the Story files are missing.

## 5. Review

View every Feed and Story PNG. Check for:
- Misspelled text
- Made-up handles or URLs, such as a fake `@brand` or `shop.brand.ai`. Flag those to the user.
- A wordmark that doesn't match
- Copied customer logos
- Text the prompt didn't ask for, such as made-up page content
- For a GPT Image ad, a size other than 1472x1840 on its `done` or `exists` line

Read the script output for `FAILED`. A failed ad can still leave the script looking finished.

`generate.py` treats any existing PNG as done and skips generation. To generate an ad again, delete its `.png` and `.res.json` in `pages/`, then run `generate.py` and `render.py` again.

## Gotchas

- **Size:** Meta recommends 1440×1800 for 4:5 feed images and 1440×2560 for 9:16, and `render.py` writes exactly those. OpenAI needs both sides in multiples of 16, which 1800 isn't, so GPT Image makes 1472×1840, exactly 4:5, and the Feed screenshot scales it to 1440×1800.
- **Deliverables:** text-only answers when the user asks for just the text. Short md files, not HTML reports.
