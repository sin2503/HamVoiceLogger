"""Unit tests for callsign dupe lookup."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from log_store import LogStore


class DupeLookupTests(unittest.TestCase):
    def test_lookup_counts_and_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LogStore(Path(tmp) / "logger.sqlite3")
            first = store.start_qso(frequency="145.000", mode="FM")
            store.end_qso(
                first,
                callsign="JA1YAA",
                frequency="145.000",
                mode="FM",
                notes="",
                callbook_info={"operator_name": "Taro", "qth": "Tokyo"},
            )
            second = store.start_qso(frequency="433.000", mode="FM")
            store.end_qso(
                second,
                callsign="JA1YAA",
                frequency="433.000",
                mode="FM",
                notes="",
                callbook_info={"operator_name": "Taro", "qth": "Yokohama"},
            )

            history = store.lookup_callsign_history("ja1yaa")
            self.assertEqual(history["total"], 2)
            self.assertEqual(history["completed"], 2)
            self.assertEqual(history["latest"]["frequency"], "433.000")
            self.assertEqual(history["latest"]["qth"], "Yokohama")

    def test_lookup_empty_for_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LogStore(Path(tmp) / "logger.sqlite3")
            history = store.lookup_callsign_history("JA1ZZZ")
            self.assertEqual(history["total"], 0)
            self.assertIsNone(history["latest"])


if __name__ == "__main__":
    unittest.main()
