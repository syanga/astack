# Migrate callers then delete legacy APIs

Apply when introducing a new internal API while old callers still exist.

When we decide a new API is the right design, migrate callers and remove the old API in the same refactor wave instead of preserving compatibility layers.

**When this applies:**
- No external users depend on backward compatibility
- The project can absorb coordinated breaking changes
- The new API is part of a simplification or refactor initiative

**Rule:**
- Inventory callers, migrate them, and delete the old API in the same wave
- Treat temporary adapters as exceptional and time-boxed, not default architecture
- Update tests to assert the new contract, and delete tests that only protect pre-refactor implementation details

Keeping both old and new APIs leaves two paths to maintain, test, and read.
