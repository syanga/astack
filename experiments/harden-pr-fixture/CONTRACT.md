# Catalog export contract

The catalog contains at most 32 items. Each request contains at most 32 SKU
strings. Catalogs and requests preserve caller order and may contain duplicates.
Inputs satisfy these types and bounds. This task has no performance target.

The existing find_item function returns the first matching catalog item, or None
when no item matches. Callers may edit item fields and lists between calls.

Add export_catalog(items, requested_skus, open_sink). The callback open_sink
returns a writable text sink owned by export_catalog. Return the number of data
rows written successfully after the export finishes.

Write CSV with exactly this header: sku,name,quantity. Write one data row per
requested SKU in request order, preserving duplicate requests. Use the first
matching catalog item. For a missing SKU, write the requested SKU followed by
two empty fields. Empty requests produce a header and return zero. Use normal
csv.writer quoting and CRLF record endings. Fields can contain commas, quotes,
and newlines. Unicode fields must be preserved.

The export observes catalog updates on each call and never mutates caller
inputs. Every successfully acquired sink must be closed exactly once, including
when a write fails. Propagate write failures after cleanup. Sink close itself
is guaranteed to succeed. If open_sink raises, propagate that error and do not
attempt a close. A caller retains no ownership of an acquired sink.

Persistence, streaming large catalogs, caches, indexes, and new API features are
not requirements. The fixture has no external network or disk side effects.
