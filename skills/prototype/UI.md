# UI Prototype

Generate **several radically different UI variations** on a single route, switchable from a floating bottom bar. The user flips between variants in the browser, picks one (or steals bits from each), then throws the rest away.

If the question is about logic/state rather than what something looks like, this is the wrong branch. Use [LOGIC.md](LOGIC.md).

## When this is the right shape

- "What should this page look like?"
- "I want to see a few options for this dashboard before committing."
- "Try a different layout for the settings screen."
- Any time the user would otherwise spend a day picking between three vague mockups in their head.

## Two sub-shapes: strongly prefer sub-shape A

A UI prototype is much easier to judge when it's **butting up against the rest of the app**: real header, real sidebar, real data, real density. A throwaway route on its own is a vacuum: every variant looks fine in isolation. Default to sub-shape A whenever there's a plausible existing page to host the variants. Only reach for sub-shape B if the prototype genuinely has no nearby home.

Build the prototype in a **scratch copy of the app** and run it there. Create the copy outside the current checkout with `git worktree add -b prototype/<topic> <path> HEAD`. Record its base commit in the preview cleanup record, then install its dependencies. If the checkout has uncommitted changes, apply them to the copy or ask. The working branch stays unchanged. A hand-built copy of the page drops whatever its author didn't notice, such as background scenes and animation.

Before starting the scratch app, list its outbound origins: data, auth, analytics, error reporting, feature flags, and build-time fetches such as hosted fonts. Copy only the env keys the page needs. Point any origin that would reach production at a local server or at fixtures with representative data. If the page needs a production origin to render or to show representative content, name it and ask before running. Serve the scratch app only on a local or already private URL, because it holds env keys. When the preview browser can't reach that URL, run the checks with Playwright.

When there is no app, build one self-contained HTML file in a scratch directory instead. When the app exists but can't run locally, report why and ask before using this fallback. Give each variant a `data-variant` section and switch with `history.replaceState`. If a real page exists, include a `current` section that reproduces it. Compare that section with a screenshot of the real page. Before loading a production site for that screenshot, name the URL and ask. Report every stand-in for runtime behavior (canvas, animation, live data) as unverified.

### Sub-shape A: adjustment to an existing page (preferred)

The route already exists. Variants are rendered **on the same route**, gated by a `?variant=` URL search param. The existing data fetching, params, and auth all stay. Only the rendering swaps. Keep the unchanged page reachable as `?variant=current`. It does not count toward N.

If the prototype is for something that doesn't yet have a page but *would naturally live inside one* (a new section of the dashboard, a new card on the settings screen, a new step in an existing flow), it's still sub-shape A. Mount the variants inside the host page.

### Sub-shape B: a new page (last resort)

Only use this when the thing being prototyped genuinely has no existing page to live inside (e.g. an entirely new top-level surface, or a flow that can't be embedded anywhere sensible).

Create a **throwaway route** following whatever routing convention the project already uses. Don't invent a new top-level structure. Name it so it's obviously a prototype (e.g. include the word `prototype` in the path or filename). Same `?variant=` pattern.

Before committing to sub-shape B, sanity-check: is there really no existing page this could be embedded in? An empty route hides design problems that a populated one would expose.

In both sub-shapes the floating bottom bar is identical.

## Process

### 1. State the question and pick N

Default to **3 variants**. More than 5 stops being radically different and starts being noise, so cap there.

Write down the plan in one line, in the prototype's location or a top-of-file comment:

> "Three variants of the settings page, switchable via `?variant=`, on the existing `/settings` route."

This works whether the user is here to push back or not.

### 2. Generate radically different variants

Before drafting, read the code that draws, animates, or measures layout around the target, such as canvas or WebGL scenes and scroll anchors. Keep each variant connected to that code so the page still runs it. When the user references an earlier design, find it in git history and port its markup and styles. Cite the commit.

Draft each variant. Hold each one to:

- The page's purpose and the data it has access to.
- The project's component library / styling system (TailwindCSS, shadcn, MUI, plain CSS, whatever).
- A clear exported component name, e.g. `VariantA`, `VariantB`, `VariantC`.

Variants must be **structurally different**: different layout, different information hierarchy, different primary affordance, not just different colours. Three slightly-tweaked card grids isn't a UI prototype, it's wallpaper. If two drafts come out too similar, redo one with explicit "do not use a card grid" guidance.

Match the project's colors, typography, spacing, and component conventions unless the user requests a redesign. New pages within an existing product follow the same conventions. For standalone work, follow the user's visual preferences. Avoid continuously repainting animations.

### 3. Wire them together

Create a single switcher component on the route:

```tsx
// pseudo-code, adapt to the project's framework
const variant = searchParams.get('variant') ?? 'A';
return (
  <>
    {variant === 'current' && <CurrentSection {...data} />}
    {variant === 'A' && <VariantA {...data} />}
    {variant === 'B' && <VariantB {...data} />}
    {variant === 'C' && <VariantC {...data} />}
    <PrototypeSwitcher variants={['current','A','B','C']} current={variant} />
  </>
);
```

For sub-shape A (existing page): keep all the existing data fetching above the switcher; only the rendered subtree changes per variant.

### 4. Build the floating switcher

A small fixed-position bar at the bottom-centre of the screen with three pieces:

- **Left arrow**: cycles to the previous variant (wraps around).
- **Variant label**: shows the current variant key and, if the variant exports a name, that name too. e.g. `B (Sidebar layout)`.
- **Right arrow**: cycles forward (wraps around).

Behaviour:

- Clicking an arrow updates the URL search param (use the framework's router, e.g. `router.replace` on Next, `navigate` on React Router, etc) so the variant is shareable and reload-stable. If page code measures layout once on mount, remount the page on each switch so it measures the new variant.
- Keyboard: `←` and `→` arrow keys also cycle. Don't intercept arrow keys when an `<input>`, `<textarea>`, `[contenteditable]`, or horizontally scrolling element is focused.
- Visually distinct from the page through a plain border and label, so it's obviously not part of the design being evaluated.

Put the switcher in a single shared component so both sub-shapes can reuse it.

### 5. Hand it over

Commit the variants on the scratch branch. Build a self-contained HTML gallery from the theme and width check screenshots. Give it one view per variant, including `current`, cycled with the same arrows and keys. Each view shows light and dark at desktop and phone width. Inline the images as JPEG data URIs. Deliver the gallery through [t3-preview](../t3-preview/SKILL.md)'s direct HTML route. In the static fallback, deliver that file instead.

Add a live URL only when the question depends on motion or interaction and the user's browser can reach the scratch app without a public tunnel. Otherwise, stop the scratch app. Report the gallery link, the `?variant=` keys with any live URL, the checks performed, and any unverified visual behavior. Stop for the user's selection before editing real components. The user will flip through whenever they get to it. The interesting feedback is usually **"I want the header from B with the sidebar from C"**, which is the actual design they want.

### 6. Capture the answer and clean up

Once a variant has won, capture the answer (which variant and why), then capture the prototype the way the [SKILL](SKILL.md) describes. Record any combination the user chose across variants. Hand the selected design to [implement](../implement/SKILL.md) for authorized production work. The scratch branch stays the primary source for the alternatives. It does not become production code.

## Anti-patterns

- **Variants that differ only in colour or copy.** That's a tweak, not a prototype. Real variants disagree about structure.
- **Sharing too much code between variants.** A shared `<Header>` is fine; a shared `<Layout>` defeats the point. Each variant should be free to throw out the layout.
- **Wiring variants to real mutations.** Read-only prototypes are fine. If a variant needs to mutate, point it at a stub: the question is "what should this look like", not "does the backend work".
- **Promoting the prototype directly to production.** The variant code was written under prototype constraints (no tests, minimal error handling). Rewrite it properly when you fold it in.
