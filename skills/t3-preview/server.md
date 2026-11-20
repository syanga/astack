# Server previews

T3's browser loads the agent machine's `localhost` URLs on every device.

1. Serve only the preview directory, a prototype's scratch app, or the task's dev server. Use task-specific logs and record each process in the [cleanup record](cleanup.md). For static files on macOS, use the firewall-approved `/usr/bin/python3`.
2. If you have the `preview_*` tools, verify loading and interactions in a preview tab with `preview_open`. Then reset any `preview_resize` or `preview_set_appearance` override.
3. Give the user the page's `localhost` URL as a link. If the user asks for a public link, follow [Create a public link](#create-a-public-link).
4. Leave the preview processes running while the user inspects.

## Create a public link

For content suitable for public access, with no secrets in the served app and never for a prototype's scratch app, run `cloudflared tunnel --url http://localhost:<port>` and use the HTTPS URL from its log. DNS may take several minutes to resolve. Verify that the HTTPS URL loads before reporting the preview as ready.

If `cloudflared` is unavailable or the content cannot be public, tell the user why no public link is available.
