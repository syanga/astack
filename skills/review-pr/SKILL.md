---
name: review-pr
description: Review changes for standards, requirements, and correctness. Use for review requests and implementation handoffs.
---

Three-axis review of the requested changes against a fixed point:

- **Standards**: does the code conform to this repo's documented coding standards?
- **Spec**: does the code faithfully implement the originating issue / spec?
- **Correctness**: does the code hold up under edge cases, failure paths, and hostile input?

The axes run as **separate reviewers** so they don't pollute each other's context, then this skill aggregates their findings.

Use **report-only** for standalone review requests. Use **fix-and-review** when the user requests repairs or an implementation workflow hands off its authorized work. Explicit read-only requests take precedence.

## Process

### 1. Pin the fixed point

Whatever the user said is the fixed point (a commit SHA, branch name, tag, `main`, `HEAD~5`, etc.). If they didn't specify one, use the PR's target branch when available; otherwise ask for it.

Resolve the comparison base to a commit SHA once. For a PR, work from its head commit and record the SHA before dispatch.

Reuse existing verification and pass it to the applicable reviewers.

Review the requested changes against the fixed point. Give every reviewer the same diff and relevant commit history.

Before going further, confirm the fixed point resolves (`git rev-parse <fixed-point>`) and the diff is non-empty. A bad ref or empty diff should fail here, before dispatching reviewers.

Save the reviewed source and diff in a temporary review directory, with the base SHA, the candidate identity (commit SHA, or HEAD plus the working-tree diff), and the capture command, for the Correctness reviewer. Refresh the snapshot before each Correctness pass.

### 2. Identify the spec source

Look for the originating spec, in this order:

1. A path the user passed as an argument, or the user's current requirements.
2. Issue references in the commit messages (`#123`, `Closes #45`, GitLab `!67`, etc.), fetched through the issue tracker.
3. A spec file under `docs/`, `specs/`, or `.scratch/` matching the branch name or feature.
4. If nothing is found, ask the user where the spec is. If they say there isn't one, the **Spec** sub-agent will skip and report "no spec available".

### 3. Identify the standards sources

Anything in the repo that documents how code should be written, such as `CODING_STANDARDS.md` or `CONTRIBUTING.md`.

For TypeScript changes, include [TypeScript guidance](../typescript-best-practices/SKILL.md) in the Standards review. Separate demonstrated defects from optional style advice. Advice does not authorize a refactor.

On top of whatever the repo documents, the Standards axis always carries the **smell baseline** below: a fixed set of Fowler code smells (_Refactoring_, ch.3) that applies even when a repo documents nothing. Two rules bind it:

- **The repo overrides.** A documented repo standard always wins; where it endorses something the baseline would flag, suppress the smell.
- **Always a judgement call.** Each smell is a labelled heuristic ("possible Feature Envy"), never a hard violation. Like any standard here, skip anything tooling already enforces.

Each smell reads *what it is* → *how to fix*; match it against the diff:

- **Mysterious Name**: a function, variable, or type whose name doesn't reveal what it does or holds. → rename it; if no honest name comes, the design's murky.
- **Duplicated Code**: the same logic shape appears in more than one hunk or file in the change. → extract the shared shape, call it from both.
- **Feature Envy**: a method that reaches into another object's data more than its own. → move the method onto the data it envies.
- **Data Clumps**: the same few fields or params keep travelling together (a type wanting to be born). → bundle them into one type, pass that.
- **Primitive Obsession**: a primitive or string standing in for a domain concept that deserves its own type. → give the concept its own small type.
- **Repeated Switches**: the same `switch`/`if`-cascade on the same type recurs across the change. → replace with polymorphism, or one map both sites share.
- **Shotgun Surgery**: one logical change forces scattered edits across many files in the diff. → gather what changes together into one module.
- **Divergent Change**: one file or module is edited for several unrelated reasons. → split so each module changes for one reason.
- **Speculative Generality**: abstraction, parameters, or hooks added for needs the spec doesn't have. → delete it; inline back until a real need shows.
- **Message Chains**: long `a.b().c().d()` navigation the caller shouldn't depend on. → hide the walk behind one method on the first object.
- **Middle Man**: a class or function that mostly just delegates onward. → cut it, call the real target direct.
- **Refused Bequest**: a subclass or implementer that ignores or overrides most of what it inherits. → drop the inheritance, use composition.

### 4. Dispatch the reviewers

In report-only mode, run the applicable reviewers in parallel when capacity permits. In fix-and-review mode, run Standards and Spec in parallel and hold Correctness until they converge in step 5. When capacity prevents concurrent dispatch, run them sequentially in separate contexts against the same pinned diff.

Every reviewer reports findings without changing code.

**Standards sub-agent prompt** should include:

- The shared diff and relevant commit history.
- The list of standards-source files you found in step 3, **plus the smell baseline from step 3** pasted in full (the sub-agent has no other access to it).
- For tests relevant to the change, include [test-behavior-not-implementation.md](../principles/test-behavior-not-implementation.md) in full and instruct the reviewer to apply it.
- The brief: "Report, per file/hunk where relevant, (a) every place the diff violates a documented standard: cite the standard (file + the rule); and (b) any baseline smell you spot: name it and quote the hunk. Distinguish hard violations from judgement calls: documented-standard breaches can be hard, but baseline smells are always judgement calls, and a documented repo standard overrides the baseline. Skip anything tooling enforces. Under 400 words."

**Spec sub-agent prompt** should include:

- The shared diff and relevant commit history.
- The path or fetched contents of the spec.
- The brief: "Report: (a) requirements the spec asked for that are missing or partial; (b) behaviour in the diff that wasn't asked for (scope creep); (c) requirements that look implemented but where the implementation looks wrong. Quote the spec line for each finding. Under 400 words."

If the spec is missing, skip the Spec sub-agent and note this in the final report.

**Correctness reviewer prompt** should include:

- The shared diff and relevant commit history.
- The requirements or spec from step 2, when available.
- The [shared adversarial brief](../harden-pr/sections/adversarial.md#shared-adversarial-brief), [the evidence requirements](../harden-pr/SKILL.md#verify-findings), and [the testing finding standard](../harden-pr/specialists/testing.md#finding-standard), pasted in full.

Run it on an outside provider. Follow [outside-review.md](../harden-pr/outside-review.md) for execution, sizing, clarification, and results. The review completes when it meets the outside adversarial criterion in [the completion criteria](../harden-pr/sections/adversarial.md#completion-criteria). Correctness uses only this brief and the outside adversarial criterion, at any diff size. Route requests for structured or full review to [harden-pr](../harden-pr/SKILL.md).

If outside coverage is unavailable, run the same prompt in a fresh native sub-agent labelled `native fallback` and report the outside coverage as missing.

### 5. Aggregate

In fix-and-review mode, collect the applicable reports. Have the parent fix verified in-scope problems and rerun the caller's affected acceptance checks. Refresh the diff against the same base, including uncommitted repairs, and repeat the applicable Standards and Spec reviews.

Run Correctness once, on the first candidate where the applicable Standards and Spec reviews report no unresolved verified problems and required checks pass. For every later repair, from any axis, have a fresh native sub-agent verify it using the [focused brief](../harden-pr/sections/adversarial.md#re-review-after-fixes), and rerun Standards and Spec for what it touched. Rerun Correctness only when a repair substantially changes the design, on the outside provider when available and otherwise as the native fallback.

Converge when the final changes have no unresolved verified problems, required checks pass, and applicable reviews are complete. A native fallback completes the Correctness review for convergence. Optional suggestions do not block completion. If progress stalls or a decision is needed, report the remaining blockers.

In fix-and-review mode with existing PRs, [finalize the PRs](../harden-pr/pr-work.md#finalize-reviewed-prs), including committing and pushing verified repairs. Without a PR, return the reviewed changes to the caller's delivery workflow.

Verify Correctness findings against the evidence requirements before presenting or posting them. Present each report under its axis heading (`## Standards`, `## Spec`, `## Correctness`), verbatim or lightly cleaned. Under `## Correctness`, name the reviewer (outside provider, `native fallback`, or not run with the reason), each finding's disposition, and any repair-verification result. Do **not** merge or rerank findings, because the axes are deliberately separate (see _Why three axes_).

End with a one-line summary: remaining findings per axis, and the worst issue _within each axis_ (if any). Don't pick a single winner across axes: that's the reranking the separation exists to prevent.

At the end of the invocation, follow [posting.md](../harden-pr/posting.md) to publish the review record and remaining actionable findings on an existing PR, including clean or incomplete results. That guidance also covers publication restrictions and runs without a PR.

## Why three axes

A change can pass one axis and fail another:

- Code that follows every standard but implements the wrong thing → **Standards pass, Spec fail.**
- Code that does exactly what the issue asked but breaks the project's conventions → **Spec pass, Standards fail.**
- Code that follows the standards and implements the spec but crashes on empty input or swallows a failed write → **Standards and Spec pass, Correctness fail.**

Reporting them separately stops one axis from masking another.
