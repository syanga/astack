---
name: wayfinder
description: Plan a huge chunk of work (more than one agent session can hold) as a shared map of decision tickets on your issue tracker, and resolve them one at a time until the way to the destination is clear.
disable-model-invocation: true
---

A large effort has unresolved decisions that span several sessions. This skill
records them as a **shared map** and **decision tickets** on the repo's issue
tracker. Each ticket resolves a question. The map is complete when the decisions
needed to reach the destination are settled.

Name the destination before creating tickets. It fixes the scope and may be a
specification, a design decision, or an authorized change such as a data-structure
migration. The map can support engineering work, course content, or other domains.

## Plan, don't do

Wayfinder is **planning** by default: each ticket resolves a decision, and the map is done when the way is clear, with nothing left to decide before someone goes and does the thing. When the remaining work is execution, hand off to the appropriate execution skill. When the user authorizes execution, record that override in **Notes**, carrying execution into the map itself, but absent that, produce decisions, not deliverables.

## Refer by name

Refer to each map or ticket by its title, linked to its URL or local path. Use the title in status messages and Decisions so far. Keep IDs for tracker operations.

## The map

The map is a single issue on this repo's issue tracker, labelled `wayfinder:map`, the canonical artifact. Its tickets are child issues of the map.

The map is an **index**. Summarize each decision in one line and link its ticket. Keep the full answer in the ticket.

**Where the map, its child tickets, blocking, and frontier queries physically live is tracker-specific.** Use the tracker already chosen for the effort and its documented operations. If none is configured, use [the local Markdown tracker](TRACKER.md). Keep local artifacts until publishing to a shared tracker is authorized.

### The map body

The whole map at low resolution, loaded once per session. Open tickets are **not** listed: they are open child issues, found by query.

```markdown
## Destination

<what reaching the end of this map looks like: the spec, decision, or change this effort is finding its way to. One or two lines; every session orients to it before choosing a ticket.>

## Notes

<domain; skills every session should consult; standing preferences for this effort>

## Decisions so far

<One line per resolved ticket, enough to judge relevance. Follow its link for the detail.>

- [<closed ticket title>](https://example.com/ticket): <one-line gist of the answer>

## Not yet specified

<In-scope fog you cannot ticket yet. See "Fog of war" below.>

## Out of scope

<Work ruled beyond the destination. See "Out of scope" below.>
```

### Tickets

Each ticket is a **child issue** of the map; the tracker's issue id is its identity. Its body is the question, sized to one agent session within the available context budget:

```markdown
## Question

<the decision or investigation this ticket resolves>
```

Each ticket carries a `wayfinder:<type>` label, one of `research`, `prototype`, `grilling`, `task` (see [Ticket types](#ticket-types)).

A session **claims** a ticket before any work, using the tracker's atomic claim or assignment operation. Record the session owner as well as the developer so concurrent sessions for the same person can distinguish their claims. If the tracker cannot claim atomically, one coordinator serializes claims and shared-map updates. An open ticket with no active claim is unclaimed.

Use the tracker's native dependency relationships so its interface shows which tickets are available. Only a tracker that lacks native blocking falls back to a body convention. A ticket is **unblocked** when every ticket blocking it is resolved with the required decision; the **frontier** is the open, unblocked, unclaimed children, the edge of the known.

The answer isn't part of the body; it's recorded on resolution (see [Work through the map](#work-through-the-map)). Assets created while resolving a ticket are linked from the issue, not pasted in.

## Ticket types

Mark each ticket **HITL** when it requires a live exchange with the human, or **AFK** when the agent can resolve it alone. Resolve HITL tickets through that exchange. The agent cannot supply the human's answers.

- **Research** (AFK): Reading documentation, third-party APIs, or local resources like knowledge bases to establish a fact needed for a decision. Resolve through primary documentation and available search tools, using a focused research subagent when available and permitted. Record sources and separate findings from inference. Use when knowledge outside the current working directory is required.
- **Prototype** (HITL): Create a rough artifact for the human to assess (an outline, a rough take, a stub, or UI/logic code) through [prototype](../prototype/SKILL.md). Link the prototype as an asset. Use when "how should it look" or "how should it behave" is the key question.
- **Grilling** (HITL): Conversation. The default case. Read [grilling](../grilling/SKILL.md) and [domain-modeling](../domain-modeling/SKILL.md).
- **Task** (HITL or AFK): Manual work that must happen before a _decision_ can be made: nothing to decide, prototype, or research, but the discussion is blocked until it's done. Signing up for a service so its API can be judged, provisioning access, moving data so its shape can be seen. This is the one type that _does_ rather than decides, and it earns its place by unblocking a decision, not by delivering the destination. The agent drives authorized work alone where it can (AFK); otherwise it hands the human a precise checklist (HITL). Resolved when the work is done; the answer records what was done and any resulting facts (credentials location, new URLs, row counts) later tickets depend on.

## Fog of war

The **fog of war** is the set of in-scope decisions you cannot yet phrase as
specific questions. Resolving a ticket can make some of those questions clear
enough to become new tickets.

Record this uncertainty under **Not yet specified**, with the suspected question
or area to revisit. Give collaborators enough context to understand what remains
unknown without inventing ticket boundaries prematurely.

**Fog or ticket?** The test is whether you can state the question precisely now, _not_ whether you can answer it now.

- **Ticket when** the question is already sharp, even if it's blocked and you can't act on it yet.
- **Not yet specified when** you can't yet phrase it that sharply. Revisit it as related decisions resolve. It may produce several tickets or none.

**Not yet specified** excludes what's already decided (Decisions so far), what's already a live ticket, and what's out of scope (the next section).

## Out of scope

Record work beyond the destination under **Out of scope**. Keep it separate from in-scope questions that are not yet specified.

Reconsider out-of-scope work only when the destination changes. Treat that change as a new effort.

When an existing ticket proves out of scope, close it with that status. Add its title, reason, and link under **Out of scope**. Keep it out of **Decisions so far**, which indexes decisions made toward the destination.

## Invocation

Two modes. Either way, **resolve one decision ticket per session by default**, with the exception of research tickets. Continue further when the user explicitly requests it.

### Chart the map

User invokes with a loose idea.

1. **Name the destination.** Use [grilling](../grilling/SKILL.md) and [domain-modeling](../domain-modeling/SKILL.md) to pin down what this map is finding its way to: the spec, decision, or change. The destination fixes the scope, so it's settled first.
2. **Map the frontier.** Grill again, **breadth-first** this time: fan out across the whole space rather than deep on any one thread, surfacing the open decisions and the first steps takeable now. **If this surfaces no fog** (the way to the destination is already clear, the whole journey small enough for one session), you don't need a map. Report that the route is clear. Hand off to [multi-phase-plan](../multi-phase-plan/SKILL.md) for several PRs, [to-spec](../to-spec/SKILL.md) for a durable spec, or [implement](../implement/SKILL.md) for a settled task, within the user's authorization.
3. **Create the map** (label `wayfinder:map`): Destination and Notes filled in, Decisions-so-far empty, the fog sketched into **Not yet specified**.
4. **Create the tickets you can specify now** as child issues of the map, then wire blocking edges in a **second pass** (issues need ids before they can reference each other). Wiring sorts them into the frontier and the blocked; everything you can't yet specify stays in the fog: the **Not yet specified** section.
5. **Resolve the research tickets.** Claim each one before dispatch. Use independent subagents when available and permitted, bounded by available capacity, or work sequentially. Record findings and source links in the ticket's resolution, with links to any durable artifacts. The coordinator integrates resolutions into the shared map.
6. Stop after charting and research. Leave human decisions open for the next exchange.

### Work through the map

User invokes with a map (URL, number, or local path). A ticket is **optional**: without one, you pick the next decision, not the user.

1. Load the **map**: the low-res view, not every ticket body.
2. Choose the ticket. If the user named one, use it. Otherwise take the first frontier ticket in order. **Claim it**: record your session ownership before any work. If the named ticket is blocked or already claimed, report that state instead of taking it over. If no frontier ticket exists, report the unresolved blockers or claims; declare the route clear only when no unresolved in-scope tickets or fog remain.
3. Resolve it. **Zoom as needed**: fetch the full body of any related or closed ticket on demand; read whichever skills the `## Notes` block names. If in doubt, read [grilling](../grilling/SKILL.md) and [domain-modeling](../domain-modeling/SKILL.md).
4. Record the resolution: post the answer as a **resolution comment**, **close** the issue, and **append a context pointer** to the map's Decisions-so-far.
5. Add newly-surfaced tickets (create-then-wire); graduate any fog the answer has made specifiable, clearing each graduated patch from **Not yet specified** so it lives only as its new ticket. If the answer reveals that a ticket (this one or another) sits beyond the destination, **rule it out of scope** rather than resolving it on the route. If the decision invalidates other parts of the map, update them or close them as superseded, retaining their history and links. A canceled blocker does not count as a resolved prerequisite; update its dependents before admitting them to the frontier.

The user may run unblocked tickets in parallel, so expect other sessions to be editing the tracker concurrently. Re-read current state before updating the shared map; preserve other sessions' decisions and use the tracker's concurrency controls.

When passing unfinished work to another session, read [handoff](../handoff/SKILL.md). Keep decision findings in the map and ticket records, and follow the chosen tracker's claim-release procedure when the owning session stops.
