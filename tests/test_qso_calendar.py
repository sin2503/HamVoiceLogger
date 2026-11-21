"""Unit tests for the JST QSO history calendar."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from log_store import LogStore
from qso_calendar import counts_for_month, jst_date_of, month_weeks, shift_month


class QsoCalendarTests(unittest.TestCase):
    def test_weeks_start_on_sunday(self) -> None:
        weeks = month_weeks(2026, 11)
        self.assertEqual(weeks[0][0], 1)
        self.assertEqual(weeks[0][6], 7)
        self.assertEqual(len(weeks[0]), 7)

    def test_shift_month_wraps_the_year(self) -> None:
        self.assertEqual(shift_month(2026, 1, -1), (2025, 12))
        self.assertEqual(shift_month(2026, 12, 1), (2027, 1))

    def test_jst_date_crosses_utc_midnight(self) -> None:
        # 2026-11-20 15:30 UTC is 2026-11-21 00:30 JST.
        self.assertEqual(jst_date_of("2026-11-20T15:30:00+00:00"), date(2026, 11, 21))
        self.assertEqual(jst_date_of("2026-11-20T12:00:00+09:00"), date(2026, 11, 20))

    def test_counts_use_jst_day(self) -> None:
        counts = counts_for_month(
            [
                "2026-11-20T15:30:00+00:00",
                "2026-11-20T12:00:00+09:00",
                "2026-10-31T15:30:00+00:00",
            ],
            2026,
            11,
        )
        self.assertEqual(counts, {21: 1, 20: 1, 1: 1})

    def test_store_marks_follow_jst(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LogStore(Path(tmp) / "logger.sqlite3")
            qso_id = store.start_qso(frequency="14.074000", mode="FT8")
            store.end_qso(
                qso_id,
                callsign="JA1YAA",
                frequency="14.074000",
                mode="FT8",
                notes="",
                started_at="2026-11-20T15:30:00+00:00",
            )
            counts = counts_for_month(store.started_at_values(), 2026, 11)
            self.assertEqual(counts, {21: 1})
            fm = store.started_at_values(mode="FM")
            self.assertEqual(fm, [])


if __name__ == "__main__":
    unittest.main()
