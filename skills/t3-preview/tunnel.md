# Server and tunnel

Steps for a preview that needs a backend, a dev server, or browser automation. Use task-specific log files so simultaneous previews do not overwrite each other, and record the PID of every process you start.

1. **Pick a free port.** Done when `lsof -nP -iTCP:<port> -sTCP:LISTEN` reports nothing. If it reports a listener, choose another port.
2. **Start the server.** Serve only the preview directory, because the tunnel URL will be public. For static files on macOS use the system Python, which is already firewall-approved:
   `nohup /usr/bin/python3 -m http.server <port> --directory <dir> > <server-log> 2>&1 &`
   Elsewhere use the available Python 3 or the project's dev server. Done when `curl -s http://localhost:<port>` returns the page.
3. **Start the tunnel.** `nohup cloudflared tunnel --url http://localhost:<port> > <tunnel-log> 2>&1 &`. Done when the log contains the `https://<name>.trycloudflare.com` URL.
4. **Wait for DNS and HTTPS.** A new tunnel name can take several minutes to resolve. Check with `dig +short @1.1.1.1 <name>.trycloudflare.com`, then `curl -sI https://<name>.trycloudflare.com/`. If DNS is still unresolved after about five minutes, stop that tunnel and start one replacement. If the replacement also fails, report a blocker. If the local resolver has cached a miss, `curl --resolve <name>.trycloudflare.com:443:<ip> https://<name>.trycloudflare.com/` diagnoses it, but that proves reachability from the agent's machine only, not from the viewing device. Report the preview as ready only after both checks pass.
5. **Drive it or hand it off.** With T3 preview tools, pass the full public URL to `preview_navigate` as `url`, then use `preview_click`, `preview_type`, and `preview_snapshot`. Without browser tools, give the user the URL and name the interaction and visual checks that remain unverified.
6. **Keep it up, then clean up.** Leave both processes running while the user inspects. Put the URL and the exact `kill <server-pid> <tunnel-pid>` in the handoff. When the preview is no longer needed, stop only the processes you started and report that they are stopped.

## When the tunnel route is unavailable

`cloudflared` is needed only for this route. When it is absent, or a public URL is unsuitable for the content, deliver self-contained HTML when it covers the need. Otherwise report that a reachable server is still required, and ask the user which preview channel to use. Never pick a preview channel yourself. A static file verifies nothing about the backend or the browser, so name those checks as still open.
