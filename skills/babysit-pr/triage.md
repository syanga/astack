# Triage a review thread

A review thread from a bot or a person is a claim to verify against the code. These are the classes, the evidence each needs, and the patterns that have repeated.

## Classes

- **fix**. The claim holds against the current code. Change only what the finding asks for, because each added sentence is new text for the next round to review. A noted finding that the Threads block of `SKILL.md` sends into the same push is the one addition. When the finding admits a test, write one that fails first, then fix in the same commit.
- **dismiss**. The current code proves the claim needs no change, and the reply carries the evidence: the file and line, the test that passes, or the invariant that covers it. A pattern below says where to look for the evidence. Matching a pattern is not itself evidence.
- **defer**. The claim holds, and fixing it now costs more than this PR should carry: it is not small, or it belongs to other work. A consider finding that the `ours` rule below defers needs no verification. Reply with the reason and resolve the thread. Every deferral goes in the report to the user as a follow-up.
- **ask**. The claim is novel or ambiguous, or it is in an ask-by-default category and you did not verify it. Reply on the thread with the question and leave it unresolved. The snapshot then shows the thread as `awaiting_user` and the PR is handed off rather than merged.

When a claim is cheap to test, run the test before classifying. A red run confirms the claim. A green run is the evidence for a dismissal. When a comment asks for work outside the PR's intent, reply with the intent quoted rather than widening the change.

A thread the snapshot marks `ours` is a review-pr finding, and its first line under the on-behalf-of header names its bucket. Fix an act-on finding on every round. When the snapshot's `our_reviews.first_round` is true, fix each consider finding whose fix is small in the same push as the act-on fixes, and defer the others. When `first_round` is false, defer every consider finding, because a push for one makes the head unreviewed and spends another review round. When `our_reviews.last_round` is true, the review on the head is the last the cap allows. A fix on the last round that pushes a commit ends the run in `stop`, because a blocking defect on the last round needs the user. A fix made with `gh pr edit` pushes nothing, so the head stays reviewed and the run goes on. The ask-by-default categories below apply to `ours` threads too, except a consider finding when `first_round` is false, which is deferred and named in the report.

## Ask by default

These categories are ask by default: security, privacy, auth, billing, data retention, permission boundaries, migrations, schema, idempotency, concurrency, and cross-system behaviour. In these categories, fix only a claim you verified, by running code, or for a claim about prose by reading the text it quotes. A prior dismissal of something similar does not carry over.

## Reply shape

`pr.py reply` adds the on-behalf-of header. Write only the body.

- **Fixed.** The SHA, the changed lines or the first few of a longer change, one sentence on what was wrong. For a fix made with `gh pr edit`, the new text in place of the SHA.
- **Already fixed.** The SHA that addressed it and how.
- **Dismissed.** One sentence stating why, then the evidence.
- **Deferred.** That the claim holds, or that you did not verify it, why it waits, and that the run's report lists it as a follow-up.
- **Asked.** The question for the user, your read of the claim, and what you verified so far.

Every reply cites code. From a bot's third pass on the same PR, lean toward dismissing the documented patterns. The snapshot counts passes, and the ask-by-default categories still go to the user. One exception to that rule: a test that pins prose (a regex over a doc, a snapshot of wording) drifts because earlier fix rounds edited the prose, so a drift claim on a late pass is often real.

## Patterns that dismiss

Each pattern names when it holds, when it does not, and the signal that identifies it.

- **Unused in this PR, used by a later one.** Holds when a later PR in the stack uses it. Does not hold outside a stack, for public API, or when the later use cannot be shown. Signal: "exported X is never used".
- **Intentional visual change.** Holds when the PR states the change or a design review approved it. Does not hold for accessibility, focus, contrast, keyboard behaviour, or a component API the PR did not mean to change. Signal: comments on focus outlines, sizes, spacing, or shared component defaults.
- **Temporary duplication during a replacement.** Holds when the old path is being deleted and the duplicate is small and local. Does not hold for security, billing, data access, or API behaviour. Signal: "duplicated logic".
- **An existing invariant covers it.** Holds when a shared component, type, or single source of truth visible in the code guarantees the concern. Does not hold when the invariant is assumed, timing-dependent, or crosses an async boundary. Signal: a missing bound or null check on a value the framework already constrains.
- **User-declared follow-up.** Holds when the user has said the issue is a known follow-up, the PR does not make it worse, and the area is low-risk. Does not hold when you have no word from the user on it, or the area is ask by default. Signal: "we'll delete this eventually".
- **Withdrawn or self-declared false positive.** Holds when the comment, or a later reply from the same bot, says the finding is withdrawn or compliant, and you verified the rule locally. Does not hold when the only evidence is someone writing "false positive" on a high-risk issue. Signal: a rule comment whose body says the file is already compliant. Source: pstack, recurring.
- **Fixed later in the same PR.** Holds when the head already contains the exact guard the finding asks for, usually from a later commit. Does not hold when the guard runs after the side effect, or misses the principal named. Signal: a "missing check" finding from a review run before the hardening commit.
- **Widening a deliberately narrow error condition.** Holds when the narrow condition encodes a real distinction, such as a fallback gated on "binary not found" that must not swallow "command ran and failed". Does not hold when the narrow condition misses a case of the same kind, or when the retry is idempotent and the original error is still surfaced. Signal: "only retries on ENOENT".

## Patterns that fix

- **Manual reimplementation of native behaviour.** Hand-rolled sticky positioning, scroll forwarding, hit-testing masks. Findings against this code have been real every time. Default to fix. Source: pstack, one PR with eighteen findings, all fixed.
