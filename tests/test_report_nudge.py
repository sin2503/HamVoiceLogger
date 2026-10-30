"""Unit tests for RST-like report nudging."""

from __future__ import annotations

import unittest

from gui import nudge_report_value


class ReportNudgeTests(unittest.TestCase):
    def test_empty_becomes_59(self) -> None:
        self.assertEqual(nudge_report_value("", 1), "59")
        self.assertEqual(nudge_report_value("", -1), "59")

    def test_rs_steps_and_clamps(self) -> None:
        self.assertEqual(nudge_report_value("58", 1), "59")
        self.assertEqual(nudge_report_value("59", 1), "59")
        self.assertEqual(nudge_report_value("59", -1), "58")
        self.assertEqual(nudge_report_value("51", -1), "49")
        self.assertEqual(nudge_report_value("11", -1), "11")
        self.assertEqual(nudge_report_value("１９", 1), "21")

    def test_rst_steps_and_clamps(self) -> None:
        self.assertEqual(nudge_report_value("598", 1), "599")
        self.assertEqual(nudge_report_value("599", 1), "599")
        self.assertEqual(nudge_report_value("599", -1), "598")
        self.assertEqual(nudge_report_value("111", -1), "111")
        self.assertEqual(nudge_report_value("119", 1), "121")

    def test_invalid_numeric_snaps(self) -> None:
        self.assertEqual(nudge_report_value("60", 1), "59")
        self.assertEqual(nudge_report_value("60", -1), "11")
        self.assertEqual(nudge_report_value("00", 1), "59")

    def test_non_numeric_unchanged(self) -> None:
        self.assertEqual(nudge_report_value("5NN", 1), "5NN")


if __name__ == "__main__":
    unittest.main()
