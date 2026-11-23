---
name: decision-requests
description: Self-contained decision requests. Use before asking the user for a decision raised by a thread you launched or about its work, while such a request is open, or when the user asks about one.
---

# Decision requests

## Write and report requests

Write each decision request so it stands without earlier messages:

- What is being decided and why it needs the user, in plain words.
- What the decision blocks and what work continues meanwhile.
- What the user must read and what they may skip.
- Each option with its consequence.
- A recommendation with a one-line reason, for each part of the decision.
- Every artifact the decision depends on, given as a URL or as an absolute path on your machine. Keep each artifact at that URL or path while the request is open. Publish mocks, screenshots, and tables wider than 100 columns with [t3-preview](../t3-preview/SKILL.md) when it is available, because a path does not render them in chat.
- A link to each thread that raised the decision or whose work it concerns.
- The shortest sufficient reply, such as "approve all".
- What happens next and who acts.

Before you send, repeat, or count a request in a message, check that its question is still open. Read the live state of each PR and issue it names. Skip a PR that your own earlier live read, or your saved record of that read, showed merged. When you cannot read a PR or issue, such as when the request asks for that access, say so in the request.

When the user asks for status or about open decisions, start the reply with the current full text of each open request under a "Decisions you owe" heading.

When another skill sets the format of your message, keep that format and put the request inside it.

## Keep open requests in one file

When work continues while a request is open, keep its full text in `open-decisions.md`, in the task's store or beside the task's notes. Keep the file out of commits. When new evidence changes a request, update it in the file. When the user answers, or someone else settles the question, record the outcome with the request. Confirm the outcome in one line of your next message.

While the file holds open requests, include these parts in each message to the user:

- The full request of each one that opened since your previous message.
- For a request that new evidence changed, only the change, in at most 300 words.
- A last line with the number of open requests and the absolute path of `open-decisions.md`.

Skip these parts in a message that starts with the current full text of each open request.
