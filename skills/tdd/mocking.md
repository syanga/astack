# Control external dependencies

Keep the behavior under test real. Control external APIs, time, randomness,
and other nondeterministic inputs at the system boundary. Prefer a temporary
filesystem or isolated test database when it gives a reliable, affordable check.

Use the project's existing dependency injection points. Pass external clients
into the module rather than constructing credentialed clients inside the logic.
Prefer operations with domain names and typed inputs over a generic fetcher
that forces each test to recreate routing logic.

Avoid replacing internal collaborators just to assert their call order. Observe
the result through the interface callers use. If the contract is an outbound
effect, assert its concrete payload and destination at that external boundary.

A fake cannot establish compatibility with the real dependency. When that
compatibility is part of the change, verify the real integration in an isolated,
authorized environment and report any unavailable coverage.
