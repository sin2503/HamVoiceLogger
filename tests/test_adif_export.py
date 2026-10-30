"""Unit tests for ADI export."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adif_export import export_adif, export_adx


def _qso(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "callsign": "JA1YAA",
        "started_at": "2026-10-01T17:05:00+09:00",
        "ended_at": "2026-10-01T17:10:00+09:00",
        "frequency": "7.074",
        "mode": "SSB",
        "received_report": "59",
        "sent_report": "59",
        "operator_name": "Taro",
        "qth": "Tokyo",
        "country": "Japan",
        "dxcc": "339",
        "gridsquare": "PM95",
        "station_code": "100106",
        "cq_zone": "25",
        "itu_zone": "45",
        "qsl_sent": "Y",
        "qsl_received": "N",
        "qsl_via": "JARL",
        "notes": "English remark",
        "remarks2": "日本語メモ",
    }
    record.update(overrides)
    return record


class ExportAdifTests(unittest.TestCase):
    def test_writes_completed_qso_in_utc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.adi"
            result = export_adif([_qso()], path)
            raw = path.read_bytes()
            text = raw.decode("ascii")

        self.assertEqual(result.exported, 1)
        self.assertEqual(result.skipped_incomplete, 0)
        self.assertEqual(result.omitted_non_ascii_fields, 1)
        self.assertIn(b"\r\n", raw)
        self.assertTrue(raw.endswith(b"<EOR>\r\n"))
        self.assertIn("<ADIF_VER:5>3.1.4", text)
        self.assertIn("<QSO_DATE:8>20261001", text)
        self.assertIn("<TIME_ON:6>080500", text)
        self.assertIn("<TIME_OFF:6>081000", text)
        self.assertIn("<CALL:6>JA1YAA", text)
        self.assertIn("<FREQ:5>7.074", text)
        self.assertIn("<BAND:3>40M", text)
        self.assertIn("<MODE:3>SSB", text)
        self.assertIn("<RST_RCVD:2>59", text)
        self.assertIn("<CNTY:6>100106", text)
        self.assertIn("<COMMENT:14>English remark", text)
        self.assertNotIn("日本語メモ", text)

    def test_skips_open_or_missing_callsign(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.adi"
            result = export_adif(
                [
                    _qso(ended_at=""),
                    _qso(callsign=""),
                    _qso(callsign="7L1ABC"),
                ],
                path,
            )
            text = path.read_text(encoding="ascii")

        self.assertEqual(result.exported, 1)
        self.assertEqual(result.skipped_incomplete, 2)
        self.assertIn("<CALL:6>7L1ABC", text)

    def test_maps_vhf_band_and_omits_other_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.adi"
            export_adif([_qso(frequency="433.0", mode="Other")], path)
            text = path.read_text(encoding="ascii")

        self.assertIn("<FREQ:5>433.0", text)
        self.assertIn("<BAND:4>70CM", text)
        self.assertNotIn("<MODE:", text)

    def test_dx_flag_prefixes_comment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.adi"
            export_adif([_qso(is_dx="1", notes="pileup")], path)
            text = path.read_text(encoding="ascii")
        self.assertIn("<COMMENT:9>DX pileup", text)

    def test_unicode_adi_keeps_japanese(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.adi"
            result = export_adif([_qso()], path, allow_unicode=True)
            text = path.read_text(encoding="utf-8")
        self.assertEqual(result.omitted_non_ascii_fields, 0)
        self.assertIn("日本語メモ", text)

    def test_adx_export_includes_japanese(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.adx"
            result = export_adx([_qso(operator_name="太郎")], path)
            text = path.read_text(encoding="utf-8")
        self.assertEqual(result.exported, 1)
        self.assertIn("<CALL>JA1YAA</CALL>", text)
        self.assertIn("<NAME>太郎</NAME>", text)
        self.assertIn("日本語メモ", text)


if __name__ == "__main__":
    unittest.main()
