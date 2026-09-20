import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import Item
from exporter import export_catalog


class CaptureSink(io.StringIO):
    def __init__(self):
        super().__init__(newline="")
        self.saved = None
        self.close_count = 0

    def close(self):
        self.saved = self.getvalue()
        self.close_count += 1
        super().close()


class ExportTests(unittest.TestCase):
    def test_requested_order_and_row_count(self):
        sink = CaptureSink()
        items = [Item("a", "Apple", 3), Item("b", "Pear", 4)]
        count = export_catalog(items, ["b", "a"], lambda: sink)
        self.assertEqual(count, 2)
        self.assertEqual(sink.saved, "sku,name,quantity\r\nb,Pear,4\r\na,Apple,3\r\n")
        self.assertEqual(sink.close_count, 1)

    def test_empty_request_has_header(self):
        sink = CaptureSink()
        self.assertEqual(export_catalog([], [], lambda: sink), 0)
        self.assertEqual(sink.saved, "sku,name,quantity\r\n")
        self.assertTrue(sink.closed)

    def test_repeated_requests_remain_repeated(self):
        sink = CaptureSink()
        items = [Item("a", "Apple", 3)]
        self.assertEqual(export_catalog(items, ["a", "a"], lambda: sink), 2)
        self.assertEqual(sink.saved, "sku,name,quantity\r\na,Apple,3\r\na,Apple,3\r\n")

    def test_unicode_and_csv_quoting(self):
        sink = CaptureSink()
        items = [Item("a", 'Pêche, "ripe"', 3)]
        export_catalog(items, ["a"], lambda: sink)
        self.assertEqual(sink.saved, 'sku,name,quantity\r\na,"Pêche, ""ripe""",3\r\n')

    def test_catalog_inputs_unchanged(self):
        sink = CaptureSink()
        items = [Item("a", "Apple", 3)]
        requested = ["a"]
        export_catalog(items, requested, lambda: sink)
        self.assertEqual(items, [Item("a", "Apple", 3)])
        self.assertEqual(requested, ["a"])

    def test_sink_creation_error_propagates(self):
        def unavailable():
            raise OSError("sink unavailable")

        with self.assertRaisesRegex(OSError, "sink unavailable"):
            export_catalog([], [], unavailable)


if __name__ == "__main__":
    unittest.main()
