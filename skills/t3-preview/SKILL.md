---
name: t3-preview
description: Previewing HTML in T3 Code. Use when delivering a page or mock for the user to view, or when a preview needs a backend server or browser automation.
---

# T3 preview

## Direct HTML preview (default)

For static pages and interactive mocks with no backend, write an HTML file and link its absolute path in the reply, for example `[Open the page](/tmp/demo/index.html)`.

T3's file panel renders HTML and runs its scripts over the existing connection, including remote connections. No server or separate tunnel is needed. Files inside the workspace can load sibling assets. For files outside the workspace, keep every asset inline.

The `preview_*` tools drive the browser tab, not the file panel.

## Server previews

For a backend, dev server, or browser automation, follow [server previews](server.md).

For either route, report unverified visual, interaction, or backend checks and link the [preview cleanup record](cleanup.md).
