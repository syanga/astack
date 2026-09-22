---
name: t3-preview
description: Previewing HTML in T3 Code. Use when delivering a page or mock for the user to view, or when a preview needs a backend server or browser automation.
---

# T3 preview

Pick the delivery route before building anything. The content itself follows the user's existing design and implementation workflow. A request to explain preview options needs only an answer.

For either delivery route, keep the [preview cleanup record](cleanup.md) with the task's output and link it in the handoff. It identifies files and processes for later worktree cleanup.

## Direct HTML preview (default)

For static pages and interactive mocks with no backend, write one `.html` file with CSS and JavaScript inline, and link its absolute path in the reply, for example `[Open the page](/tmp/demo/index.html)`.

T3's file panel renders HTML and runs its scripts over the existing connection, including remote connections. No server or separate tunnel is needed. Files inside the workspace can load sibling assets. For files outside the workspace, keep every asset inline.

Report the file link and distinguish source checks from visual and interaction checks. The `preview_*` tools drive the browser tab, not the file panel. If you need browser automation, follow [server previews](server.md).

## Server previews

For a backend, dev server, or browser automation, follow [server previews](server.md). Use an address reachable from the viewing device. Use a Cloudflare quick tunnel only when no existing route reaches the preview server.
