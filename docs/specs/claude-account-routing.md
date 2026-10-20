# Claude subscription account routing

Status: agreed behavior, with blocking feasibility probes before full implementation.

## Problem Statement

The current `claude-accts`, `claude-use`, and related commands switch global
Claude credentials on one Mac. Saved credentials, live credentials, running
clients, and the credential mirror can disagree. The helper has no coordinated
quota policy and does not provide a portable account router.

The user has several Claude subscriptions and wants to use their included
allowance across terminal Claude, T3 Code, and SSH sessions on macOS and Linux.
Manual account changes interrupt work and can create unnecessary prompt-cache
writes. Routing must preserve ongoing conversations, use allowance before it
resets, and avoid automatic paid overflow.

## Solution

An optional astack component runs one local proxy on each machine. Each proxy
has independent logins for the user's subscription accounts and one account
pool. Claude clients use that proxy as their default connection after acceptance
tests pass.

The proxy assigns a new conversation according to account capacity and observed
quota. It favors unused allowance approaching reset without moving healthy
conversations. A conversation keeps its account across idle periods and proxy
restarts. Subagents inherit their parent's account.

Confirmed quota exhaustion permits automatic migration within the same
conversation. Authentication failures and transient errors have distinct recovery
behavior. When every eligible account is exhausted, the system reports the
earliest usable reset and waits automatically with cancellation.

Command-line status explains quota observations, assignments, interruptions,
migrations, and cache usage. An offline simulator exercises the production
policy. Bounded live tests establish client compatibility and actual cache costs.

## User Stories

1. As a user with several subscriptions, I want new conversations spread according to capacity and reset times, so that included allowance does not expire unused.
2. As a user continuing a conversation, I want its account assignment to survive idle periods and proxy restarts, so that routing does not cause unnecessary cache writes.
3. As a T3 Code user, I want quota exhaustion to change the account within my existing thread, so that I retain messages, tool results, and working context.
4. As a user running subagents, I want them to inherit their parent's account, so that related work follows the same assignment policy.
5. As a subscription user, I want paid overflow disabled and no automatic paid fallback, so that routing consumes only included allowance.
6. As a user encountering a transient failure, I want recovery on the same account, so that a temporary error does not cause account churn.
7. As a user whose response was interrupted, I want to retry explicitly without repeating completed tool actions, so that recovery preserves my work.
8. As a user whose accounts are exhausted, I want the system to report when useful capacity returns and wait with cancellation, so that I can resume without repeated manual checks.
9. As a user whose login needs renewal, I want a clear reauthentication status, so that I can restore access without silently moving existing conversations.
10. As a user working on several machines, I want independent local proxies and logins, so that one machine does not depend on another machine's availability.
11. As a user diagnosing routing, I want command-line status and persistent events, so that I can understand account selection and cache costs without inspecting credentials.
12. As an astack maintainer, I want deterministic simulation and isolated live acceptance, so that I can verify routing before making it the default.

## Implementation Decisions

### Domain terms

The component uses the following terms consistently. No existing project domain
glossary or applicable ADR was found during repository inspection.

| Term | Meaning |
| --- | --- |
| Subscription account | A Claude account with included allowance and account-wide usage settings. |
| Credential | A machine-local OAuth login for a subscription account. Refreshing it does not create a new account. |
| Conversation | A continuing client thread with messages, tool results, and context. Its assignment is independent of process lifetime and requested model. |
| Request | One inference attempt within a conversation. A conversation can have concurrent requests. |
| Subagent | A child conversation or execution whose account derives from a known parent conversation. |
| Assignment | The durable relationship between a conversation and a subscription account. |
| Migration | An explicit change of an existing assignment, caused by confirmed quota exhaustion or manual override. |
| Quota observation | Reported utilization, reset times, applicable limits, and the observation time. It can be stale or incomplete. |
| Usable reset | A reset after which all known quota constraints blocking the requested model can permit work on an otherwise eligible account. |

### Runtime and client integration

- Build a Go service that embeds a pinned CLIProxyAPI SDK. Reuse upstream OAuth
  refresh and request transport. Maintain custom routing policy without an
  upstream fork.
- The component owns durable assignments, quota interpretation, account
  selection, retry and migration decisions, simulation, status, and event records.
- Run one proxy per machine with one enrolled account pool. Each machine obtains
  fresh, independent OAuth logins. Do not import rotating credential snapshots
  from the existing switcher or share refresh tokens between machines.
- Support terminal Claude, T3 Code, and Claude use through SSH on that machine.
  Bind the proxy to loopback and require a private client authentication token.
  Public network access is outside this design.
- Integrate through the clients' supported connection configuration. Preserve
  the requested model and the existing T3 thread. Remote Control is not required
  for T3 Code and is outside scope.
- Preserve cacheable prompt prefixes, tool definitions, cache controls, and
  conversation meaning. Allow required account-specific authentication metadata
  changes. Universal byte-identical request bodies are not a requirement.

### Included allowance

- Enroll only accounts with account-wide paid overflow disabled. This includes
  the setting described as usage credits or extra usage.
- Never enable paid overflow, route to an API-paid fallback, or bypass the proxy
  automatically when included allowance is exhausted.
- A login proves access to an account, not its paid-overflow setting. The
  verification mechanism is a blocking feasibility question.
- Manual attestation and an alert after a paid response cannot establish a hard
  pre-request guarantee. Acceptance must state what prevents a paid request if
  the account setting changes outside the proxy.

### Placement of new conversations

- With comparable quota conditions, distribute new conversations in proportion
  to subscription capacity. Account for workload already assigned rather than
  treating every arriving conversation as the only work in progress.
- Apply a soft preference for unused allowance approaching reset. Account for
  five-hour limits, weekly limits, and model-specific limits when available.
  A near five-hour reset does not make an account usable if a weekly limit still
  blocks the requested model.
- Apply reset urgency only to new assignments. Do not migrate a healthy
  conversation to rebalance accounts or drain allowance before reset.
- Use fresh observations for reset urgency. With missing or stale observations,
  fall back to capacity weights and known exhaustion information. Expose the
  observation age rather than inventing precision.
- Treat traffic from other machines and direct clients as external workload.
  Local proxies share subscription limits but do not coordinate assignments.
- The exact capacity weights, freshness threshold, workload estimate, and reset
  preference function remain implementation proposals. Simulation must compare
  the chosen rule against capacity-only placement.

### Durable assignments

- Capture the original conversation identity before account-specific request
  transformations. Establish parent relationships for subagents from observed
  client behavior.
- Create the first assignment atomically. Concurrent first requests for one
  conversation must obtain the same account. Persist the assignment before
  dispatch so a crash cannot silently create a different assignment on restart.
- Keep assignments across idle periods and proxy restarts. A model change within
  a conversation does not create a new assignment.
- Pin each dispatched request to the account selected by the component. SDK
  defaults, configuration reloads, and client retries must not introduce hidden
  account selection or migration.
- A subagent inherits the parent's current account. A required parent migration
  must not turn later child requests into independent placement decisions.
  Already dispatched requests retain their pinned account.
- Automatically change an assignment only after confirmed quota exhaustion.
  Provide an explicit manual override. A migrated conversation does not
  automatically return to its original account when that account recovers.
- Missing conversation or parent identity is a blocking compatibility question.
  Per-request balancing is not an acceptable fallback. A fixed assignment or
  rejection is a proposal to evaluate in the feasibility probes.

### Failure and recovery behavior

The routing contract distinguishes these outcomes.

| Condition | Required behavior |
| --- | --- |
| Transient network or provider failure before response output | Retry within a bounded policy on the assigned account. Preserve the assignment. |
| Confirmed quota exhaustion before response output | Record exhaustion and migrate to an eligible account. Exclude accounts already exhausted for this request. |
| Failure after response output reaches the client | Report the interruption and preserve conversation state. Require explicit retry. Record confirmed exhaustion so the next explicit attempt can use the migration policy. |
| Normal OAuth expiry | Refresh automatically using the machine-local credential. |
| Invalid or revoked login requiring browser reauthentication | Exclude the account from new assignments. Keep existing assignments and report the need to log in again or move explicitly. |
| All eligible accounts exhausted | Report the earliest known usable reset, wait automatically with cancellation, and avoid inference attempts against known exhausted accounts before their reset. |
| Unknown reset time | Report uncertainty rather than promise an exact resume time. The recovery mechanism must be established by the waiting probe. |
| Proxy failure | Report the outage, restart the local service automatically, and recover assignments. Do not silently connect directly. |

- A generic rate-limit response alone does not establish subscription exhaustion.
  Distinguish quota exhaustion from transient throttling and authentication errors
  using verified provider signals.
- The component controls its retries and migrations. End-to-end acceptance must
  also establish how terminal Claude and T3's SDK retry interrupted requests.
- Never automatically replay completed tool actions. Preserve streaming
  backpressure and cancellation. The exact boundary for delivered response
  output must be established by client probes.
- Compute the earliest usable reset across otherwise eligible accounts for the
  requested model. Do not choose an earlier reset that leaves another known limit
  blocking work. Reassess observations when the reset arrives.
- Automatic waiting is a system behavior. It does not require holding a single
  HTTP connection open for hours or days. Its client protocol remains a blocking
  feasibility question.
- Service supervision must bound crash loops and preserve durable state. Direct
  recovery is available only through an explicit user action.

### Status and data

- Command-line status shows enrolled accounts, capacity weights, authentication
  state, observed utilization, applicable resets, freshness, and known exhaustion.
- Show conversation assignments, waits, interruptions, migration reasons, and
  proxy health. Distinguish observed values from estimates.
- Keep persistent routing events and per-account cache creation and cache read
  counters when the upstream response supplies them. Missing counters mean
  unknown usage, not zero usage.
- Diagnostics contain no OAuth tokens, client authentication tokens, prompts,
  or tool contents. Credentials and service state use private local storage.
- Choose the durable storage format after crash and concurrency evaluation.
  Database storage and an append-only event log are proposals, not settled
  implementation decisions.

### astack packaging and cutover

- Installation is optional and portable across macOS and Linux. It adds a
  compiled Go runtime without changing astack's Python convention for ordinary
  installer scripts.
- Follow the existing installer's preview, conflict detection, managed settings,
  update, and restoration behavior. Preserve unrelated configuration and respect
  supported configuration-directory overrides.
- Keep proxy credential storage separate from native Claude credentials and the
  old switcher's snapshots. Establish actual coupling before retiring a legacy
  mirror or helper.
- After offline and bounded live acceptance pass, make the proxy the default
  Claude connection immediately. This order is agreed, and does not require a
  second product decision about whether to cut over.
- Define reversible client wiring and legacy-helper coordination before cutover.
  Explicit direct recovery must restore a usable connection without deleting
  proxy assignments or credentials.

## Testing Decisions

### Behavioral boundaries

- Test through the proxy's client-facing behavior as the highest practical
  integration boundary. Use temporary state and a controlled upstream that
  returns quota observations, failures, streamed responses, and usage counters.
- Expose one production policy transition boundary to the simulator. It accepts
  workload and observed account state, and produces routing outcomes. Its exact
  interface remains a proposal. The simulator must call production policy code,
  not a second implementation of the algorithm.
- Assert concrete selected accounts, delivered responses, durable assignments,
  wait outcomes, and diagnostic records against independent expected values.
  Prefer observable effects over internal call counts or implementation details.
- Use a deterministic clock, account fixtures, and random seed. Synthetic quota
  and cache models must state their assumptions and permit alternative models.
- Existing installer lifecycle tests provide prior art for isolated homes,
  temporary checkouts, configuration overrides, restoration, and failure recovery.
  Background-job tests provide prior art for cancellation and process cleanup.
  There is no existing account-routing test boundary in astack.
- Automated repository tests remain offline and confined to temporary directories.
  Run the required Python suite. The Go component also needs its own offline
  policy and proxy tests. Live acceptance is a separate, bounded procedure.

### Deterministic acceptance scenarios

The offline suite and simulator cover these behaviors.

| Scenario | Observable expectation |
| --- | --- |
| Equal quota conditions, unequal capacity | Over a controlled workload, new assignments reflect the configured capacity ratio. |
| Account near reset with available allowance | New work favors that account compared with the capacity-only baseline. Existing healthy assignments remain unchanged. |
| Near five-hour reset with exhausted weekly allowance | The account does not become a migration destination solely because the five-hour reset is near. |
| Parallel first requests for one conversation | Every request receives the same durable account assignment. |
| Idle period, model change, or proxy restart | The conversation retains its assignment. |
| Parent and nested subagents | Child requests inherit the parent's account and do not balance independently. |
| Transient failure before output | Recovery uses the assigned account and returns the expected response. |
| Confirmed exhaustion before output | Recovery moves to an eligible account, preserves the conversation, and records the migration reason. |
| Original account recovers after migration | The migrated conversation remains on its destination account. |
| Interrupted response or cancellation | The client observes interruption or cancellation. Completed tool results remain intact, and explicit retry can resume without automatic replay. |
| Revoked authentication | New work uses another eligible account. Existing assignments report reauthentication until relogin or explicit movement. |
| Every account exhausted | The system reports the earliest usable reset and resumes automatically when eligible capacity returns. Cancellation stops pending work. |
| Missing, stale, or externally changed quota | Status exposes uncertainty. Placement uses the fallback rule and respects known exhaustion. |
| Crash during assignment or migration | Restart recovers one committed assignment rather than creating competing assignments. |
| Proxy outage and repeated restart failure | The client receives an explicit outage. No direct bypass occurs, and restart attempts remain bounded. |

The capacity ratio and reset-preference cases require literal fixture outcomes
once the selection rule is chosen. Their numerical thresholds are not yet fixed.
Negative assertions pair with positive outcomes, such as a resumed response after
a wait or preserved tool results after an interruption.

### Simulation and cache evaluation

- Simulate varying account capacity, nested quota windows, model restrictions,
  conversation context growth, parallel requests, subagents, and external usage.
  Include stale observations, auth faults, transient throttling, cancellations,
  partial streams, and proxy restarts.
- Compare capacity-only placement with the reset-aware rule on the same workload.
  Report completed useful work, waiting time, unused allowance at reset,
  migrations, and estimated additional cache writes caused by migration.
- Model growing cacheable prefixes and cache expiry. Attribute ordinary prefix
  growth separately from a migration's cold cache write.
- Require no avoidable account churn for healthy conversations. Do not generate
  inference merely to keep a cache warm or drain allowance.
- Live validation measures `cache_creation_input_tokens` and
  `cache_read_input_tokens`. API price equivalents, if reported, remain separate
  from included subscription allowance consumption.
- Necessary cross-account migration may cause a cold cache write. Zero extra
  writes across all migrations is not a promise. Cache ownership and subscription
  quota accounting require measurement.
- Stable routing must show no systematic additional cache writes caused by the
  proxy. Establish a comparison method that accounts for cache expiry, prefix
  growth, and measurement variation before declaring acceptance.

### Bounded live acceptance

- Use isolated terminal and T3 configuration with a small included-usage budget.
  Verify paid overflow is disabled before any live inference. Do not change the
  daily default while acceptance is in progress.
- Compare direct Claude and routed Claude with equivalent context progression
  on a stable account. Record actual upstream account, original conversation
  identity, allowed request transformations, and cache creation and read counters.
  Control cache warmth and elapsed time so a direct run does not warm the cache
  in a way that hides a routed cache-write regression.
- Exercise one controlled migration. Verify the same terminal conversation or
  T3 thread retains messages, tool results, and working context. Probe thinking
  content and account-scoped artifacts where the clients use them.
- Verify resume, nested subagents, concurrency, cancellation, partial output,
  client retries, credential refresh, proxy restart, and the waiting mechanism.
- Acceptance requires assignment stability, explicit migration reasons,
  included-only operation, no automatic replay of completed tool actions, and
  no systematic cache-write increase during stable routing.
- Report the observed cold-write cost of necessary migration. Simulation
  estimates alone do not establish live cache behavior.

## Out of Scope

- A shared gateway, remote proxy management, or coordination between machines.
- Separate account pools, a graphical dashboard, or changes to T3 Code itself.
- Claude Remote Control support through the proxy.
- Paid overflow, paid API fallback, automatic direct bypass, or model substitution.
- Periodic migration of healthy conversations or background cache-warming inference.
- A maintained CLIProxyAPI fork or generalized routing for other providers.
- Exact forecasts of subscription allowance from API token prices.

## Further Notes

### Blocking feasibility probes

The product behavior above is settled. These probes resolve technical facts.
They permit isolated experiments now and block full service implementation or
default cutover where noted.

| Probe | Evidence required | Blocking scope |
| --- | --- | --- |
| Conversation and parent identity | Stable identity for terminal Claude, T3, resume, and nested subagents, captured before request rewriting. A safe policy for requests without identity. | Blocks durable routing integration. |
| Retry ownership and pinning | The pinned SDK executes only on the chosen account. Its retries, configuration lifecycle, and both clients cannot introduce hidden failover or automatic replay after partial output. | Blocks executor integration and cutover. |
| Included-only enforcement | A supported verification or enforcement mechanism for disabled paid overflow, including external setting changes. State any remaining guarantee limit explicitly. | Blocks a claim of hard enforcement and live acceptance or cutover that depends on that claim. |
| Waiting and cancellation | A client-compatible method for automatic waiting, bounded connection lifetime, reset reporting, cancellation, and resumption. Unknown reset times have explicit behavior. | Blocks exhaustion recovery and cutover. |
| Cache and context continuity | Stable routing preserves cacheable prefixes. Required migration preserves tools, thinking content, and account-scoped artifacts, with measured cache costs. | Blocks migration support and cutover. |

If a probe cannot meet the agreed behavior, report the specific incompatibility.
Do not silently weaken the requirement or substitute a different workflow.

### Remaining implementation choices and assumptions

- Exact CLI commands, service supervision details, durable storage, and installer
  option names remain proposals. They do not block feasibility probes.
- Capacity weights, freshness thresholds, reset urgency, and retry bounds need
  simulation and observed provider behavior. No arbitrary performance gain or
  cache-cost tolerance has been adopted as an acceptance threshold.
- Quota headers and observations may not expose every applicable limit. A
  subscription account's authoritative usage interface has not been established.
  Status and simulation must preserve that uncertainty.
- Each local proxy observes only part of total subscription traffic. The design
  assumes delayed observations and tests external consumption.
- The legacy switcher is not an identity or credential source for the proxy.
  The exact retirement sequence depends on client wiring and credential-store
  isolation established during implementation.

### Execution order

1. Run the blocking probes against isolated clients and the pinned SDK.
2. Choose the minimal policy and persistence interfaces. Validate selection with
   deterministic simulation and controlled upstream integration tests.
3. Add portable installation, supervision, command-line status, and reversible
   client wiring. Verify their lifecycle in temporary environments.
4. Run bounded live acceptance. If the agreed gates pass, make the local proxy
   the default and coordinate retirement of coupled legacy behavior.

### Evidence references

The design incorporates the completed Claude Opus 5.5 plan review. That review
identified feasibility gaps; it did not establish runtime compatibility.
This spec records the accepted requirements and probes rather than the review's
detailed findings or implementation history.

The inspected upstream and client revisions provide the starting point for the
probes. They are not a substitute for testing the shipped integration.

- [CLIProxyAPI inspected revision](https://github.com/router-for-me/CLIProxyAPI/tree/e2bff0107bb307337aaa19018ccddd55f64253d5).
- [T3 Code Claude provider documentation at the inspected revision](https://github.com/pingdotgg/t3code/blob/5cc99e1c2398/docs/user/providers-claude.md).
- [Claude Code authentication documentation](https://code.claude.com/docs/en/authentication).
- [Claude usage credits account settings](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans).
- [Claude prompt caching documentation](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).
- Repository testing prior art: `tests/test_install.py`,
  `tests/test_install_lifecycle.py`, and `tests/test_run_job.py`.
