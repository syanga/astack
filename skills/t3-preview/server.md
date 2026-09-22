# Server previews

Use task-specific log files and update the [cleanup record](cleanup.md) for every process you start.

T3's preview browser runs on the viewing device. For a remote connection, `localhost` refers to that device unless a port forward connects it to the server. The HTML file panel uses T3's existing connection, but that does not make dev-server ports reachable.

1. **Start the server on a free port.** Serve only the preview directory. For static files on macOS use the system Python, which is already firewall-approved:
   `nohup /usr/bin/python3 -m http.server <port> --directory <dir> > <server-log> 2>&1 &`
   Elsewhere use the available Python 3 or the project's dev server. Done when `curl -s http://localhost:<port>` returns the page.
2. **Choose a reachable URL.** Use an existing route such as a forwarded port or a reachable private-network address. If no existing route reaches the preview server, follow [Cloudflare fallback](#cloudflare-fallback).
3. **Drive it or hand it off.** With T3 preview tools, verify loading and interactions in the viewing browser. Give the user the URL and name reachability, interaction, and visual checks that remain unverified.
4. **Keep it up, then clean up.** Leave the preview processes running while the user inspects. When the preview is no longer needed and cleanup is authorized, follow [preview cleanup](cleanup.md).

## Cloudflare fallback

The tunnel URL is public. Use this fallback only when public access is suitable for the preview content.

1. **Start the tunnel.** `nohup cloudflared tunnel --url http://localhost:<port> > <tunnel-log> 2>&1 &`. Done when the log contains the `https://<name>.trycloudflare.com` URL.
2. **Wait for DNS and HTTPS.** A new tunnel name can take several minutes to resolve. Check with `dig +short @1.1.1.1 <name>.trycloudflare.com`, then `curl -sI https://<name>.trycloudflare.com/`. If DNS is still unresolved after about five minutes, stop that tunnel and start one replacement. If the replacement also fails, report a blocker. If the local resolver has cached a miss, `curl --resolve <name>.trycloudflare.com:443:<ip> https://<name>.trycloudflare.com/` diagnoses it. After both checks pass, return to the browser checks in step 3.

## When the tunnel route is unavailable

When `cloudflared` is absent, or a public URL is unsuitable for the content, deliver self-contained HTML when it covers the need. Otherwise report that a reachable server is still required, and ask the user which preview channel to use. A static file verifies nothing about the backend or the browser, so name those checks as still open.
