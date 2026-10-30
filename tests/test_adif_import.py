"""Unit tests for ADIF import and round-trip."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adif_export import export_adif
from adif_import import import_adif
from log_store import LogStore


class AdifImportTests(unittest.TestCase):
    def test_roundtrip_export_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LogStore(Path(tmp) / "logger.sqlite3")
            qso_id = store.start_qso(frequency="7.074", mode="SSB")
            store.end_qso(
                qso_id,
                callsign="JA1YAA",
                frequency="7.074",
                mode="SSB",
                notes="English remark",
                started_at="2026-10-01T17:05:00+09:00",
                received_report="59",
                sent_report="59",
                callbook_info={
                    "operator_name": "Taro",
                    "qth": "Tokyo",
                    "country": "Japan",
                    "dxcc": "339",
                    "gridsquare": "PM95",
                    "station_code": "100106",
                },
                qsl_sent="Y",
                qsl_received="N",
            )
            adi = Path(tmp) / "out.adi"
            export_adif(store.search_qsos(limit=10), adi)

            store2 = LogStore(Path(tmp) / "logger2.sqlite3")
            result = import_adif(adi, store2)
            self.assertEqual(result.imported, 1)
            rows = store2.search_qsos(limit=10)
            self.assertEqual(rows[0]["callsign"], "JA1YAA")
            self.assertEqual(rows[0]["frequency"], "7.074")
            self.assertEqual(rows[0]["mode"], "SSB")
            self.assertTrue(rows[0]["started_at"].startswith("2026-10-01T08:05:00"))

            again = import_adif(adi, store2)
            self.assertEqual(again.imported, 0)
            self.assertEqual(again.skipped_duplicate, 1)

    def test_dx_comment_sets_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dx.adi"
            path.write_text(
                "\r\n".join(
                    [
                        "<ADIF_VER:5>3.1.4<EOH>",
                        "<QSO_DATE:8>20260315<TIME_ON:6>033400<CALL:4>W1AW"
                        "<FREQ:6>14.200<MODE:3>SSB<COMMENT:9>DX pileup<EOR>",
                        "",
                    ]
                ),
                encoding="ascii",
            )
            store = LogStore(Path(tmp) / "logger.sqlite3")
            result = import_adif(path, store)
            self.assertEqual(result.imported, 1)
            row = store.search_qsos(limit=1)[0]
            self.assertEqual(row["is_dx"], "1")
            self.assertEqual(row["notes"], "pileup")


if __name__ == "__main__":
    unittest.main()
