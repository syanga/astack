import csv
from collections.abc import Callable
from typing import TextIO

from catalog import Item, find_item


def export_catalog(
    items: list[Item], requested_skus: list[str], open_sink: Callable[[], TextIO]
) -> int:
    sink = open_sink()
    try:
        writer = csv.writer(sink)
        writer.writerow(["sku", "name", "quantity"])
        for sku in requested_skus:
            item = find_item(items, sku)
            row = [sku, "", ""] if item is None else [sku, item.name, item.quantity]
            writer.writerow(row)
        return len(requested_skus)
    finally:
        sink.close()
