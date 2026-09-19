# UI prototypes

Build several distinct static mocks before changing real components. Default
to three alternatives when the user has not specified a count.

## Make the alternatives comparable

Reproduce enough surrounding context to judge each design: the header,
navigation, realistic sample data, density, and important empty or error states.
Keep the input data and task consistent across variants.

Make the alternatives differ in layout, information hierarchy, or primary
interaction. Color and wording changes alone do not explore the design.
Give each variant its own layout so a shared abstraction does not force them
into the same shape.

Use self-contained HTML with inline CSS and JavaScript. Keep the background
true black, primary text white, and the layout dense. Use minimal copy, no
decorative card or pill chrome, no light-gray subtitle lines above sections,
and no continuously repainting animations. Honor explicit task-specific design
directions when they override these defaults.

## Compare and inspect

Put variants behind a labeled switcher with shareable `?variant=` values, or
provide separate clearly labeled HTML files. A switcher belongs to the preview,
not to a real app route. If arrow keys switch variants, preserve normal keyboard
behavior inside editable fields.

Follow [t3-preview](../t3-preview/SKILL.md) for delivery and browser inspection.
When browser automation is available, inspect every variant and exercise the
interactions relevant to the decision. Otherwise report the source checks and
the visual checks still needed.

Provide the preview link, explain the useful tradeoffs, recommend a direction,
and stop for a pick. Record combinations the user chooses across alternatives
before production implementation begins.
