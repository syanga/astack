---
name: writing-for-agents
description: Writing documents for agents. Use when creating or editing skills, or modifying AGENTS.md or CLAUDE.md.
---

Reference for writing any document an agent consumes: a skill, an `AGENTS.md` / `CLAUDE.md`, a doc reached by a pointer. The packaging differs. The writing does not. The same levers make each one predictable, because the agent takes the same _process_ every run rather than producing the same output.

When the document you're writing is a skill, read [`SKILL-MECHANICS.md`](SKILL-MECHANICS.md) for frontmatter, invocation choice, and router skills. When a draft is done, run its prose through the sibling `unslop` skill at `../unslop/SKILL.md`.

When improving an installed skill, follow [source edits](SOURCE-EDITS.md) to
locate its owning repository and follow that repository's authoring workflow.

## Context pointers

A **context pointer** is a reference held in the agent's context that names some out-of-context material and encodes the condition for reaching it. A skill's description is one; a line in `AGENTS.md` naming a doc is the same object. The pointer's _wording_, not its target, decides when the agent reaches the material, and how reliably. A must-have target behind a weakly worded pointer is a variance bug. Sharpen the wording first, and inline the material only if sharpening fails.

A pointer does two jobs: state what the material is, and list the **branches** that should trigger reaching it (a branch is a distinct case the document handles, so different runs take different paths through it). Every word of an always-loaded pointer costs on every turn, so it earns even harder pruning than the body:

- **Front-load the leading word.** The pointer is where it does its triggering work.
- **One trigger per branch.** Synonyms that rename a single branch are one branch written twice; collapse them and keep only genuinely distinct branches.
- **Cut identity the body already carries.** The description says when to reach the skill, not what it contains. One clause naming the skill, then the triggers. Every word costs every turn in every session, so a description that reads like a summary of the body has failed.

## The two loads

Every document and pointer you add spends one of two budgets:

- **Context load** is the cost of always-loaded material on the agent's window: an `AGENTS.md` line, a skill description, anything sitting in context every turn, spending tokens and attention whether or not it fires.
- **Cognitive load** is the cost on the human: which documents exist and when to reach for each. The human is the index. This is not a cost to minimise. It is the price of human agency. Spend it where human judgement matters and remove it where it does not.

Material reached only through a pointer escapes context load at the price of the pointer's own line. Material with no pointer at all costs only cognitive load.

## Information hierarchy

A document is built from two content types: **steps** (the ordered actions the agent performs) and **reference** (definitions, rules, facts consulted on demand). The two mix freely: all steps (a recipe), all reference (a review's rules, this skill), or both. The core decision is where each piece sits on the **information hierarchy**, a ladder ranked by how immediately the agent needs the material:

1. **In-file step** is the primary tier. It is what the agent does, in order.
2. **In-file reference** is consulted on demand. Often a legitimately flat peer-set (every rule of a review on one rung), which is a fine arrangement, not a smell.
3. **Disclosed reference** is pushed out into a separate file, reached by a context pointer, loaded only when the pointer fires. Spans a sibling file in the same folder through fully external reference that lives anywhere and any document can point at.

Push too little down and the top bloats; push too much and you hide material the agent actually needs. That tension is the whole decision.

**Progressive disclosure** is the move down the ladder (out of the main file and behind a pointer) so the top stays legible. It is not primarily a token optimisation. It is how the hierarchy is protected. Branching is the cleanest disclosure test. Inline what every branch needs, and push behind a pointer what only some branches reach. When a document has steps, in-file reference that should be disclosed buries them and turns attending to them into a coin-flip. That makes disclosure a variance lever as well as a legibility one.

**Co-location** is the within-file companion. The ladder decides _how far down_ a piece sits. Co-location decides _what sits beside it_ once there. Keep a concept's definition, rules, and caveats under one heading rather than scattered, so reading one part brings its neighbours with it. The test is whether the document reads like documentation written for the agent. Grouped material reads that way; scattered material does not. Scattering is distinct from duplication. Duplication repeats one meaning in two places; scattering fragments one meaning across many.

**Sprawl** is the failure mode here. The document is simply too long, even when every line is live and unique. Attention thins across the excess, and every extra line is one more to keep relevant. The cure is the ladder. Disclose reference behind pointers, and split by branch or sequence so each path carries only what it needs.

## Steps and completion criteria

Every step ends on a **completion criterion**, the condition that tells the agent the work is done. Two properties make it a lever:

- **Clarity.** Can the agent tell done from not-done? A vague bound ("understanding reached") invites **premature completion**, ending the step before it is genuinely done because attention has slipped to _being done_. The visible steps still ahead (the **post-completion steps**) supply the pull; the criterion's clarity is the resistance. Defend in order. **Sharpen the bound first**, which is local and cheap. Only if it is irreducibly fuzzy _and_ you observe the rush, hide the later steps by splitting the sequence. Hiding only works across a real context boundary (a hand-off or a subagent dispatch; an inline call leaves the later steps in context and clears nothing).
- **Demand.** How much it requires. "Every modified model accounted for" forces thorough work where "produce a change list" does not. Demand drives **legwork** (the digging the agent does within the work, latent in the wording rather than written as its own step), and it is not step-bound. "Every rule applied" binds a body of flat reference just as "every step done" binds a sequence, which is how an all-reference document still carries an exhaustiveness bar.

The strongest criteria are both checkable and exhaustive.

## When to split

Splitting one document into two spends one of the two loads, so split only when the cut earns it:

- **By sequence.** Split a run of steps where the post-completion steps tempt the agent to rush the one in front of it. Keeping them out of view drives more legwork on the current task. Beware the reverse. Merging sequences exposes each step's later steps to what follows, inviting premature completion.
- **By invocation.** This cut applies only to skills. See [`SKILL-MECHANICS.md`](SKILL-MECHANICS.md).

## Leading words

A **leading word** is a compact concept already living in the model's pretraining that the agent thinks with while running the document (_lesson_, _fog of war_, _tracer bullets_). Repeat it as a token, never as a sentence. It accumulates a distributed definition and anchors a whole region of behaviour in the fewest tokens, because it recruits priors the model already holds. Coining your own works if you define it clearly, but a made-up word recruits no priors. You pay in definition tokens what a pretrained word gives free, so reach for an existing word first.

It anchors twice. In the body it anchors _execution_. The agent reaches for the same behaviour every time the word appears, and inside flat reference it focuses attention on a class of thing to look for. In a pointer it anchors _invocation_. When the same word lives in your prompts, your docs, and your codebase, the agent links that shared language to the material and reaches it more reliably.

Hunt for opportunities to refactor with leading words. A triad spelled out at three sites, or a pointer spending a sentence to gesture at one idea, is a passage that can collapse into a single token:

- "fast, deterministic, low-overhead" becomes _tight_ (a _tight_ loop).
- "a loop you believe in" becomes _red_, turning a fuzzy gate into a binary observable state (the loop goes _red_ on the bug, or it doesn't).

You win twice, with fewer tokens and a sharper anchor for the agent's thinking. Assume every document carries restatements that leading words retire. Go find them.

**Negation** is the failure mode beside this lever. Steering by prohibition drags the forbidden behaviour into context and makes it _more_ available, not less. _Don't think of an elephant_, and the elephant is all there is. The negation is a weak modifier that the strongly activated concept overruns, so the ban half-reads as an instruction to do the thing. Prompt the **positive**. State the target behaviour ("write one-line comments") so the banned one is never spoken. A prohibition earns its place only as a hard guardrail you cannot phrase positively. Even then, pair it with the positive target so attention lands on what to do.

## Pruning

- Before writing a rule, ask whether a lint, a check, a script, or a hook can enforce it. If one can, build it and leave the sentence out. A structural check holds without the reader's cooperation; a sentence has to be noticed, remembered, and obeyed every time.
- Keep each meaning in a **single source of truth**, one authoritative place, so changing the behaviour is a one-place edit. **Duplication** (the same meaning in more than one place) costs maintenance and tokens, and inflates a meaning's prominence on the ladder past its real rank. Duplication is the accidental inverse of a leading word, which repeats a token on purpose and never the meaning.
- The **environment** is a source of truth too (`package.json` scripts, config files, the directory layout, `--help` output), and a document that restates it is a **cache**, a copy of a lookup that earns its load only when the lookup is expensive. Cache what the agent cannot find by looking: the unwritten convention, the reason behind a choice, the gotcha no config file states. Leave the one-file, one-command lookups to the environment, where they cannot go stale.
- Check every line for **relevance**. Does it still bear on what the document does? A line loses relevance by never bearing on the task (mere exposition, or a branch that should be disclosed) or by going stale as the behaviour or world it describes changes. Shorter documents are easier to keep relevant. Without a pruning discipline the default fate is **sediment**, stale layers that settle because adding feels safe and removing feels risky, until the reader has to dig through them to find what is still live.
- Hunt **no-ops** sentence by sentence. An instruction the model already obeys by default pays load to say nothing. The test (does it change behaviour versus the default?) is model-relative, not reader-relative. Two people disagreeing about a no-op disagree about the default, and settle it by running the document, not by debate. When a sentence fails, delete the whole sentence rather than trim words from it. The test also grades leading words. A word too weak to beat the default (_be thorough_ when the agent is already thorough-ish) is a no-op, and the fix is a stronger word (_relentless_), not a different technique.
