# Encode Lessons in Structure

Apply when you catch yourself writing the same instruction a second time, or notice a recurring correction.

Encode a recurring fix as a mechanism instead of a textual instruction. Every error, human correction, and unexpected outcome is a learning signal.

**Why:** Textual instructions are easy to miss. They require the reader to notice, remember, and comply. A mechanism enforces the rule without cooperation.

**Pattern:**
When you catch yourself writing the same instruction a second time:
1. Ask whether a mechanism can enforce it. Pick the strongest the situation allows: an unrepresentable state that cannot compile, then a lint or banned API that fails CI, then a canonical helper, then a runtime check. Agents copy whatever the surrounding code already does, and a weaker guard becomes the next template.
2. If one can, encode it and delete the instruction. When a structural fix exists, the instruction it replaces is the symptom.
3. If none can, because the rule needs judgment, make the instruction more prominent and add an example of the failure mode.

**Feedback loop:**
- **Capture every correction.** When the human intervenes or tests fail, decide if it is a one-off or a pattern.
- **Route to the right layer.** One-off -> a note in the reply. Recurring fix -> skill or lint rule. Systemic issue -> principle.
- **Close the loop.** Apply now or create a concrete todo. "I'll keep that in mind" does not persist.

**The test:** the diff contains the lint rule, check, or type change, and the instruction it replaced is gone.
