# TypeScript patterns

Examples for [TypeScript best practices](../SKILL.md). Shared rules live in [type system discipline](../../principles/type-system-discipline.md) and [boundary discipline](../../principles/boundary-discipline.md).

## Branded types

```ts
type AgentId = string & { readonly __brand: "AgentId" };

function parseAgentId(input: string): AgentId {
  if (!isUUID(input)) throw new Error(`Invalid agent id: ${input}`);
  return input as AgentId;
}

declare function focusAgent(id: AgentId): void;
```

This example uses a string brand. Reuse an existing brand convention when the repository has one.

## Discriminated unions

```ts
type DiffState = { loading: boolean; diff?: GitDiff; error?: string };
```

Replace the boolean and optional fields with explicit variants:

```ts
type DiffState =
  | { kind: "loading" }
  | { kind: "ready"; diff: GitDiff }
  | { kind: "error"; error: string };
```

Pick one discriminant name (`kind`, `type`, `tag`) and stick to it.

## Constructive modeling

Non-empty, via a variadic tuple. The required first element remains accessible under `noUncheckedIndexedAccess`:

```ts
type NonEmpty<T> = [T, ...T[]];

function firstEntry(entries: NonEmpty<string>): string {
  return entries[0];
}
```

An arbitrary numeric index still needs an absence check, even with a non-empty tuple.

Where a plain `T[]` arrives, narrow once with a guard. The fact then travels in the type:

```ts
const isNonEmpty = <T>(arr: T[]): arr is NonEmpty<T> => arr.length > 0;
```

Even length, as pairs:

```ts
type Pairs<T> = [T, T][];
```

A time range can use a start plus a validated duration:

```ts
type DurationMs = number & { readonly __brand: "DurationMs" };

function parseDurationMs(value: number): DurationMs {
  if (!Number.isFinite(value) || value < 0) {
    throw new Error("expected a finite nonnegative duration");
  }
  return value as DurationMs;
}

type TimeRange = { start: Date; durationMs: DurationMs };
```

A plain `number` permits negative durations. The constructor establishes the bound; the brand carries it. Validate the start date and representable end date too when the contract requires valid dates.

## Simplest total type

Don't strengthen everything. Keep `T[]` when every operation on it is total:

```ts
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
```

Strengthen when the loose type forces a lie at a use site. The tells are `!`, `arr[0] as T`, and a "should never happen" throw:

```ts
function newestSession(sessions: Session[]): Session {
  return sessions.at(0)!;
}
```

Require a non-empty input when the operation needs a first element:

```ts
function newestSession(sessions: NonEmpty<Session>): Session {
  return sessions[0];
}
```

Weakening the result to `Session | undefined` is the other total signature.

## `unknown` over `any`

```ts
function handle(input: any) {
  return input.foo.bar;
}
```

Accept `unknown` and establish each property before using it:

```ts
function handle(input: unknown) {
  if (typeof input === "object" && input !== null && "foo" in input) {
    return input.foo;
  }
  throw new Error("expected an object with foo");
}
```

## Schemas before hand-rolled guards

With Zod, derive the type from the schema that validates the input:

```ts
import { z } from "zod";

const UserSchema = z.object({
  id: z.string().uuid(),
  role: z.enum(["admin", "member"]),
});

type User = z.infer<typeof UserSchema>;

function parseUser(input: unknown): User {
  return UserSchema.parse(input);
}
```

Use `safeParse` when failure is an expected branch. Use the equivalent inference helper when the repository uses another schema library. Do not add a new schema dependency for one guard.

## Type assertions

When removing an assertion, identify why TypeScript cannot infer the type:

- Missing discriminant: model the variants as a discriminated union.
- An overly wide source type: narrow it.
- Untyped boundary: add a parse function or reuse the repository's schema.
- A fact TypeScript cannot express: keep the smallest assertion supported by the invariant.

`as const` narrows literal inference and makes literal properties readonly in the type. It does not freeze the object at runtime or validate its contents.

## Narrowing hierarchy

From best to last-resort:

1. **Discriminated union switch / if.** Compiler narrows automatically.
2. **`in` operator.** `"key" in obj` narrows to variants containing that key.
3. **`typeof` / `instanceof`.** For primitives and class instances.
4. **User-defined type guard.** When the above aren't enough.
5. **Type assertion.** Only with validation or an established invariant.

```ts
function area(s: Shape): number {
  if ("radius" in s) return Math.PI * s.radius ** 2;
  return s.width * s.height;
}
```

## Type guards

```ts
function isCircle(s: Shape): s is Shape & { kind: "circle" } {
  return s.kind === "circle";
}
```

## Exhaustiveness

In default arms, assign the narrowed value to a `never`-typed local.

```ts
function area(s: Shape): number {
  switch (s.kind) {
    case "circle":
      return Math.PI * s.radius ** 2;
    case "rect":
      return s.width * s.height;
    default: {
      const _exhaustive: never = s;
      return _exhaustive;
    }
  }
}

function handle(s: Shape): void {
  switch (s.kind) {
    case "circle":
      drawCircle(s);
      break;
    case "rect":
      drawRect(s);
      break;
    default: {
      const _exhaustive: never = s;
      void _exhaustive;
    }
  }
}
```

Return-style in value-returning switches, void-style in statement switches.

## `satisfies` over `as`

`satisfies` checks assignability without replacing the inferred expression type with the target type. Contextual typing can still affect inference. It does not validate at runtime.

```ts
type Config = { theme: "dark" | "light"; cols: number };

const asserted = { theme: "dark", cols: 3 } as Config;

const checked = { theme: "dark", cols: 3 } satisfies Config;
```

## Boundary validation

Keep intentionally open metadata as `Record<string, unknown>`.

- **Wire formats:** follow the protocol's unknown-field policy. Ignore additional fields only where the contract permits them.
- **Persisted JSON:** versioned blob with a try/catch around the parse.

## Schema-derived types

When a `.proto`, OpenAPI spec, GraphQL schema, or database migration already defines a shape, derive from the generated types instead of duplicating them.

```ts
type CheckSummary = {
  totalCount: number;
  checks: { name: string; status: string }[];
};
declare function renderChecks(s: CheckSummary): void;
```

Derive the consumed fields from the generated type instead:

```ts
import type { ChecksMessage } from "<generated module>";
declare function renderChecks(
  s: Pick<ChecksMessage, "totalCount" | "checks">,
): void;
```

## Object args

```ts
openFile(uri, 10, 1, 10, 1);
```

Named options expose the meaning of each coordinate:

```ts
openFile({
  uri,
  selection: {
    startLineNumber: 10,
    startColumn: 1,
    endLineNumber: 10,
    endColumn: 1,
  },
});
```
