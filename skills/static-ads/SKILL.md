---
name: static-ads
description: Make five on-brand static Meta ads and their ad text from a brand's website and, optionally, its repo, with GPT Image and headless Chrome.
argument-hint: "<brand url> [repo]"
disable-model-invocation: true
---

# static-ads

Brand URL and repo → style guide → 5 ideas → GPT Image scenes or ads → HTML pages → Feed and Story PNGs and `copy.md` in `~/Downloads/<slug>-ads-<date>/`.

Put every file from this run in that folder: the style guide, the reference image, the ad pages and their files, the ads, and the ad text. `<slug>` is the brand's name in lowercase with hyphens, such as `acme-coffee`. `<date>` is today's date as `YYYY-MM-DD`. A GPT Image run needs `OPENAI_API_KEY` in the environment. Never print the key.

## 1. Their current ads

If the user pasted the text of the ads they run, or attached screenshots, note what those ads lead with: proof, pain, or offer. The new ads should target the angles they don't cover yet. If the user pasted results, such as clicks or sales per ad, say how many ads the numbers cover, and how many impressions when the results give them. State in plain words how far the numbers can be trusted: forty or more ads support a pattern, and a dozen don't. If the user gave no ads, skip this step.

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
- Download the og image into the run folder as `site-og-image.<ext>`, with the og image's own extension. It is the style reference for every GPT Image ad. When the page has no og:image, `site-screenshot.png` from the next section is the reference.

### Both

- Screenshot the page's first screen into the run folder as `site-screenshot.png`, and view it to confirm the look. `<skill-dir>` is the folder this SKILL.md is in.

  ```bash
  python3 <skill-dir>/scripts/render.py <run-folder> --page <url>
  ```

  The screenshot needs Google Chrome or Chromium, and so does every Feed and Story file in step 4. When `render.py` finds neither, tell the user. With a repo, stop there, because no ad can be made. Without a repo, go on with GPT Image, and ask the user for a reference image when the page has no og:image.
- Write `ad-style-guide.md` in the run folder. Keep it short: colors, fonts, layout, product names and prices, copy rules and imagery, about 40 lines. Look for the site's notice in the page's footer and on the legal or terms pages the footer links to. A notice is a non-affiliation line, or a trademark line that names a mark or its owner. "All other trademarks are the property of their owners" names neither, so it isn't one. Copy the notice into the guide word for word. End the guide with three lists:
  - **Proof:** each number, outcome, and fact the site states, with its page URL
  - **Only us:** what no other brand in the niche could truthfully say, with its page URL: its product and feature names, its price and offer dates, its format, its guarantee, and claims about its own product. A fact about the exam, the field, or the customer's problem isn't on it, because every competitor can state it.
  - **Off limits:** other organizations' logos, and claims the user rules out, such as ones a regulator or employer forbids

## 3. Five static ideas

Use one line each: a name, the visual, the headline (3–8 words, caps), the sub-line, the CTA, the cited page, the hook formula, and the Only us item. The cited page is the URL of the site page that states the ad's claim. Put it on every line, even when several ideas cite the same page, and fetch each cited page with curl to check the claim is there. The name has 12 characters or fewer, such as `Proof`, because Meta's media library shows 12. Aim each one at a pain their current ads don't cover. With no ads, aim each one at a different pain or claim from the site's own copy. Give each idea a different formula from [hooks.md](hooks.md). Check the headline against hooks.md's Say the number rule and the formula's trap. Build each headline on an item from the Only us list. If a competitor could run the headline unchanged, rewrite it. In Objection and Question, the headline is the customer's own words, so the sub-line takes the item and the rewrite test instead. Take each number from the style guide's Proof list, and leave out everything on its Off limits list. When an idea needs a number the site doesn't state, write a placeholder such as `{{pass rate}}` and tell the user. The claims rules still apply, so drop that number or the idea before making the ads. Show the user the list before generating unless they already said to make them. When you show it, ask which claims they can't make, such as ones a regulator or employer forbids. Add those to Off limits, and drop or change the ideas that make them.

Every ad keeps to these claims rules:
- It states only claims and numbers that its cited page states. That includes pass rates, guarantees, testimonials, customer counts, and prices. A claim or number that appears only in hidden markup, such as a stand-in price that a script replaces, is one the page doesn't state.
- The only logo or mark it shows is the brand's own, so no exam owner's or partner's logo.
- It names products, exams, and organizations the way the site names them.
- When the style guide records a notice, every ad carries it word for word. The first rule covers the ad's claims, not the notice.

## 4. Make the ads

Every ad is a page, `pages/<Name>.html` in the run folder, and `render.py` screenshots each page twice: `<Name> Feed.png` at 1440x1800 and `<Name> Story.png` at 1440x2560. With a repo, GPT Image draws a scene with no text, and the page puts the brand's real words and files on top of it. Without a repo, GPT Image draws the whole ad and the page frames it.

### Hybrid ads, with a repo

For each ad, write `pages/scene-<Name>.req.json` in the JSON shape the next section shows, with these changes:
- The prompt describes a scene that fits the idea, in the brand's colors with their hex values. It keeps the main subject in the middle and leaves plain space at the top and bottom for text. It ends "No text, no letters, no numbers, no logos, no screens or pages with content".
- The scene never shows the product. The page shows the real product image from the repo, so no product page is ever drawn.
- `size` is `1152x2048`, so one scene fills both the Feed and the Story. Leave out `images`.

Run `generate.py` on `pages/`, first with `--dry-run`. Each scene costs about $0.07.

Then copy `<skill-dir>/starter/ad.html` to `pages/<Name>.html` for each ad. The starter lays out one ad for both sizes: a CSS media query moves the content into the part of a Story that Instagram leaves uncovered. Then:
- Point the `scene` image at `scene-<Name>.png`.
- Set the `:root` colors and fonts to the repo's values, with an `@font-face` for each font file in `pages/assets/`. Set `--scrim` dark or light enough that the text reads clearly on the scene.
- Put in the brand's logo, the product image, the idea's headline, sub-line, and CTA, and the site's address.
- Replace the footer's notice with the notice from the style guide, or delete it when the guide records none.
- Change the layout when the idea needs it, such as a product shot that fills the frame.
- Use only files in `pages/`. `render.py` blocks the network, so a remote font or image never loads.

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

`images` names reference files in `pages/`: `site-og-image.<ext>`, or `site-screenshot.png` when the page has no og:image.

The style block, filled from the guide:
- Brand colors with hex values
- Headline font description and case
- Wordmark at the top, as small as the text size line below allows, "exactly like the <brand name> wordmark in the reference". When the reference doesn't show the brand's wordmark, describe it in words: the brand name, its font, and its color.
- The CTA pill
- "No drop shadows, no robots, no glowing AI imagery"
- "Use ONLY the <brand name> wordmark and look from the reference, do not copy its headline or logos"
- "Render all text exactly as written"
- "Show no text other than the text in this prompt"
- "Set every line of text at least 3.5% of the image's width tall, and at least 12% of its width from each edge"
- When the style guide records a notice, the notice at the bottom in the smallest type the line above allows, word for word

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
- Text under 43 px, too small to read on a phone
- Text within 160 px of an edge
- Text that overlaps other text
- An image that failed to load

It reads the text in the page, not text drawn inside an image, so place an image that holds text by eye. Fix the page and run `render.py` again until it prints no problem. It replaces the PNGs each time, and deletes both of an ad's PNGs when it prints `FAILED` for the ad. Without Chrome or Chromium, hand over each GPT Image ad's `pages/<Name>.png` as its Feed image, and tell the user the Story files are missing.

## 5. Review

View every Feed and Story PNG. Without Chrome or Chromium, view each GPT Image ad's `pages/<Name>.png` instead. Check for:
- Misspelled text
- Made-up handles or URLs, such as a fake `@brand` or `shop.brand.ai`. Flag those to the user.
- A wordmark that doesn't match
- Copied customer logos
- Text the prompt didn't ask for, such as made-up page content
- An ad that breaks a claims rule from step 3
- For a GPT Image ad, a size other than 1472x1840 on its `done` or `exists` line
- For a GPT Image ad, text in `pages/<Name>.png` smaller than about 3.5% of the image's width, or within about 12% of its width from an edge

Read the script output for `FAILED`. A failed ad can still leave the script looking finished.

Hand over only ads that keep every claims rule. For a hybrid ad, fix the page when its page breaks the rule, and run `render.py` again. When its scene breaks the rule, draw the scene again once, as the next paragraph says. When the new scene still breaks it, delete the scene's `<img>` from the page, so the ad shows the page's background color. Then run `render.py` again, and tell the user which rule the scene broke. For a GPT Image ad that breaks one, delete its `.req.json`, `.png`, `.res.json`, and `.html` in `pages/`, and its `<Name> Feed.png` and `<Name> Story.png` in the run folder. Then tell the user which rule it broke.

`generate.py` treats any existing PNG as done and skips generation. To draw an image again, delete its `.png` and `.res.json` in `pages/`, such as `scene-<Name>.png` and `scene-<Name>.res.json` for a hybrid ad's scene. Then run `generate.py` and `render.py` again.

## 6. Ad text

Write `ad-text.json` in the run folder, with one entry per ad that passed the review:

```json
{
  "campaign": "<only when the user names a campaign>",
  "ads": [
    {"name": "<Name>", "page": "<cited page URL>", "headline": "<27 characters or fewer>", "primary_text": "<text>"}
  ]
}
```

- Put the primary text's point in its first 125 characters, which the feed shows before "See more". End the primary text with one ask, the ad's CTA.
- Keep the headline to 27 characters, Meta's recommended length for Facebook Feed.
- Write no description. Meta's is optional and often hidden.
- The claims rules from step 3 apply to the headline and the primary text.

Then run:

```bash
python3 <skill-dir>/scripts/ad_text.py <run-folder>
```

It writes `copy.md` with each ad's files, headline, link, and primary text, ready to paste into Ads Manager. Each link is the ad's cited page plus `utm_source=meta`, `utm_medium=paid`, `utm_campaign`, and `utm_content` from the ad's name. `utm_campaign` is the run folder's name unless the user names a campaign. While any ad fails a check, such as a long headline, an em dash, or an emoji, the script writes no `copy.md` and prints a line for each problem. Fix each problem it prints, and run it again until it prints `wrote`.

## 7. Hand over

Give the user the run folder and one line per ad. Then tell them:
- Nothing was uploaded to Meta.
- Adding ads to a live ad set restarts that ad set's learning phase, so the user picks when to add them.

## Gotchas

- **Size:** Meta recommends 1440×1800 for 4:5 feed images and 1440×2560 for 9:16, and `render.py` writes exactly those. OpenAI needs both sides in multiples of 16, which 1800 isn't, so GPT Image makes 1472×1840, exactly 4:5, and the Feed screenshot scales it to 1440×1800. A scene is 1152×2048, 9:16, and the page crops it to fill either size.
- **Deliverables:** text-only answers when the user asks for just the text. Short md files, not HTML reports.
