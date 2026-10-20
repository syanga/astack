# T3 host status protocol

These application APIs can change when T3 Code updates. The command reads each
host's advertised orchestration protocol version and sends it as the
`orchestrationProtocol` query parameter when opening the WebSocket.

- [HostResourcesSnapshot](https://github.com/pingdotgg/t3code/blob/main/packages/contracts/src/resourceTelemetry.ts)
  defines CPU utilization as a fraction and memory in bytes.
- [HostResources](https://github.com/pingdotgg/t3code/blob/main/apps/server/src/resourceTelemetry/HostResources.ts)
  samples whole-host CPU over 200 milliseconds and caches results for five seconds.
- [RPC authorization](https://github.com/pingdotgg/t3code/blob/main/apps/server/src/auth/RpcAuthorization.ts)
  grants `server.getHostResources` to `orchestration:read` sessions.
- [Relay contracts](https://github.com/pingdotgg/t3code/blob/main/packages/contracts/src/relay.ts)
  define `GET /v1/environments`, the DPoP client exchange, and environment bootstrap.
- [T3 Connect internals](https://github.com/pingdotgg/t3code/blob/main/docs/internals/t3-connect.md)
  describe account discovery and proof-bound host access.

The utility reads the desktop's Clerk client credential, requests a relay JWT from
the active account, and lists linked environments. It exchanges the relay JWT for
a DPoP credential, bootstraps a read session on each remote host, and requests a
one-use WebSocket ticket. The ticket authenticates the `server.getHostResources`
request on `/ws`. The local machine instead uses the matching T3 CLI to issue and
revoke a scoped bearer session.

The [relay token exchange](https://github.com/pingdotgg/t3code/blob/main/infra/relay/src/http/Api.ts)
requires a Clerk JWT with the relay audience. T3 CLI OAuth credentials can list
environments but lack that audience, so they cannot replace the desktop credential
for remote host access.

Python handles HTTP and WebSocket transport. Node's crypto library handles
Electron's macOS Safe Storage AES envelope and P-256 DPoP signatures. Private keys
and credentials cross the crypto subprocess boundary through stdin and captured
stdout. The utility does not save them to disk.
