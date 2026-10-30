"""Unit tests for HAMLOG CSV import and round-trip."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hamlog_csv_export import export_hamlog_csv
from hamlog_csv_import import import_hamlog_csv
from log_store import LogStore


class HamlogCsvImportTests(unittest.TestCase):
    def test_roundtrip_export_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LogStore(Path(tmp) / "logger.sqlite3")
            qso_id = store.start_qso(frequency="145.000", mode="FM")
            store.end_qso(
                qso_id,
                callsign="JA1YAA",
                frequency="145.000",
                mode="FM",
                notes="test note",
                started_at="2026-03-15T12:34:00+09:00",
                received_report="59",
                sent_report="59",
                callbook_info={
                    "operator_name": "太郎",
                    "qth": "東京",
                    "station_code": "100105",
                    "gridsquare": "PM95",
                },
                qsl_sent="Y",
                remarks2="r2",
            )
            csv_path = Path(tmp) / "out.csv"
            export_hamlog_csv(store.search_qsos(limit=10), csv_path)

            store2 = LogStore(Path(tmp) / "logger2.sqlite3")
            result = import_hamlog_csv(csv_path, store2)
            self.assertEqual(result.imported, 1)
            row = store2.search_qsos(limit=1)[0]
            self.assertEqual(row["callsign"], "JA1YAA")
            self.assertEqual(row["frequency"], "145.000")
            self.assertEqual(row["operator_name"], "太郎")
            self.assertEqual(row["qth"], "東京")
            self.assertTrue(row["started_at"].startswith("2026-03-15T12:34:00"))

            again = import_hamlog_csv(csv_path, store2)
            self.assertEqual(again.imported, 0)
            self.assertEqual(again.skipped_duplicate, 1)

    def test_record_number_column_and_dx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "numbered.csv"
            # record#, Call, Date, Time, His, My, Freq, Mode, Code, GL, QSL, Name, QTH, R1, R2
            path.write_bytes(
                "1,W1AW,26/03/15,12:34J,59,59,14.200,SSB,,FN31,YN,Bob,CT,[DX] pileup,r2\r\n".encode(
                    "cp932"
                )
            )
            store = LogStore(Path(tmp) / "logger.sqlite3")
            result = import_hamlog_csv(path, store)
            self.assertEqual(result.imported, 1)
            row = store.search_qsos(limit=1)[0]
            self.assertEqual(row["callsign"], "W1AW")
            self.assertEqual(row["is_dx"], "1")
            self.assertEqual(row["notes"], "pileup")
            self.assertEqual(row["qsl_sent"], "Y")
            self.assertEqual(row["qsl_received"], "N")


if __name__ == "__main__":
    unittest.main()
