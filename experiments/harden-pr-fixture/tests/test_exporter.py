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


class FailingSink(CaptureSink):
    def __init__(self, fail_on_write):
        super().__init__()
        self.fail_on_write = fail_on_write
        self.write_count = 0

    def write(self, value):
        self.write_count += 1
        if self.write_count == self.fail_on_write:
            raise OSError("write failed")
        return super().write(value)


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

    def test_missing_request_has_empty_fields_and_export_continues(self):
        sink = CaptureSink()
        items = [Item("a", "Apple", 3)]
        count = export_catalog(items, ["missing", "a"], lambda: sink)
        self.assertEqual(count, 2)
        self.assertEqual(
            sink.saved, "sku,name,quantity\r\nmissing,,\r\na,Apple,3\r\n"
        )
        self.assertEqual(sink.close_count, 1)

    def test_first_duplicate_and_later_edits_are_observed(self):
        first = Item("a", "Apple", 3)
        items = [first, Item("a", "Alternate", 8)]
        first_sink = CaptureSink()
        export_catalog(items, ["a"], lambda: first_sink)
        self.assertEqual(first_sink.saved, "sku,name,quantity\r\na,Apple,3\r\n")

        first.name = "Apricot"
        first.quantity = 5
        second_sink = CaptureSink()
        export_catalog(items, ["a"], lambda: second_sink)
        self.assertEqual(second_sink.saved, "sku,name,quantity\r\na,Apricot,5\r\n")

    def test_unicode_and_csv_quoting(self):
        sink = CaptureSink()
        items = [Item("a", 'Pêche, "ripe"', 3)]
        export_catalog(items, ["a"], lambda: sink)
        self.assertEqual(sink.saved, 'sku,name,quantity\r\na,"Pêche, ""ripe""",3\r\n')

    def test_embedded_newline_uses_csv_quoting(self):
        sink = CaptureSink()
        items = [Item("a", "line 1\nline 2", 3)]
        self.assertEqual(export_catalog(items, ["a"], lambda: sink), 1)
        self.assertEqual(
            sink.saved, 'sku,name,quantity\r\na,"line 1\nline 2",3\r\n'
        )

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

    def test_write_errors_propagate_after_closing_sink(self):
        for fail_on_write in (1, 2):
            with self.subTest(fail_on_write=fail_on_write):
                sink = FailingSink(fail_on_write)
                with self.assertRaisesRegex(OSError, "write failed"):
                    export_catalog(
                        [Item("a", "Apple", 3)], ["a"], lambda: sink
                    )
                self.assertEqual(sink.close_count, 1)


if __name__ == "__main__":
    unittest.main()
