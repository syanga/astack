# Check the shared browser

Use T3's `preview_*` tools for browser checks. If they are absent, report browser checks as unverified.

1. Call `preview_status`. If no automation-capable tab is attached, call `preview_open` before concluding that the browser is unavailable.
2. Inspect the returned tab ID, URL, title, and visibility. Use the returned `tabId` explicitly for subsequent calls when supported.
3. Navigate to the intended URL when needed, then inspect the resulting page with `preview_snapshot` before interacting.
4. After navigation, resize, or a timeout, recheck status and page content against the intended URL and authentication state. A matching tab ID alone does not establish session continuity.
5. For responsive layout checks, confirm the measured CSS viewport using status or `window.innerWidth` and `window.innerHeight`. Report the measured size. A requested preset alone does not verify that size.

## Resolve conflicting session state

An automation-capable hidden tab does not establish which browser the user sees. If automation shows Sign In while the user reports being signed in, treat those observations as an unresolved session discrepancy.

Inspect the tool error and available session metadata. Retry with corrected arguments when the error identifies an actionable correction, then recheck status and page content. Reopening alone does not prove recovery.

Before requesting login, establish that the intended shared tab is visible to the user and that its page requires authentication. If the discrepancy remains unresolved, report the conflicting observations and affected checks as unverified. Resume those checks only after confirming the intended page state.

Use another browser system only when T3's tools are absent, `preview_open` explicitly reports unsupported or unavailable, or the user requests another browser.
