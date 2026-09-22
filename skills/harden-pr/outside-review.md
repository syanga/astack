# Run an outside review

Follow [provider execution](../arena/PROVIDERS.md) for provider selection, CLI invocation, timeouts, captured output, and unavailable coverage. Run from the reviewed repository. Supply each assignment's complete prompt, diff, and relevant source context from the fixed review snapshot.

Use its read-only prompted invocations for every assignment, including structured reviews. Review-specific completion criteria remain in [adversarial.md](sections/adversarial.md).

## Size assignments before dispatch

Before launch, assess the diff and supporting source against the execution budget, including time to report results. If the assignment is too broad, split it by related behavior, keeping callers, contracts, and tests together. Assign interactions between parts explicitly and retain the original coverage obligation across the parts.

Give each reviewer a bounded scope. Embed the assigned diff text in the prompt. Put supporting source and requirements inside the provider's readable checkout or snapshot, and reference those paths. Keep relevant shared context accessible to every part.

Before dispatch, verify that the captured diff and source match the recorded base and candidate. Include those identities and the capture command in the brief. The orchestrator owns this provenance check; the reviewer assesses the supplied diff and source. A snapshot needs no Git metadata. Unreadable source or a mismatch between the diff and source still means incomplete coverage.

State a working budget shorter than the provider's hard timeout. Ask the reviewer to return the [required review output](sections/adversarial.md#completion-criteria) within that budget.

## Results and limits

Successful process exit alone does not establish a completed review. Validate the response as described in [adversarial.md](sections/adversarial.md).

If a returned review omits required output fields, request one targeted clarification. Keep coverage incomplete until the response meets the completion criteria.

A fresh clarification or retry has no earlier conversation. Include the original scope and requirements, the prior findings and coverage statement being clarified, the required output format, and the exact missing information. Embed the relevant diff and provide readable source references again. Ask for the missing assessment or correction, preserving completed work.

After timeout or failure, record unavailable coverage and preserve partial output as incomplete evidence. Retry only when an identified scope or failure condition changes, or the user requests another attempt. For an oversized assignment, split unfinished scope before retrying. Retain completed parts whose evidence still applies; partial output from a timed-out invocation remains incomplete evidence.

If the reviewed source changes, follow [affected re-review](sections/adversarial.md#re-review-after-fixes) before applying the result to the new candidate.
