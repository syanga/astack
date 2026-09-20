from dataclasses import dataclass


@dataclass
class Item:
    sku: str
    name: str
    quantity: int


def find_item(items: list[Item], sku: str) -> Item | None:
    for item in items:
        if item.sku == sku:
            return item
    return None
