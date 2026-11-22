---
name: t3-preview
description: Previewing HTML in T3 Code. Use when delivering a page or mock for the user to view, or when a preview needs a backend server or browser automation.
---

# T3 preview

## Direct HTML preview (default)

For static pages and interactive mocks with no backend, write an HTML file and link its absolute path in the reply, for example `[Open the page](/tmp/demo/index.html)`.

T3's file panel renders HTML and runs its scripts over the existing connection, including remote connections. No server or separate tunnel is needed. Files inside the workspace can load sibling assets. For files outside the workspace, keep every asset inline.

## Preview tabs

The `preview_*` tools drive preview tabs in T3's browser panel, not the file panel. T3 streams preview tabs from the agent machine, and they lag on a remote connection. Use preview tabs only for your own checks. The user views and uses the page through the link you give them.

To check a page in a preview tab, follow these steps:

1. To open a background tab of your own, call `preview_open` with `open: false` and `reuseExistingTab: false`.
2. Run the checks in that tab, including each interaction the user named. Pass the returned `tabId` to each `preview_*` call.
3. Before you reply, call `t3_preview_close` on each tab you opened, even if the user wants to use the page.

If a preview tab can't load the page, close it and run the checks with Playwright.

## Server previews

For a backend, dev server, or public link, follow [server previews](server.md).

For direct HTML and server previews, report unverified visual, interaction, or backend checks and link the [preview cleanup record](cleanup.md).

## Viewport and theme checks

Take screenshots in light and dark at phone (375px) and desktop (1280px or wider) widths. Pass explicit widths to `preview_resize` or Playwright's `viewport`. Before each screenshot, check `window.innerWidth`, because a preset or the browser panel can give a different width. Capture the full page, or scroll the changed element into view. Look at every screenshot, because DOM measurements miss rendered details such as a date input's placeholder.

In a [preview tab](#preview-tabs), use `preview_resize` and `preview_set_appearance`. Otherwise, install Playwright outside the project lockfile and use its `viewport`, `isMobile`, and `colorScheme` options. Headless Chrome's `--window-size` does not narrow the layout below about 500px.

Each preview screenshot also returns the page's accessibility tree and logs. Capture large screenshot sets with Playwright, and use a preview tab for interaction and spot checks.

Set each `prefers-color-scheme` value before load, or reload after changing it, because scripts and canvas scenes often read it once. If the product sets its theme with a class or stored setting, switch it through the product's own toggle. Before trusting a canvas screenshot, confirm that the headless browser created a WebGL context. If it did not, relaunch with software GL (`--use-angle=swiftshader --enable-unsafe-swiftshader`) or run headed.
