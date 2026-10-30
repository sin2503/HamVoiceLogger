"""Unit tests for QSO history filters."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from log_store import LogStore


class LogStoreSearchTests(unittest.TestCase):
    def _seed(self, store: LogStore) -> None:
        a = store.start_qso(frequency="145.000", mode="FM")
        store.end_qso(
            a,
            callsign="JA1YAA",
            frequency="145.000",
            mode="FM",
            notes="",
            started_at="2026-01-10T12:00:00+09:00",
            qsl_sent="N",
            qsl_received="",
            is_dx=False,
        )
        b = store.start_qso(frequency="14.200", mode="SSB")
        store.end_qso(
            b,
            callsign="W1AW",
            frequency="14.200",
            mode="SSB",
            notes="dx",
            started_at="2026-03-15T12:00:00+09:00",
            callbook_info={"country": "United States", "dxcc": "291"},
            qsl_sent="Y",
            qsl_received="N",
            is_dx=True,
        )

    def test_mode_and_dx_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LogStore(Path(tmp) / "logger.sqlite3")
            self._seed(store)
            fm = store.search_qsos(mode="FM")
            self.assertEqual(len(fm), 1)
            self.assertEqual(fm[0]["callsign"], "JA1YAA")
            dx = store.search_qsos(dx_only=True)
            self.assertEqual(len(dx), 1)
            self.assertEqual(dx[0]["callsign"], "W1AW")

    def test_qsl_and_date_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LogStore(Path(tmp) / "logger.sqlite3")
            self._seed(store)
            pending = store.search_qsos(qsl="sent_pending")
            self.assertEqual([row["callsign"] for row in pending], ["JA1YAA"])
            march = store.search_qsos(date_from="2026-03-01", date_to="2026-03-31")
            self.assertEqual(len(march), 1)
            self.assertEqual(march[0]["callsign"], "W1AW")

    def test_text_and_filter_combine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LogStore(Path(tmp) / "logger.sqlite3")
            self._seed(store)
            rows = store.search_qsos("W1", mode="SSB", dx_only=True)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["callsign"], "W1AW")


if __name__ == "__main__":
    unittest.main()
