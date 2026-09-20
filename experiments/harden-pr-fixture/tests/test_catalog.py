import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import Item, find_item


class CatalogTests(unittest.TestCase):
    def test_first_duplicate_wins(self):
        items = [Item("x", "first", 2), Item("x", "second", 9)]
        self.assertEqual(find_item(items, "x").name, "first")

    def test_missing_is_none(self):
        self.assertIsNone(find_item([Item("x", "known", 2)], "missing"))
        self.assertEqual(find_item([Item("x", "known", 2)], "x").name, "known")

    def test_later_calls_observe_edits(self):
        items = [Item("x", "first", 2)]
        self.assertEqual(find_item(items, "x").quantity, 2)
        items[0].quantity = 7
        self.assertEqual(find_item(items, "x").quantity, 7)


if __name__ == "__main__":
    unittest.main()
