# Triage a review thread

A review thread, from a bot or a person, is a claim to verify against the code. These are the classes, the evidence each needs, and the patterns that have repeated.

## Classes

- **fix**. The claim holds against the current code.
- **dismiss**. The current code proves the claim needs no change, and the reply carries the evidence: the file and line, the test that passes, or the invariant that covers it. A pattern below says where to look for the evidence. Matching a pattern is not itself evidence.
- **ask**. The claim is novel or ambiguous, or it is in an ask-by-default category and you did not verify it by running code. An ask stays unresolved.

A red test run confirms a claim. A green run is the evidence for a dismissal.

## Ask by default

These categories are ask by default: security, privacy, auth, billing, data retention, permission boundaries, migrations, schema, idempotency, concurrency, and cross-system behaviour. So is any comment whose suggested fix is small and reduces risk without changing intent. In these categories, fix only a claim you verified by running code. A prior dismissal of something similar does not carry over.

## Reply shape

`pr.py reply` adds the on-behalf-of header. Write only the body.

- **Fixed.** The SHA, a two-line diff of the change, one sentence on what was wrong.
- **Already fixed.** The SHA that addressed it and how.
- **Dismissed.** One sentence stating why, then the evidence.

Every reply cites code. From a bot's third pass on the same PR, lean toward dismissing the documented patterns. The snapshot counts passes, and the ask-by-default categories still go to the user. One exception to that rule: a test that pins prose (a regex over a doc, a snapshot of wording) drifts because earlier fix rounds edited the prose, so a drift claim on a late pass is often real.

## Patterns that dismiss

Each pattern names when it holds, when it does not, the signal that identifies it, and its source.

- **Unused in this PR, used by a later one.** Holds when a later PR in the stack uses it. Does not hold outside a stack, for public API, or when the later use cannot be shown. Signal: "exported X is never used". Source: pstack.
- **Intentional visual change.** Holds when the PR states the change or a design review approved it. Does not hold for accessibility, focus, contrast, keyboard behaviour, or a component API the PR did not mean to change. Signal: comments on focus outlines, sizes, spacing, or shared component defaults. Source: pstack.
- **Temporary duplication during a replacement.** Holds when the old path is being deleted and the duplicate is small and local. Does not hold for security, billing, data access, or API behaviour. Signal: "duplicated logic". Source: pstack.
- **An existing invariant covers it.** Holds when a shared component, type, or single source of truth visible in the code guarantees the concern. Does not hold when the invariant is assumed, timing-dependent, or crosses an async boundary. Signal: a missing bound or null check on a value the framework already constrains. Source: pstack.
- **User-declared follow-up.** Holds when the user has said the issue is a known follow-up, the PR does not make it worse, and the area is low-risk. Does not hold when you have no word from the user on it, or the area is ask by default. Signal: "we'll delete this eventually". Source: pstack.
- **Withdrawn or self-declared false positive.** Holds when the comment, or a later reply from the same bot, says the finding is withdrawn or compliant, and you verified the rule locally. Does not hold when the only evidence is someone writing "false positive" on a high-risk issue. Signal: a rule comment whose body says the file is already compliant. Source: pstack, recurring.
- **Fixed later in the same PR.** Holds when the head already contains the exact guard the finding asks for, usually from a later commit. Does not hold when the guard runs after the side effect, or misses the principal named. Signal: a "missing check" finding from a review run before the hardening commit. Source: pstack.
- **Widening a deliberately narrow error condition.** Holds when the narrow condition encodes a real distinction, such as a fallback gated on "binary not found" that must not swallow "command ran and failed". Does not hold when the narrow condition misses a case of the same kind, or when the retry is idempotent and the original error is still surfaced. Signal: "only retries on ENOENT". Source: pstack.

## Patterns that fix

- **Manual reimplementation of native behaviour.** Hand-rolled sticky positioning, scroll forwarding, hit-testing masks. Findings against this code have been real every time. Default to fix. Source: pstack, one PR with eighteen findings, all fixed.
