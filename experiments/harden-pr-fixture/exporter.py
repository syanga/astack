import csv
from collections.abc import Callable
from typing import TextIO

from catalog import Item


def export_catalog(
    items: list[Item], requested_skus: list[str], open_sink: Callable[[], TextIO]
) -> int:
    sink = open_sink()
    writer = csv.writer(sink)
    writer.writerow(["sku", "name", "quantity"])
    count = 0
    for sku in requested_skus:
        item = next(item for item in items if item.sku == sku)
        writer.writerow([sku, item.name, item.quantity])
        count += 1
    sink.close()
    return count
