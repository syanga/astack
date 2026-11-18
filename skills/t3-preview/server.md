# Server previews

In T3 Code, `preview_open` opens a URL in a preview tab. The tab's browser runs on the agent machine, so it loads `localhost` URLs, and T3 streams the tab to the user's device. By default, a `localhost` link in chat opens in the user's own browser. That browser can't reach the server when the user is on another device.

1. Serve only the preview directory, a prototype's scratch app, or the task's dev server. Use task-specific logs and record each process in the [cleanup record](cleanup.md). For static files on macOS, use the firewall-approved `/usr/bin/python3`.
2. Hand off the page:
   - In T3 Code with the `preview_*` tools, open the page with `preview_open`. Verify loading and interactions in that tab. Reset any `preview_resize` or `preview_set_appearance` override, then point the user to the tab. T3 closes an agent's tab after a period without a viewer. Also give the URL in code font so the user can reopen it in T3's browser panel.
   - In T3 Code without the `preview_*` tools, give the URL in code font for T3's browser panel.
   - Outside T3 Code, give the `localhost` URL.

   If the user asks for a public link, follow [Create a public link](#create-a-public-link).
3. Leave the preview processes running, and any preview tab open, while the user inspects.

## Create a public link

For content suitable for public access, with no secrets in the served app and never for a prototype's scratch app, run `cloudflared tunnel --url http://localhost:<port>` and use the HTTPS URL from its log. DNS may take several minutes to resolve. Verify that the HTTPS URL loads before reporting the preview as ready.

If `cloudflared` is unavailable or the content cannot be public, tell the user why no public link is available.
