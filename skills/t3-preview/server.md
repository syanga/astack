# Server previews

In T3 Code, `preview_open` opens a URL in a preview tab. The tab's browser runs on the agent machine, so it loads `localhost` URLs. T3 streams the tab to the user's device. On web and mobile, T3's browser panel also loads URLs on the agent machine. In the desktop app connected to another machine, a tab the user opens in the browser panel loads on the user's device. **Open in `<environment>`'s browser** moves that tab to the agent machine. By default, a `localhost` link in chat opens in the user's own browser. That browser can't reach the server when the user is on another device.

1. Serve only the preview directory, a prototype's scratch app, or the task's dev server. Use task-specific logs and record each process in the [cleanup record](cleanup.md). For static files on macOS, use the firewall-approved `/usr/bin/python3`.
2. Hand off the page:
   - In T3 Code, do both of these:
     - With the `preview_*` tools, open the page in a preview tab with `preview_open`. Verify loading and interactions in that tab. Reset any `preview_resize` or `preview_set_appearance` override. Then point the user to the tab.
     - Give the URL for T3's browser panel, so the user can reopen the page after T3 closes a preview tab that had no viewer. Use this sentence. Fill in `<URL>`, and replace `<environment>` with the `label` that `t3_environment_read` returns, or with the output of `hostname` if you lack that tool:

       ```text
       To reopen the page, paste `<URL>` into T3's browser panel. In the desktop app, then choose **Open in <environment>'s browser**.
       ```
   - Outside T3 Code, give the `localhost` URL.

   If the user asks for a public link, follow [Create a public link](#create-a-public-link).
3. Leave the preview processes running, and any preview tab open, while the user inspects.

## Create a public link

For content suitable for public access, with no secrets in the served app and never for a prototype's scratch app, run `cloudflared tunnel --url http://localhost:<port>` and use the HTTPS URL from its log. DNS may take several minutes to resolve. Verify that the HTTPS URL loads before reporting the preview as ready.

If `cloudflared` is unavailable or the content cannot be public, tell the user why no public link is available.
