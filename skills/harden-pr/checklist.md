# Core review checklist

Apply these checks to the captured diff and relevant surrounding code. Investigate each pattern against the actual behavior; its presence alone does not establish a defect.

## SQL & Data Safety
- String interpolation in SQL (even if values are `.to_i`/`.to_f` — use parameterized queries (Rails: sanitize_sql_array/Arel; Node: prepared statements; Python: parameterized queries))
- TOCTOU races: check-then-set patterns that should be atomic `WHERE` + `update_all`
- Bypassing model validations for direct DB writes (Rails: update_column; Django: QuerySet.update(); Prisma: raw queries)
- N+1 queries: Missing eager loading (Rails: .includes(); SQLAlchemy: joinedload(); Prisma: include) for associations used in loops/views

## Race Conditions & Concurrency
- Read-check-write without uniqueness constraint or catch duplicate key error and retry (e.g., `where(hash:).first` then `save!` without handling concurrent insert)
- find-or-create without unique DB index — concurrent calls can create duplicates
- Status transitions that don't use atomic `WHERE old_status = ? UPDATE SET new_status` — concurrent updates can skip or double-apply transitions

## Unsafe HTML rendering

- Unsafe HTML rendering (Rails: .html_safe/raw(); React: dangerouslySetInnerHTML; Vue: v-html; Django: |safe/mark_safe) on user-controlled data (XSS)

## LLM Output Trust Boundary
- LLM-generated values (emails, URLs, names) written to DB or passed to mailers without format validation. Add lightweight guards (`EMAIL_REGEXP`, `URI.parse`, `.strip`) before persisting.
- Structured tool output (arrays, hashes) accepted without type/shape checks before database writes.
- LLM-generated URLs fetched without allowlist — SSRF risk if URL points to internal network (Python: `urllib.parse.urlparse` → check hostname against blocklist before `requests.get`/`httpx.get`)
- LLM output stored in knowledge bases or vector DBs without sanitization — stored prompt injection risk

## Shell Injection (Python-specific)
- `subprocess.run()` / `subprocess.call()` / `subprocess.Popen()` with `shell=True` AND f-string/`.format()` interpolation in the command string — use argument arrays instead
- `os.system()` with variable interpolation — replace with `subprocess.run()` using argument arrays
- `eval()` / `exec()` on LLM-generated code without sandboxing

## Enum & Value Completeness
When the diff introduces a new enum value, status string, tier name, or type constant:
- **Trace it through every consumer.** Read (don't just grep — READ) each file that switches on, filters by, or displays that value. If any consumer doesn't handle the new value, flag it. Common miss: adding a value to the frontend dropdown but the backend model/compute method doesn't persist it.
- **Check allowlists/filter arrays.** Search for arrays or `%w[]` lists containing sibling values (e.g., if adding "revise" to tiers, find every `%w[quick lfg mega]` and verify "revise" is included where needed).
- **Check `case`/`if-elsif` chains.** If existing code branches on the enum, does the new value fall through to a wrong default?

## Async/Sync Mixing (Python-specific)
- Synchronous `subprocess.run()`, `open()`, `requests.get()` inside `async def` endpoints — blocks the event loop. Use `asyncio.to_thread()`, `aiofiles`, or `httpx.AsyncClient` instead.
- `time.sleep()` inside async functions — use `asyncio.sleep()`
- Sync DB calls in async context without `run_in_executor()` wrapping

## Column/Field Name Safety
- Verify column names in ORM queries (`.select()`, `.eq()`, `.gte()`, `.order()`) against actual DB schema — wrong column names silently return empty results or throw swallowed errors
- Check `.get()` calls on query results use the column name that was actually selected
- Cross-reference with schema documentation when available

## Dead Code & Consistency (version/changelog only — other items handled by maintainability specialist)
- Version mismatch between PR title and VERSION/CHANGELOG files
- CHANGELOG entries that describe changes inaccurately (e.g., "changed from X to Y" when X never existed)

## LLM Prompt Issues
- Indexing conventions in prompts that disagree with the parser or examples
- Prompt text listing available tools/capabilities that don't match what's actually wired up in the `tool_classes`/`tools` array
- Word/token limits stated in multiple places that could drift

## Completeness Gaps
- Shortcut implementations that omit required behavior (e.g., partial enum handling, incomplete error paths, missing edge cases that are straightforward to add)
- Test coverage gaps in required behavior (e.g., missing negative-path tests, missing edge case tests that mirror happy-path structure)
- Required behavior left incomplete

## Time Window Safety
- Date-key lookups that assume "today" covers 24h — report at 8am PT only sees midnight→8am under today's key
- Mismatched time windows between related features — one uses hourly buckets, another uses daily keys for the same data

## Type Coercion at Boundaries
- Values crossing Ruby→JSON→JS boundaries where type could change (numeric vs string) — hash/digest inputs must normalize types
- Hash/digest inputs whose serialization violates the contract — `{ cores: 8 }` vs `{ cores: "8" }` produce different hashes; normalize only if the contract treats them as equivalent

## View/Frontend
- Inline `<style>` blocks in partials (re-parsed every render)
- O(n*m) lookups in views (`Array#find` in a loop instead of `index_by` hash)
- Ruby-side `.select{}` filtering on DB results that could be a `WHERE` clause (unless intentionally avoiding leading-wildcard `LIKE`)

### Frontend behavior

For frontend changes, inspect the full affected components and their rendered behavior when source alone cannot establish a finding:

- Keyboard navigation or focus indicators lost through styling or custom controls.
- Interactive controls with missing accessible names, incorrect semantics, or broken state and error feedback.
- Heading structure that fails to convey the content hierarchy; inspect the assembled page rather than assuming each component stands alone.
- Fixed dimensions or overflow that make content or controls unusable at supported viewport sizes, zoom levels, or input methods.
- Targets that are difficult to activate with supported input methods; verify rendered size and spacing.
- Violations of documented project requirements, including design tokens when the project requires them.

Use a configured repository detector when relevant, verify its findings, and merge duplicates. State when visual or interaction verification remains unavailable. Apply the user's design-selection workflow before changing UI.

## Distribution & CI/CD Pipeline
- CI/CD workflow changes (`.github/workflows/`): verify build tool versions match project requirements, artifact names/paths are correct, secrets use `${{ secrets.X }}` not hardcoded values
- New artifact types (CLI binary, library, package): verify a publish/release workflow exists and targets correct platforms
- Cross-platform builds: verify CI matrix covers all target OS/arch combinations, or documents which are untested
- Version tag format consistency: `v1.2.3` vs `1.2.3` — must match across VERSION file, git tags, and publish scripts
- Publish step idempotency: re-running the publish workflow should not fail (e.g., detect an existing release and resume only missing uploads)

**DO NOT flag:**
- Web services with existing auto-deploy pipelines (Docker build + K8s deploy)
- Internal tools not distributed outside the team
- Test-only CI changes (adding test steps, not publish steps)

---

## Suppressions — DO NOT flag these

- "X is redundant with Y" when the redundancy is harmless and aids readability (e.g., `present?` redundant with `length > 20`)
- "Add a comment explaining why this threshold/constant was chosen" — thresholds change during tuning, comments rot
- "This assertion could be tighter" when the assertion already covers the behavior
- Suggesting consistency-only changes (wrapping a value in a conditional to match how another constant is guarded)
- "Regex doesn't handle edge case X" when the input is constrained and X never occurs in practice
- "Test exercises multiple guards simultaneously" — that's fine, tests don't need to isolate every guard
- Eval threshold changes (max_actionable, min scores) — these are tuned empirically and change constantly
- Harmless no-ops (e.g., `.reject` on an element that's never in the array)
- ANYTHING already addressed in the diff you're reviewing — read the FULL diff before commenting
- Deliberately accepted debt supported by a linked issue, decision record, or the user's prior decision. Verify the reference and whether the decision still applies; a comment alone does not establish acceptance.
