"""WSJT-X / JTDX UDP logged-QSO parsing and ingest."""

from __future__ import annotations

import socket
import struct
import tempfile
import time
import unittest
from pathlib import Path

from log_store import LogStore
from wsjtx_udp import (
    WsjtxUdpListener,
    ingest_logged_qso,
    logged_qsos_from_datagram,
)


def _utf8(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack(">I", len(raw)) + raw


def _header(message_type: int, schema: int = 3) -> bytes:
    return struct.pack(">III", 0xADBCCBDA, schema, message_type)


class WsjtxUdpTests(unittest.TestCase):
    def test_logged_adif_packet(self) -> None:
        adif = (
            "<adif_ver:5>3.1.4<programid:6>WSJT-X<EOH>"
            "<CALL:6>JA1YAA<GRIDSQUARE:4>PM95<MODE:3>FT8"
            "<RST_SENT:3>-10<RST_RCVD:3>+02"
            "<QSO_DATE:8>20261120<TIME_ON:6>033400"
            "<QSO_DATE_OFF:8>20261120<TIME_OFF:6>033500"
            "<FREQ:9>14.074000<NAME:4>Taro<EOR>"
        )
        packet = _header(12) + _utf8("WSJT-X") + _utf8(adif)
        qsos = logged_qsos_from_datagram(packet)
        self.assertEqual(len(qsos), 1)
        qso = qsos[0]
        self.assertEqual(qso["callsign"], "JA1YAA")
        self.assertEqual(qso["gridsquare"], "PM95")
        self.assertEqual(qso["mode"], "FT8")
        self.assertEqual(qso["frequency"], "14.074")
        self.assertEqual(qso["sent_report"], "-10")
        self.assertEqual(qso["received_report"], "+02")
        self.assertEqual(qso["operator_name"], "Taro")
        self.assertEqual(qso["lookup_provider"], "WSJT-X")
        self.assertEqual(qso["started_at"], "2026-11-20T03:34:00+00:00")
        self.assertEqual(qso["ended_at"], "2026-11-20T03:35:00+00:00")

    def test_qso_logged_uses_qt_julian_day(self) -> None:
        # QDate(2000, 1, 1).toJulianDay() == 2451545. Timespec 1 is UTC.
        time_on = struct.pack(">qIB", 2451545, 0, 1)
        time_off = struct.pack(">qIB", 2451545, 45_000, 1)
        packet = b"".join(
            [
                _header(5),
                _utf8("JTDX"),
                time_off,
                _utf8("W1AW"),
                _utf8("fn31"),
                struct.pack(">Q", 14_074_000),
                _utf8("FT8"),
                _utf8("-08"),
                _utf8("-12"),
                _utf8("5W"),
                _utf8("cq"),
                _utf8("Joe"),
                time_on,
                _utf8("JA1OP"),
                _utf8("JA1OP"),
                _utf8("PM95"),
                _utf8("PM95"),
                _utf8("FN31"),
                _utf8(""),
            ]
        )
        qso = logged_qsos_from_datagram(packet)[0]
        self.assertEqual(qso["callsign"], "W1AW")
        self.assertEqual(qso["gridsquare"], "FN31")
        self.assertEqual(qso["frequency"], "14.074")
        self.assertEqual(qso["mode"], "FT8")
        self.assertEqual(qso["sent_report"], "-08")
        self.assertEqual(qso["received_report"], "-12")
        self.assertEqual(qso["operator_name"], "Joe")
        self.assertEqual(qso["notes"], "cq")
        self.assertEqual(qso["remarks2"], "FN31")
        self.assertEqual(qso["started_at"], "2000-01-01T00:00:00+00:00")
        self.assertEqual(qso["ended_at"], "2000-01-01T00:00:45+00:00")
        self.assertEqual(qso["lookup_provider"], "WSJT-X/JTDX")

    def test_qso_logged_offset_from_utc(self) -> None:
        # 09:00 at UTC+9 is 00:00 UTC. Timespec 2 carries the offset in seconds.
        nine_hours_ms = 9 * 3600 * 1000
        stamp = struct.pack(">qIBi", 2451545, nine_hours_ms, 2, 9 * 3600)
        packet = b"".join(
            [
                _header(5),
                _utf8("WSJT-X"),
                stamp,
                _utf8("JA1YAA"),
                _utf8("PM95"),
                struct.pack(">Q", 7_074_000),
                _utf8("FT4"),
                _utf8("+00"),
                _utf8("-01"),
                _utf8(""),
                _utf8(""),
                _utf8(""),
                stamp,
            ]
        )
        qso = logged_qsos_from_datagram(packet)[0]
        self.assertEqual(qso["started_at"], "2000-01-01T00:00:00+00:00")
        self.assertEqual(qso["frequency"], "7.074")
        self.assertEqual(qso["mode"], "FT4")

    def test_decode_and_garbage_are_ignored(self) -> None:
        decode = _header(2) + _utf8("WSJT-X") + b"\x00" * 40
        self.assertEqual(logged_qsos_from_datagram(decode), [])
        self.assertEqual(logged_qsos_from_datagram(b"not a packet"), [])
        self.assertEqual(logged_qsos_from_datagram(_header(12) + b"\xff" * 3), [])

    def test_duplicate_logged_adif_and_qso_logged(self) -> None:
        adif = (
            "<programid:4>JTDX<EOH>"
            "<CALL:6>JA1YAA<MODE:3>FT8<FREQ:6>14.074"
            "<QSO_DATE:8>20000101<TIME_ON:6>000000<TIME_OFF:6>000045<EOR>"
        )
        adif_packet = _header(12) + _utf8("JTDX") + _utf8(adif)
        time_on = struct.pack(">qIB", 2451545, 0, 1)
        time_off = struct.pack(">qIB", 2451545, 45_000, 1)
        qso_packet = b"".join(
            [
                _header(5),
                _utf8("JTDX"),
                time_off,
                _utf8("JA1YAA"),
                _utf8("PM95"),
                struct.pack(">Q", 14_074_000),
                _utf8("FT8"),
                _utf8("-10"),
                _utf8("-12"),
                _utf8(""),
                _utf8(""),
                _utf8(""),
                time_on,
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = LogStore(Path(tmp) / "logger.sqlite3")
            first = ingest_logged_qso(store, logged_qsos_from_datagram(adif_packet)[0])
            second = ingest_logged_qso(store, logged_qsos_from_datagram(qso_packet)[0])
            self.assertTrue(first.inserted)
            self.assertTrue(second.duplicate)
            self.assertEqual(len(store.search_qsos(limit=10)), 1)
            row = store.search_qsos(limit=1)[0]
            self.assertEqual(row["callsign"], "JA1YAA")
            self.assertEqual(row["frequency"], "14.074")
            self.assertEqual(row["lookup_provider"], "JTDX")

    def test_listener_loopback(self) -> None:
        found: list[dict[str, object]] = []
        adif = (
            "<programid:6>WSJT-X<EOH>"
            "<CALL:4>W1AW<MODE:3>FT8<FREQ:6>14.074"
            "<QSO_DATE:8>20000101<TIME_ON:6>000000<EOR>"
        )
        packet = _header(12) + _utf8("WSJT-X") + _utf8(adif)
        listener = WsjtxUdpListener(
            "127.0.0.1",
            0,
            "",
            found.append,
            lambda _text, _is_error: None,
        )
        listener.start()
        self.addCleanup(listener.stop)
        self.assertTrue(listener.ready.wait(2))
        self.assertIsNone(listener.bind_error)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(packet, ("127.0.0.1", listener.bound_port))
        deadline = time.time() + 2
        while time.time() < deadline and not found:
            time.sleep(0.02)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["callsign"], "W1AW")


if __name__ == "__main__":
    unittest.main()
