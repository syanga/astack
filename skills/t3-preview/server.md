# Server previews

T3's preview browser runs on the viewing device, so `localhost` there may not reach the agent's server.

1. Serve only the preview directory. Use task-specific logs and record each process in the [cleanup record](cleanup.md). For static files on macOS, use the firewall-approved `/usr/bin/python3`.
2. Use an already reachable URL. Otherwise follow [Cloudflare fallback](#cloudflare-fallback).
3. With T3 preview tools, verify loading and interactions in the viewing browser. Without those tools, report browser reachability as unverified. Hand off the URL and leave the preview processes running while the user inspects.

## Cloudflare fallback

For content suitable for public access, run `cloudflared tunnel --url http://localhost:<port>` and use the HTTPS URL from its log. DNS may take several minutes to resolve. Verify HTTPS and the viewing-browser checks above before reporting the preview as ready.

If `cloudflared` is unavailable or the content cannot be public, use direct HTML only if it meets the task. Otherwise report the missing preview route.
