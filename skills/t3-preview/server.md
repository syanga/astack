# Server previews

In the desktop, web, and mobile apps, T3's browser panel loads the agent machine's `localhost` URLs.

To hand off a server preview, follow these steps:

1. Serve only the preview directory, a prototype's scratch app, or the task's dev server. Use task-specific logs and record each process in the [cleanup record](cleanup.md). For static files on macOS, use the firewall-approved `/usr/bin/python3`.
2. When T3's `preview_*` tools are available, follow the [preview tab steps](SKILL.md#preview-tabs) to check that the page loads and that its interactions work.
3. To show the user the page, give its `localhost` URL as a link.
4. Leave the preview processes running while the user inspects.

In T3 Code, if the user says the link opened their own browser and didn't load, tell them to set T3's **Open links in** setting to **T3 Code**. Also tell them to click the link without ⌘ or Ctrl.

## Create a public link

Create a public link only when the user asks for one. The server must hold no secrets, such as private API keys or credentials. Everything it serves, including a dev server's source files, must be suitable for public access. Never create a public link for a prototype's scratch app.

To create the link, run `cloudflared tunnel --url http://localhost:<port>` and use the HTTPS URL from its log. Record the tunnel process in the cleanup record. DNS may take several minutes to resolve. Before you report the preview as ready, verify that the HTTPS URL loads.

If `cloudflared` is unavailable or the server breaks a rule above, tell the user why no public link is available. If the HTTPS URL still doesn't load after 10 minutes, stop the tunnel and tell the user that the public link didn't load.
