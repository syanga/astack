# UI Prototype

Generate **several radically different UI variations** in a self-contained HTML file, switchable from a floating bottom bar. The user flips between variants in the browser, picks one (or steals bits from each), then throws the rest away.

If the question is about logic/state rather than what something looks like, this is the wrong branch. Use [LOGIC.md](LOGIC.md).

## When this is the right shape

- "What should this page look like?"
- "I want to see a few options for this dashboard before committing."
- "Try a different layout for the settings screen."
- Any time the user would otherwise spend a day picking between three vague mockups in their head.

## Two sub-shapes: strongly prefer sub-shape A

A UI prototype is much easier to judge in the context of the rest of the app: the header, sidebar, representative data, and density. Reproduce that context in a static mock using sample data. Keep the mock in a scratch directory, separate from real components. Default to sub-shape A whenever there's a plausible existing page to represent.

### Sub-shape A: adjustment to an existing page (preferred)

Reproduce the existing page around the proposed change. Switch static variants with a `?variant=` URL search param. Represent data fetching and auth states with fixtures; keep the real route unchanged.

If the prototype is for something that doesn't yet have a page but *would naturally live inside one* (a new section of the dashboard, a new card on the settings screen, a new step in an existing flow), it's still sub-shape A. Show the variants inside a static copy of the host page.

### Sub-shape B: a new page (last resort)

Only use this when the thing being prototyped genuinely has no existing page to live inside (e.g. an entirely new top-level surface, or a flow that can't be embedded anywhere sensible).

Create a **throwaway HTML page** in the scratch directory. Name it so it's obviously a prototype (e.g. include the word `prototype` in the filename). Use the same `?variant=` pattern.

Before committing to sub-shape B, sanity-check: is there really no existing page to represent around the change? An empty page hides design problems that a populated one would expose.

In both sub-shapes the floating bottom bar is identical.

## Process

### 1. State the question and pick N

Default to **3 variants**. More than 5 stops being radically different and starts being noise, so cap there.

Write down the plan in one visible line in the prototype:

> "Three variants of the settings page, switchable via `?variant=`, in a static copy of the settings page."

This works whether the user is here to push back or not.

### 2. Generate radically different variants

Draft each variant. Hold each one to:

- The page's purpose and the data it has access to.
- The project's visual context, reproduced with inline HTML/CSS/JS.
- A clear variant name, e.g. `A`, `B`, `C`.

Variants must be **structurally different**: different layout, different information hierarchy, different primary affordance, not just different colours. Three slightly-tweaked card grids isn't a UI prototype, it's wallpaper. If two drafts come out too similar, redo one with explicit "do not use a card grid" guidance.

Use the user's visual constraints: true black background, white primary text, dense layout, minimal copy, no decorative card or pill chrome, and no light-gray subtitle lines above sections. Avoid continuously repainting animations.

### 3. Wire them together

Create a single switcher in the static HTML file:

```javascript
function showVariant() {
  const variant = new URLSearchParams(location.search).get('variant') ?? 'A';
  for (const section of document.querySelectorAll('[data-variant]')) {
    section.hidden = section.dataset.variant !== variant;
  }
}
showVariant();
```

Each variant has its own section and layout. For sub-shape A, keep the surrounding page context consistent. For sub-shape B, each section represents the proposed page.

### 4. Build the floating switcher

A small fixed-position bar at the bottom-centre of the screen with three pieces:

- **Left arrow**: cycles to the previous variant (wraps around).
- **Variant label**: shows the current variant key and, if the variant exports a name, that name too. e.g. `B (Sidebar layout)`.
- **Right arrow**: cycles forward (wraps around).

Behaviour:

- Clicking an arrow updates the URL search param (use `history.replaceState` and rerender the selected section) so the variant is shareable and reload-stable.
- Keyboard: `←` and `→` arrow keys also cycle. Don't intercept arrow keys when an `<input>`, `<textarea>`, or `[contenteditable]` is focused.
- Visually distinct from the page through a plain border and label, so it's obviously not part of the design being evaluated.
- Confined to the scratch artifact, with no production route or component edits.

Use one switcher for all variants in the HTML file.

### 5. Hand it over

Deliver through [t3-preview](../t3-preview/SKILL.md). Report the link (and the `?variant=` keys), checks performed, and any unverified visual behavior. Stop for the user's selection before editing real components. The user will flip through whenever they get to it. The interesting feedback is usually **"I want the header from B with the sidebar from C"**, which is the actual design they want.

### 6. Capture the answer and clean up

Once a variant has won, capture the answer (which variant and why), then capture the prototype the way the [SKILL](SKILL.md) describes. Record any combination the user chose across variants. Hand the selected design to [implement](../implement/SKILL.md) for authorized production work. The scratch artifact remains the primary source for the alternatives; it does not become production code.

## Anti-patterns

- **Variants that differ only in colour or copy.** That's a tweak, not a prototype. Real variants disagree about structure.
- **Sharing too much code between variants.** A shared `<Header>` is fine; a shared `<Layout>` defeats the point. Each variant should be free to throw out the layout.
- **Wiring variants to real mutations.** Read-only prototypes are fine. If a variant needs to mutate, point it at a stub: the question is "what should this look like", not "does the backend work".
- **Promoting the prototype directly to production.** The variant code was written under prototype constraints (no tests, minimal error handling). Rewrite it properly when you fold it in.
