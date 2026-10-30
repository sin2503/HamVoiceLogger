"""Unit tests for Turbo HAMLOG CSV export."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hamlog_csv_export import export_hamlog_csv


class HamlogCsvExportTests(unittest.TestCase):
    def test_writes_classic_fourteen_columns(self) -> None:
        records = [
            {
                "callsign": "JA1YAA",
                "started_at": "2026-03-15T12:34:00+09:00",
                "ended_at": "2026-03-15T12:40:00+09:00",
                "received_report": "59",
                "sent_report": "59",
                "frequency": "145.000",
                "mode": "FM",
                "station_code": "100105",
                "gridsquare": "PM95",
                "qsl_sent": "Y",
                "qsl_received": "",
                "operator_name": "太郎",
                "qth": "東京",
                "notes": "test note",
                "remarks2": "r2",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            result = export_hamlog_csv(records, path)
            self.assertEqual(result.exported, 1)
            self.assertEqual(result.skipped_incomplete, 0)
            raw = path.read_bytes()
            self.assertIn(b"\r\n", raw)
            text = raw.decode("cp932")
            row = text.strip().split("\r\n")[0]
            cells = next(csv_reader_row(row))
            self.assertEqual(len(cells), 14)
            self.assertEqual(cells[0], "JA1YAA")
            self.assertEqual(cells[1], "26/03/15")
            self.assertEqual(cells[2], "12:34J")
            self.assertEqual(cells[3], "59")
            self.assertEqual(cells[5], "145.000")
            self.assertEqual(cells[7], "100105")
            self.assertEqual(cells[10], "太郎")
            self.assertEqual(cells[11], "東京")

    def test_dx_flag_prefixes_remarks1(self) -> None:
        records = [
            {
                "callsign": "W1AW",
                "started_at": "2026-03-15T12:34:00+09:00",
                "ended_at": "2026-03-15T12:40:00+09:00",
                "notes": "dx test",
                "is_dx": "1",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            export_hamlog_csv(records, path)
            text = path.read_bytes().decode("cp932")
            row = text.strip().split("\r\n")[0]
            cells = next(csv_reader_row(row))
            self.assertEqual(cells[12], "[DX] dx test")

    def test_skips_open_qso(self) -> None:
        records = [
            {
                "callsign": "JA1YAA",
                "started_at": "2026-03-15T12:34:00+09:00",
                "ended_at": None,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            result = export_hamlog_csv(records, path)
            self.assertEqual(result.exported, 0)
            self.assertEqual(result.skipped_incomplete, 1)
            self.assertEqual(path.read_bytes(), b"")


def csv_reader_row(line: str):
    import csv
    from io import StringIO

    yield from csv.reader(StringIO(line))


if __name__ == "__main__":
    unittest.main()
