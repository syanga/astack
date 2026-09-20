---
name: typescript-best-practices
description: TypeScript guidance. Use when writing or reviewing .ts or .tsx code.
---

# TypeScript best practices

Apply [type system discipline](../principles/type-system-discipline.md) first. Follow the repository's compiler settings, schema libraries, and established type conventions. Apply these rules to the requested change.

| Rule | Summary |
|------|---------|
| Schemas before guards | Before hand-writing a property-by-property type guard, use the repository's runtime schema library and infer the type from the schema, such as `z.infer`. |
| Type assertions | Prefer narrowing to assertions. Justify unavoidable assertions with validation or a demonstrated invariant. `as const` preserves literals; it does not validate external data. |
| Narrowing hierarchy | Discriminant switch > `in` operator > `typeof`/`instanceof` > user-defined type guard > `as`. |
| Type guards | Verify every property the predicate claims. Name guards `isX` or `hasX`. |
| Exhaustiveness | Inline `const _exhaustive: never = x;` in default arms so the compiler errors when a new variant is added. |
| `satisfies` over `as` | Check assignability without replacing the expression's inferred type with the target type. It supplies no runtime validation. |
| Schema-derived types | Reach for `Pick`/`Omit`/`Parameters`/`ReturnType`/`Awaited`/`typeof` before declaring a new interface. |
| Object args | Prefer named options where positional arguments are easy to confuse. Keep simple positional APIs and existing conventions when clear. Measure allocation concerns before changing a hot path. |

Read the relevant [patterns](references/patterns.md) for examples. The calling workflow owns verification, evidence reuse, and finding disposition. Optional style advice does not expand scope.
