# Logic prototypes

Build one self-contained HTML file that lets the user drive a state model.
Write the question visibly at the top, in the project's domain language.

Keep the model separate from the page. Use the shape the question needs: a pure
reducer, explicit state machine, pure functions over data, or a module that owns
state. The page calls the model's interface. The model has no DOM dependencies.

Show the relevant state as labeled fields after every action. Use domain names
for buttons and values rather than implementation terms or raw JSON. Include:

- Free-play actions that let the user explore sequences in any order.
- A reset to a known starting state.
- Guided scenarios for the ordinary case and difficult or illegal transitions.
  Starting a scenario resets state, and each step performs a real action.
- A visible result when an action is rejected, so the user can judge the rule.

Keep CSS and JavaScript inline with no build step or external dependency. Use
a black background, white primary text, minimal copy, and no animation.

Exercise each guided scenario and at least one free-play sequence. Report what
the model permits, rejects, or leaves unresolved. Follow
[t3-preview](../t3-preview/SKILL.md) to deliver the artifact.

After feedback, record the decision and preserve any small model fragment that
expresses it more precisely than prose. Prototype behavior still needs tests
and production constraints checked when incorporated into real code.
