---
name: brag-slim
description: Turn a project directory or a website URL into short, shareable videos that sell it, with music, motion, and share copy. Use when someone wants to show off what they built.
disable-model-invocation: true
---

# /brag-slim

You built it. Now brag. You make the whole video yourself — story, visuals, audio, render — with whatever tools are on the machine.

Whatever the tone, it should feel like a modern, slick, polished launch video: nothing on screen or in the soundtrack that doesn't earn its place.

Usage: `/brag-slim [input] [options]`. Options (flags or plain language):

| Option | Default |
|---|---|
| `--tone <preset or freeform>` | inferred; `default` if nothing clearly fits |
| `--format landscape\|vertical\|square` | landscape (1920×1080; vertical 1080×1920, square 1080×1080), 30fps |
| `--duration <s>` | about 20s |

Write the deliverables to `brag-output/` in the current directory (timestamped `brag-output-YYYY-MM-DD-HHmmss/` if it already exists). A revision in the same session overwrites the files in that session's output directory. Keep every intermediate file (frames, downloads, scripts, stems) in a `work/` subfolder inside it. Put a `.gitignore` containing `*` in the output directory when you create it, so Git ignores everything in it inside any repository.

## 1. Inspect

First decide what the input is, then gather material from it. Only the source changes; everything from the questions below onward is the same for every input.

| Input | How to recognize it | Where the material comes from |
|---|---|---|
| Project | No input given, and the current directory is a project | The code |
| Own site | A URL served by the project in the current directory | The code, as for a Project |
| Website | Any other `http(s)://` URL, or a bare domain like `example.com` | The live site |

If the input matches no type, or there's no input and the current directory isn't a project, ask the user what to brag about.

### Project

Read the code: the main page, styles (exact colors and fonts), README, routes and key components. The best material is the product **in use** — find its 2–3 beats: entry → key action → result.

You have the source, so use it directly: import or render the project's real components, stylesheets, fonts, images and animations in the video instead of rebuilding them.

Capture the project's pages from a local production build only, even when the project has a live site. A visit to the live site writes real analytics, and its forms act on real data. In the build:

- Unset the backend and analytics origins, and block requests to their live hosts.
- Serve the API from the repo's API fixtures, fictional responses, or a local backend with sandboxed payment and a mail sink.

Don't read build output (`dist/`, `.next/`, `build/`), lock files, test files other than API fixtures you serve, `.git/`, env and secret files (`.env`, `.env.*`), key and credential material (`.pem`, `.key`, `id_rsa`, service-account JSON, anything under `secrets/` or `credentials/`), local config that commonly holds tokens, or anything `.gitignore` excludes for those reasons.

### Website

Get the site as a visitor sees it. Many sites build their page with JavaScript, so a plain download can come back as an almost empty shell. If it does, load the page in a headless browser to get the rendered result. Dismiss cookie banners and other overlays, and scroll section by section, since content that animates in on scroll stays blank in a single full-page capture.

- **Copy:** headline, tagline, section headings, feature names, calls to action, testimonials. Also check the title, meta description and social-preview tags.
- **Identity:** exact colors from the site's CSS and the fonts it loads.
- **Visuals:** the logo, product screenshots, hero images, demo videos. Download the ones you'll use into `work/`.
- **Screenshots:** capture the page at the video's aspect ratio to understand the layout. In the video, reuse the site's real markup, CSS and assets and animate those, rather than panning over flat screenshots.
- **The product in use:** check the demo videos, how-it-works sections and linked docs for the entry → key action → result flow.

### Then, for every input

When the product delivers a per-customer artifact, such as a report, an export or a result page, film one the product generated for fictional inputs. If none exists and you have the code, generate one with the local setup described under Project. If generating spends money, such as paid model calls, or writes outside the machine, list the fictional inputs and the spend, and wait for the user's yes even if they asked to skip plan approval. If you end up without a generated result, name its absence as the plan's main weakness.

Before planning, answer: What is it (one sentence)? Who is it for, and what does it do for them? What sets it apart? What's the most impressive or funniest claim? What's the visual hook? What real UI or flow should be shown? What tone fits? What's the one-line share caption?

Everything gathered here can end up in a public video. Never carry secrets, API keys, tokens, internal hostnames or URLs, real customer or user names, email addresses, or other personal data into the plan, the video, or the share copy. If the real UI shows such data, swap in plausible fictional stand-ins and say so in the plan. In on-screen text, call the viewer "you" and anyone else by pronoun; fictional names appear only inside product UI.

Pitch every word and visual at the customer from "Who is it for", not at the person asking for the video.

## 2. Plan

Write `brag-plan.md` in the output directory: the angle, tone, visual identity, and for each video a scene-by-scene storyboard with durations that sum to the target.

If the user points at one part — a new version, a new feature, one angle — make this the focus of the video.

**Shape:** one video per feature, unless the user asks for one combined video. Each opens on the viewer's own question in big type (2–3s), then shows what sets the product apart in the product's own words, the real result as proof, 2–3 payoffs, and a call to action. Each payoff quotes a revealing detail from the result, such as a specific finding, rather than its overall verdict. The shape sets the scenes, and the tone sets their feel. A starting shape, not a template.

Then show the user `brag-plan.md` and the format, and wait for their approval. Update the plan with any edits before building. Plan approval is the video's design checkpoint, so build without separate design mocks. Before rendering a kind of visual the approved plan did not describe, show the user a still and wait for approval. If the user asked to skip plan approval, build without waiting at either point.

## Creative laws

- **Short.** 15–25 seconds; 18–22 is the sweet spot.
- **Clear to a stranger.** After one viewing, someone who's never heard of it knows what it does, who it's for, and how to get it. Lead with that, not with how it's built.
- **The hook is everything.** The first 2 seconds decide whether anyone keeps watching. Plan it first.
- **Show the thing.** Reuse the real thing from the source — its UI, components, copy, images, videos and animations — rather than re-creating it. Rebuild only what you can't reuse. Prefer the working app doing its job over a landing page describing it. Small illustrative UI text is fine when showing the product in use (a filename, an "Exported" toast); invented claims, numbers, or testimonials are not. Never abstract filler. New graphics use the product's own icons.
- **Footage behind type.** Put footage behind type, under a brand-tinted scrim, instead of a plain background. For the general public, footage of people and objects beats charts and diagrams. Product icons stay in front of it. When the source has no fitting footage, use stock clips licensed for commercial use, with no recognizable logos and nobody who appears to endorse the product.
- **Specific.** It must feel made for this exact project. Use its own copy and claims; no generic SaaS language ("streamline your workflow" is banned).
- **Readable.** Pace comes from motion and cuts, not from pulling text away early. Each scene says one thing in a few words. Any line the viewer is meant to read stays fully visible and settled long enough to read it (roughly 0.3s per word), counted from when the whole line is on screen. Text that's only texture doesn't need to be read, but stays legible enough to show what it is: a spotlight on one line of real output dims the rest without hiding it.
- **Make it alive.** Things that appear one by one, simulated clicks, swipes, and typing beat static slides.
- **Funny earns its place.** Humor comes from the project's own absurdity, not from trying.
- **Every frame postable.** Any frozen frame should be worth sharing.

## Tones

Presets are defaults; freeform direction ("fake Series A launch from 2016") refines or overrides them.

| Tone | Feel | Pacing / transitions |
|---|---|---|
| `default` | Punchy, playful, clean | 4–5 scenes; soft transitions |
| `polished` | Serious, elegant, restrained | 3–4 scenes, long holds; soft fades |
| `yc-parody` | Deadpan startup launch, played straight | 4–5 scenes, one claim each; hard cuts |
| `chaotic` | FAST, LOUD, ALL CAPS | 6–8 scenes, some under 2s; flash/zoom cuts |
| `deadpan` | Calm, dry, nothing is a joke | 3–4 scenes, big empty space; slow fades |
| `cinematic` | Trailer-scale, epic claims | 4–5 scenes, big type; dramatic wipes |
| `app-store` | Clean feature cards | 4–6 scenes; smooth slides |

## Sound

Write the music and sound effects as one piece: effects in the same key and the same space as the music, blended in rather than laid on top. Give it a basic, proper mix, the way a real track is mixed: effects sit softly under the music, nothing harsh or spiky, and repeated little sounds stay in the background.

## 3. Build, check, render

Build it with whatever works on this machine. If you draw the video in a browser, make every frame a pure function of time and wait for fonts and images to load before capturing each one. Measure the position of each element a camera move targets once, before you apply any transform. A position measured after a zoom is already scaled, so the camera drifts. When shared render code changes, re-render every video built from it.

Write a pacing timeline, one beat per scene, from the same data that drives the render. Before each full render, run `python3 <this skill's directory>/scripts/check_pacing.py <timeline.json>`, and lengthen scenes or cut words until it exits 0. Its `--help` describes the format.

Before the full render, look at stills from every scene *and* from mid-transition, and fix overflow, collisions, and low contrast. Stills cannot show shake, so for each camera move, track one element's position across consecutive frames, and fix any move where the element jumps or reverses. A plain crossfade between two busy layouts makes a muddy double exposure; stagger it (old content out, then new content in) or dip through the background. Then render each video as `brag-<feature>.mp4`.

## 4. Deliver

- **Poster:** pull each video's strongest *settled* frame (text fully in, not mid-transition) to `brag-<feature>.jpg`, and bake it in as frame 0 of that video so every platform's thumbnail shows it. Replace frame 0 rather than adding a frame, so the duration and audio sync stay the same.
- **`share-copy.txt`:** for each video, 1–3 sentences, postable as-is, specific, matching the tone. No "excited to share."
- **`credits.txt`:** the source and license of every stock clip, when a video uses any.
- **Tell the user** where the video and copy are, give one sentence on the creative angle, and offer to re-roll a scene or try another tone.
