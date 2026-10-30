"""Unit tests for QSO end / force-end helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from log_store import LogStore


class LogStoreQsoTests(unittest.TestCase):
    def test_force_end_marks_open_qso(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LogStore(Path(tmp) / "logger.sqlite3")
            qso_id = store.start_qso(frequency="145.000", mode="FM")
            store.force_end_qso(qso_id, note="[forced]")
            rows = store.search_qsos(limit=10)
            self.assertEqual(len(rows), 1)
            self.assertIsNotNone(rows[0]["ended_at"])
            self.assertIn("[forced]", rows[0]["notes"])

    def test_force_end_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LogStore(Path(tmp) / "logger.sqlite3")
            qso_id = store.start_qso()
            store.end_qso(
                qso_id,
                callsign="JA1YAA",
                frequency="",
                mode="FM",
                notes="done",
            )
            store.force_end_qso(qso_id, note="[forced]")
            rows = store.search_qsos(limit=10)
            self.assertEqual(rows[0]["notes"], "done")

    def test_delete_qsos_removes_rows_and_detaches_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LogStore(Path(tmp) / "logger.sqlite3")
            keep_id = store.add_completed_qso(
                {
                    "callsign": "JA1YAA",
                    "started_at": "2024-01-01T00:00:00+00:00",
                    "ended_at": "2024-01-01T00:01:00+00:00",
                    "frequency": "145.000",
                    "mode": "FM",
                }
            )
            delete_id = store.add_completed_qso(
                {
                    "callsign": "7L1ABC",
                    "started_at": "2024-01-02T00:00:00+00:00",
                    "ended_at": "2024-01-02T00:01:00+00:00",
                    "frequency": "433.000",
                    "mode": "FM",
                }
            )
            store.save_candidate("7L1ABC", "november lima one", qso_id=delete_id)
            deleted = store.delete_qsos([delete_id, delete_id, 0, -1])
            self.assertEqual(deleted, 1)
            rows = store.search_qsos(limit=20)
            self.assertEqual([row["id"] for row in rows], [keep_id])
            candidates = store.recent_candidates(limit=20)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["callsign"], "7L1ABC")
            self.assertIsNone(candidates[0]["qso_id"])

    def test_locked_qsos_are_not_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LogStore(Path(tmp) / "logger.sqlite3")
            locked_id = store.add_completed_qso(
                {
                    "callsign": "JA1YAA",
                    "started_at": "2024-01-01T00:00:00+00:00",
                    "ended_at": "2024-01-01T00:01:00+00:00",
                    "mode": "FM",
                }
            )
            free_id = store.add_completed_qso(
                {
                    "callsign": "7L1ABC",
                    "started_at": "2024-01-02T00:00:00+00:00",
                    "ended_at": "2024-01-02T00:01:00+00:00",
                    "mode": "FM",
                }
            )
            self.assertEqual(store.set_qso_locked([locked_id], True), 1)
            rows = store.search_qsos(limit=10)
            by_id = {row["id"]: row for row in rows}
            self.assertEqual(by_id[locked_id]["is_locked"], "1")
            deleted = store.delete_qsos([locked_id, free_id])
            self.assertEqual(deleted, 1)
            remaining = {row["id"] for row in store.search_qsos(limit=10)}
            self.assertEqual(remaining, {locked_id})
            self.assertEqual(store.set_qso_locked([locked_id], False), 1)
            self.assertEqual(store.delete_qsos([locked_id]), 1)
            self.assertEqual(store.search_qsos(limit=10), [])

    def test_update_qso_edits_completed_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LogStore(Path(tmp) / "logger.sqlite3")
            qso_id = store.add_completed_qso(
                {
                    "callsign": "JA1YAA",
                    "started_at": "2024-01-01T00:00:00+00:00",
                    "ended_at": "2024-01-01T00:01:00+00:00",
                    "mode": "FM",
                    "notes": "old",
                }
            )
            store.set_qso_locked([qso_id], True)
            store.update_qso(
                qso_id,
                {
                    "callsign": "7L1ABC",
                    "started_at": "2024-01-01T00:00:00+00:00",
                    "ended_at": "2024-01-01T00:05:00+00:00",
                    "mode": "SSB",
                    "qsl_sent": "Y",
                    "notes": "edited",
                    "is_dx": "1",
                },
            )
            row = store.get_qso(qso_id)
            assert row is not None
            self.assertEqual(row["callsign"], "7L1ABC")
            self.assertEqual(row["mode"], "SSB")
            self.assertEqual(row["qsl_sent"], "Y")
            self.assertEqual(row["notes"], "edited")
            self.assertEqual(row["is_dx"], "1")
            self.assertEqual(row["is_locked"], "1")


if __name__ == "__main__":
    unittest.main()
