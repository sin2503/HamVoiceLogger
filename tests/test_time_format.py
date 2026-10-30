"""Unit tests for JST/UTC display helpers."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from time_format import (
    format_dual_hint,
    format_timestamp,
    is_japan_entity,
    normalize_time_display,
)


class TimeFormatTests(unittest.TestCase):
    def test_normalize_time_display(self) -> None:
        self.assertEqual(normalize_time_display("UTC"), "utc")
        self.assertEqual(normalize_time_display("nope"), "both")

    def test_format_modes(self) -> None:
        value = "2026-10-02T17:05:00+09:00"
        self.assertIn("JST", format_timestamp(value, "jst"))
        self.assertIn("UTC", format_timestamp(value, "utc"))
        both = format_timestamp(value, "both")
        self.assertIn("JST", both)
        self.assertIn("UTC", both)

    def test_dual_hint_mentions_exports(self) -> None:
        moment = datetime(2026, 10, 2, 17, 5, tzinfo=timezone(timedelta(hours=9)))
        hint = format_dual_hint(moment)
        self.assertIn("JST", hint)
        self.assertIn("UTC", hint)
        self.assertIn("ADIF", hint)

    def test_japan_entity(self) -> None:
        self.assertTrue(is_japan_entity("339", "Japan"))
        self.assertTrue(is_japan_entity("", "日本"))
        self.assertFalse(is_japan_entity("291", "United States"))


if __name__ == "__main__":
    unittest.main()
