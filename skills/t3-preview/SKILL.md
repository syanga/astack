---
name: t3-preview
description: T3 Code previews and browser checks. Use when delivering HTML or mocks, previewing a backend server, verifying an existing website, or troubleshooting the shared browser.
---

# T3 preview

## Direct HTML preview (default)

For static pages and interactive mocks with no backend, write an HTML file and link its absolute path in the reply, for example `[Open the page](/tmp/demo/index.html)`.

T3's file panel renders HTML and runs its scripts over the existing connection, including remote connections. No server or separate tunnel is needed. Files inside the workspace can load sibling assets. For files outside the workspace, keep every asset inline.

The `preview_*` tools drive the browser tab, not the file panel.

## Existing websites and browser checks

For an existing URL or shared-browser troubleshooting, follow [browser checks](browser.md). Use the existing URL directly.

## Server previews

For a backend or dev server that needs a preview URL, follow [server previews](server.md).

Report unverified visual, interaction, or backend checks. For artifacts or processes you create, link the [preview cleanup record](cleanup.md).
