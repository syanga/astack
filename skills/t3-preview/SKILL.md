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

## Viewport and theme checks

Use `preview_resize` and `preview_set_appearance` on the browser route. Without that route, use Playwright's `viewport`, `isMobile`, and `colorScheme` options, installed outside the project lockfile. Headless Chrome's `--window-size` does not narrow the layout below about 500px. Each preview screenshot also returns the page's accessibility tree and logs, so capture a full variant matrix with Playwright and use the preview browser for interaction and spot checks.

Set the color scheme before load, or reload after changing it, because scripts and canvas scenes often read it once. If the product sets its theme with a class or stored setting, switch it through the product's own toggle. Before trusting a canvas screenshot, confirm that the headless browser created a WebGL context. If it did not, relaunch with software GL (`--use-angle=swiftshader --enable-unsafe-swiftshader`) or run headed.

