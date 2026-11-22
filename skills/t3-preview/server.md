# Server previews

T3's browser panel loads `localhost` URLs from the agent machine in the desktop, web, and mobile apps.

1. Serve only the preview directory, a prototype's scratch app, or the task's dev server. Use task-specific logs and record each process in the [cleanup record](cleanup.md). For static files on macOS, use the firewall-approved `/usr/bin/python3`.
2. When the `preview_*` tools are available, verify in a background [preview tab](SKILL.md#preview-tabs) that the page loads and that its interactions work.
3. Show the user the page by giving its `localhost` URL as a link.
4. Leave the preview processes running while the user inspects.

In T3 Code, if the user says the link opened their own browser and didn't load, tell them to set T3's **Open links in** setting to **T3 Code**. Also tell them to click the link without ⌘ or Ctrl.

If the user asks for a public link, follow [Create a public link](#create-a-public-link).

## Create a public link

For content suitable for public access, with no secrets in the served app and never for a prototype's scratch app, run `cloudflared tunnel --url http://localhost:<port>` and use the HTTPS URL from its log. Record the tunnel process in the cleanup record. DNS may take several minutes to resolve. Before you report the preview as ready, verify that the HTTPS URL loads.

If `cloudflared` is unavailable or the content cannot be public, tell the user why no public link is available.
