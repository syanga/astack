---
name: typescript-best-practices
description: TypeScript guidance for writing or reviewing .ts and .tsx code, especially domain types, boundary validation, narrowing, and exhaustive handling.
---

# TypeScript best practices

Apply [type system discipline](../principles/type-system-discipline.md) first. Follow the repository's compiler settings, schema libraries, and established type conventions. Apply these rules to the requested change. Optional style improvements remain advice and do not expand implementation or review scope.

| Rule | Summary |
|------|---------|
| Discriminated unions | Model mutually exclusive variants with a literal discriminant so impossible combinations cannot be represented. Use the repository's discriminant name. |
| Branded types | Distinguish semantic primitives where mixing them would be a defect. Reuse the repository's brand convention and validate at construction. |
| Constructive modeling | Build the shape so the illegal value cannot be constructed, such as `[T, ...T[]]` for non-empty or `[T, T][]` for pairs. Numeric bounds still need validation. |
| Simplest total type | Keep `T[]` while every operation on it stays total. Strengthen to `NonEmpty<T>` only where the loose type forces `!`, a cast, or a "should never happen" throw. |
| `unknown` over `any` | External data is `unknown`. |
| Schemas before guards | Before hand-writing a property-by-property type guard, use the repository's runtime schema library and infer the type from the schema, such as `z.infer`. |
| Type assertions | Prefer narrowing to assertions. Justify unavoidable assertions with validation or a demonstrated invariant. `as const` preserves literals; it does not validate external data. |
| Narrowing hierarchy | Discriminant switch > `in` operator > `typeof`/`instanceof` > user-defined type guard > `as`. |
| Type guards | Must verify the claim. A lying guard is worse than `as` because the bug hides behind a name that says it's safe. Name them `isX` or `hasX`. |
| Exhaustiveness | Inline `const _exhaustive: never = x;` in default arms so the compiler errors when a new variant is added. |
| `satisfies` over `as` | Check assignability without replacing the expression's inferred type with the target type. It supplies no runtime validation. |
| Boundary validation | Parse external data into a named domain type under [boundary discipline](../principles/boundary-discipline.md). Keep intentionally open metadata as such. Trust established types inside. |
| Schema-derived types | Reach for `Pick`/`Omit`/`Parameters`/`ReturnType`/`Awaited`/`typeof` before declaring a new interface. |
| Object args | Prefer named options where positional arguments are easy to confuse. Keep simple positional APIs and existing conventions when clear. Measure allocation concerns before changing a hot path. |
| Real tests | Follow [test behavior](../principles/test-behavior-not-implementation.md). Exercise real behavior and cleanup. Isolate external dependencies at their boundaries when needed. |
| Structured telemetry | Use the repository's logger with enough context to debug from an id. Preserve intentional CLI output contracts. |

Read the relevant [patterns](references/patterns.md) for examples. Run the repository's typecheck and affected tests. For review, report demonstrated defects separately from optional advice under the caller's finding criteria.
