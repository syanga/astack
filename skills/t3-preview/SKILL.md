---
name: t3-preview
description: Previewing HTML in T3 Code. Use when delivering a page or mock for the user to view, or when a preview needs a backend server or browser automation.
---

# T3 preview

Pick the delivery route before building anything. The content itself follows the user's existing design and implementation workflow. A request to explain preview options needs only an answer.

For either delivery route, keep the [preview cleanup record](cleanup.md) with the task's output and link it in the handoff. It identifies files and processes for later worktree cleanup.

## Where the browser runs

The user usually works in T3 Code through a Cloudflare tunnel. T3's preview browser runs on the **viewing device**, which can be a different machine from the one the agent runs on. In that browser, `localhost` is the viewing device, so a server on the agent's machine is unreachable at a localhost address. The only public route from the agent's machine is a Cloudflare quick tunnel.

## Self-contained HTML (default)

For static pages and interactive mocks with no backend, write one `.html` file with CSS and JavaScript inline, and link its absolute path in the reply, for example `[Open the page](/tmp/demo/index.html)`.

T3's file panel renders the file and runs its scripts over the existing connection, so no server or tunnel is needed. A file outside the workspace cannot load sibling files, so keep every asset inline. The `preview_*` tools drive the browser tab, not the file panel. When you need to click through or screenshot the page yourself, take the server route in [`tunnel.md`](tunnel.md). Otherwise report the link, and say which checks were done on the source and which visual checks remain for the user.

## Server and tunnel

A page that needs a backend, a dev server, or browser automation goes through a local server plus a public Cloudflare tunnel. Follow [`tunnel.md`](tunnel.md). The tunnel URL is public, so serve only the preview directory.
