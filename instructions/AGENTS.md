# Personal coding instructions

<!-- Shared by all supported harnesses. Fill in your preferences below. -->

I'm Alan. You're my agent. We will be working together a lot, so I thought it would be worth introducing myself.

I did my PhD in electrical engineering at Stanford University, working with Stephen Boyd. My academic background is mainly in convex optimization, control, machine learning.

I love to build and deeply understand how systems work. I focus on building complex things as simple as possible. I love to find ways to reduce complexity when solving problems, and to explain and build things in simple yet elegant ways, as a mathematician might.

I wanted to share some of my preferences here so we can be more aligned as we work together.

## Coding preferences - general
* Keep things simple. Channel "yagni" energy unless told otherwise.
* Typesafety is useful, take advantage of it.
* Don't be scared to propose bold ideas if they can meaningfully benefit our work.
* Be careful with destructive actions that are not explicitly requested by the user.
* Tests are good! Endless smoke tests, "regression tests" for feature deletions, etc, much less good. Tests should be focused, not slop.
* Names, types, and structure carry meaning. Comments do not. Keep a comment only for a legal or license header, behaviour forced by an external dependency, platform, vendor, or protocol we cannot change, a doc comment that defines a public API contract, an issue or RFC link that explains a constraint the code cannot express, or a lint or type suppression whose rule is faulty, pedantic, or style-only. When unsure, delete the comment. The no-comments skill's keep list is the authority.

## Questions are read-only
* A question is a request for an answer, not for changes. If the message opens with "how hard would it be", "what are your thoughts", "why does", "should we", "is it possible", "can X do Y", or otherwise asks rather than instructs: answer it, and do not edit files.
* If the answer is obvious and the change is trivial, still answer first and offer the change. Ask before making it.

## Visual and design work
* Do not edit real components first. For any non-trivial UI, layout, or copy change, build several distinct static mocks, publish them with the t3-preview skill, report the link, and stop. Wait for a pick before implementing.
* Standing constraints: dark mode, true black (#000) background, white primary text. Information-dense, no decorative card/pill chrome, no light-gray subtitle lines above sections. Minimal copy. No em dashes.
* Avoid continuously repainting CSS animations (pulse, shimmer, blur, spinners); they peg the GPU on high-refresh displays.

## Live systems
* Never touch production, live databases, or daily-driver build/preview channels unless explicitly told to. When a task is adjacent to any of them, name what you are about to touch before touching it.

## Pull Requests
* Open PRs with the open-pr skill. Prefer several narrow PRs to one large one.
* Titles follow the repo's convention. Conventional Commits in projects that use them, i.e. "fix(web): new threads no longer spike CPU"
* The body is a briefing a reviewer reads in under a minute: why the change exists, then how it was verified. End with a blurb naming the model and harness that made the changes.
* Open a real PR, not a draft. Drafts do not get review-bot coverage.
* Rebase onto latest main before opening. Stale branches conflict and waste a review round.
* Babysit only when asked, with the babysit-pr skill. Verify each bot finding against the source before acting on it, dismiss false positives with a written reason, and stay quiet while waiting.
* Merge only per the disposition given in the request (merge when green, or stop and report). If none was given, report and ask.
