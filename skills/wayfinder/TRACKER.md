# Issue tracker: Local Markdown

Maps and decision tickets live as Markdown files in `.scratch/` unless the
user chooses another directory. Share the directory between sessions that work
on the same map, and keep it outside disposable worktrees. Record its absolute
path in the map's Notes.

## Wayfinding operations

Used by `wayfinder`. The **map** is a file with one **child** file per ticket.

- **Map**: `.scratch/<effort>/map.md`, with the Destination, Notes, Decisions so
  far, Not yet specified, and Out of scope sections.
- **Child ticket**: `.scratch/<effort>/issues/NN-<slug>.md`, numbered from `01`,
  with the question in the body. A `Type:` line records `research`, `prototype`,
  `grilling`, or `task`. A `Status:` line records `open`, `claimed`, `resolved`,
  `out-of-scope`, or `superseded`. An `Owner:` line records the claiming session.
- **Blocking**: a `Blocked by: NN, NN` line near the top. Use `None` when there
  are no blockers. A ticket is unblocked when every file it lists is `resolved`.
  If a blocker closes out of scope or as superseded, revise the dependency
  explicitly; closing it alone does not satisfy the prerequisite.
- **Frontier**: scan the child files for `open`, unblocked tickets with no owner.
  First by number wins. Missing or cyclic dependencies block work until fixed.
- **Claim**: one coordinator serializes claims, ticket numbering, and shared-map
  updates. Record that coordinator in Notes. Set `Status: claimed` and save the
  session's `Owner:` before work or dispatch. Other sessions request a claim
  from that coordinator; they do not race a read followed by a file write.
- **Resolve**: append the answer under `## Answer`, set `Status: resolved`, then
  append a context pointer with the gist and link to Decisions so far in the map.
  Workers return resolutions to the coordinator for this update.
- **Release**: when a session stops, retain its findings and return an unfinished
  ticket to `open` with an empty owner. Reassign a stale claim only after checking
  that its owner has stopped. A new coordinator takes over only after the prior
  coordinator has stopped.

Append conversation history under `## Comments`. Link artifacts from the answer
instead of copying them into the map. Preserve resolved and superseded files so
later sessions can recover the decision history.
