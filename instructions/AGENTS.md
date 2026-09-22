# Personal coding instructions

I'm Alan. I did my PhD in electrical engineering at Stanford with Stephen Boyd, focusing on convex optimization, control, and machine learning. I value simple designs and a deep understanding of how systems work.

## Coding preferences

* Build only what the task needs.
* Use types to express constraints.
* Propose larger changes when they meaningfully simplify the solution.
* Be careful with destructive actions that are not explicitly requested by the user.
* Add tests when they verify a meaningful behavior change or failure case. When writing or reviewing tests, read and apply `principles/test-behavior-not-implementation.md` from the installed skills.
* When adding or changing code comments, read and follow the no-comments skill's keep list.

## Questions are read-only

Answer questions without editing files, even when the implied change is trivial. Offer the change and wait for an instruction to implement it.

## Writing

* When writing documentation, read and apply the technical-writing skill, which includes unslop.
* When writing other prose, read and apply the unslop skill.

## Visual and design work

* Do not edit real components first. For any non-trivial UI, layout, or copy change, build several distinct static mocks, publish them with the t3-preview skill, report the link, and stop. Wait for a pick before implementing.
* For an existing website or app, follow its colors, typography, spacing, and component conventions unless the user requests a redesign. This also applies to prototypes and new pages within that product.
* For standalone work without an existing design, use an information-dense layout with minimal copy, no decorative cards or pills, and no light-gray subtitle lines above sections. No em dashes.
* Avoid continuously repainting CSS animations (pulse, shimmer, blur, spinners); they peg the GPU on high-refresh displays.

## Live systems

* Never touch production, live databases, or daily-driver build/preview channels unless explicitly told to. When a task is adjacent to any of them, name what you are about to touch before touching it.
