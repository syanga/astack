# GLOSSARY.md format

Use the domain glossary identified by the caller or project instructions. When none is identified, use the default locations below.

## Structure

```md
# {Context Name}

{One or two sentence description of what this context is and why it exists.}

## Language

**Order**:
{A one or two sentence description of the term}
_Avoid_: Purchase, transaction

**Invoice**:
A request for payment sent to a customer after delivery.
_Avoid_: Bill, payment request

**Customer**:
A person or organization that places orders.
_Avoid_: Client, buyer, account
```

## Rules

- **Be opinionated.** When multiple words exist for the same concept, pick the best one and list the others under `_Avoid_`.
- **Keep definitions tight.** One or two sentences max. Define what it IS, not what it does.
- **Only include terms specific to this project's context.** General programming concepts (timeouts, error types, utility patterns) don't belong even if the project uses them extensively. Before adding a term, ask: is this a concept unique to this context, or a general programming concept? Only the former belongs.
- **Group terms under subheadings** when natural clusters emerge. If all terms belong to a single cohesive area, a flat list is fine.

## Single vs multi-context repos

**Single context (most repos):** One `GLOSSARY.md` at the repo root.

**Multiple contexts:** A `GLOSSARY-MAP.md` at the repo root lists the contexts, where they live, and how they relate to each other:

```md
# Glossary map

## Contexts

- [Ordering][ordering-context]: receives and tracks customer orders
- [Billing][billing-context]: generates invoices and processes payments
- [Fulfillment][fulfillment-context]: manages warehouse picking and shipping

[ordering-context]: ./src/ordering/GLOSSARY.md
[billing-context]: ./src/billing/GLOSSARY.md
[fulfillment-context]: ./src/fulfillment/GLOSSARY.md

## Relationships

- **Ordering → Fulfillment**: Ordering emits `OrderPlaced` events; Fulfillment consumes them to start picking
- **Fulfillment → Billing**: Fulfillment emits `ShipmentDispatched` events; Billing consumes them to generate invoices
- **Ordering ↔ Billing**: Shared types for `CustomerId` and `Money`
```

For the default layout:

- If `GLOSSARY-MAP.md` exists, read it to find contexts
- If only a root `GLOSSARY.md` exists, single context
- If neither exists, use the root `GLOSSARY.md` location for the first glossary

An existing `CONTEXT.md` or `CONTEXT-MAP.md`, this convention's earlier names, takes the place of `GLOSSARY.md` or `GLOSSARY-MAP.md`. Read and update it in place, and never create a second glossary beside it. New files use the new names.

When multiple contexts exist, infer which one the current topic relates to. If unclear, ask.
