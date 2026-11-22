"""Receive logged QSOs from WSJT-X and JTDX over their UDP status protocol.

Decodes are ignored. A QSO is stored only after the operator accepts Log QSO
in WSJT-X or JTDX, which sends QSO Logged (type 5) and, on current versions,
Logged ADIF (type 12). Both describe the same contact, so ingest dedupes them.
"""

from __future__ import annotations

import re
import socket
import struct
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from adif_import import adif_text_to_qsos
from log_store import LogStore

MAGIC = 0xADBCCBDA
TYPE_QSO_LOGGED = 5
TYPE_LOGGED_ADIF = 12
# QDate(1970, 1, 1).toJulianDay() in Qt.
_UNIX_JULIAN_DAY = 2440588
_PROGRAM_RE = re.compile(r"<PROGRAMID:(\d+)>", re.IGNORECASE)


@dataclass(frozen=True)
class IngestResult:
    inserted: bool
    duplicate: bool
    callsign: str
    provider: str
    qso_id: int | None = None


def program_id_from_adif(text: str) -> str:
    match = _PROGRAM_RE.search(text)
    if match is None:
        return ""
    start = match.end()
    length = int(match.group(1))
    return text[start : start + length].strip()


def hz_to_mhz(hz: int) -> str:
    """Format a dial frequency in hertz as megahertz, keeping trailing zeros."""
    whole, frac = divmod(int(hz), 1_000_000)
    return f"{whole}.{frac:06d}"


def logged_qsos_from_datagram(payload: bytes) -> list[dict[str, Any]]:
    """Return completed QSO dicts from one WSJT-X/JTDX datagram, or none."""
    try:
        return _logged_qsos_from_datagram(payload)
    except (ValueError, struct.error):
        return []


def ingest_logged_qso(store: LogStore, qso: dict[str, Any]) -> IngestResult:
    """Insert one logged QSO. The same call, start, mode, and frequency is a duplicate."""
    record = dict(qso)
    record["frequency"] = str(record.get("frequency") or "").strip()
    callsign = str(record.get("callsign") or "")
    started_at = str(record.get("started_at") or "")
    mode = str(record.get("mode") or "")
    provider = str(record.get("lookup_provider") or "") or "WSJT-X/JTDX"
    record["lookup_provider"] = provider
    if store.find_qso_id(callsign, started_at, record["frequency"], mode) is not None:
        return IngestResult(False, True, callsign.strip().upper(), provider)
    qso_id = store.add_completed_qso(record)
    return IngestResult(True, False, callsign.strip().upper(), provider, qso_id)


_CALLBOOK_FILL_FIELDS = (
    "operator_name",
    "qth",
    "country",
    "dxcc",
    "cq_zone",
    "itu_zone",
    "dxcc_details",
    "continent",
)


def merge_hamqth_record(
    existing: dict[str, Any], hamqth: dict[str, Any]
) -> dict[str, Any]:
    """Fill empty station fields from HamQTH and keep a grid already on the row."""
    merged = dict(existing)
    for key in _CALLBOOK_FILL_FIELDS:
        if str(merged.get(key) or "").strip():
            continue
        value = str(hamqth.get(key) or "").strip()
        if value:
            merged[key] = value
    if not str(merged.get("gridsquare") or "").strip():
        grid = str(hamqth.get("gridsquare") or "").strip()
        if grid:
            merged["gridsquare"] = grid
    looked_up_at = str(hamqth.get("lookup_at") or "").strip()
    if looked_up_at:
        merged["lookup_at"] = looked_up_at
    provider = str(merged.get("lookup_provider") or "").strip()
    if "HamQTH" not in provider:
        merged["lookup_provider"] = f"{provider} + HamQTH" if provider else "HamQTH"
    return merged


def apply_hamqth_to_qso(
    store: LogStore, qso_id: int, hamqth: dict[str, Any]
) -> bool:
    """Write a HamQTH lookup onto one stored QSO. The WSJT-X grid stays."""
    existing = store.get_qso(qso_id)
    if existing is None:
        return False
    store.update_qso(qso_id, merge_hamqth_record(existing, hamqth))
    return True


class WsjtxUdpListener:
    """Background UDP socket. Callbacks run on the listener thread."""

    def __init__(
        self,
        host: str,
        port: int,
        multicast: str,
        on_qso: Callable[[dict[str, Any]], None],
        on_status: Callable[[str, bool], None],
    ) -> None:
        self.host = host or "127.0.0.1"
        self.port = int(port)
        self.multicast = multicast.strip()
        self._on_qso = on_qso
        self._on_status = on_status
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self.ready = threading.Event()
        self.bind_error: OSError | None = None
        self.bound_port = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.ready.clear()
        self.bind_error = None
        self._thread = threading.Thread(
            target=self._serve, name="wsjtx-udp", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        self._thread = None

    def _serve(self) -> None:
        try:
            sock = self._open()
        except OSError as exc:
            self.bind_error = exc
            self.ready.set()
            where = (
                f"{self.multicast}:{self.port}"
                if self.multicast
                else f"{self.host}:{self.port}"
            )
            self._on_status(f"UDP待受に失敗しました: {where} ({exc})", True)
            return
        self._sock = sock
        self.bound_port = int(sock.getsockname()[1])
        self.ready.set()
        if self.multicast:
            label = f"{self.multicast}:{self.bound_port}"
        else:
            label = f"{self.host}:{self.bound_port}"
        self._on_status(f"UDP待受 {label}", False)
        try:
            while not self._stop.is_set():
                try:
                    data, _addr = sock.recvfrom(65535)
                except TimeoutError:
                    continue
                except OSError:
                    break
                for qso in logged_qsos_from_datagram(data):
                    try:
                        self._on_qso(qso)
                    except Exception:
                        continue
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _open(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if self.multicast:
                packed = socket.inet_aton(self.multicast)
                if not 224 <= packed[0] <= 239:
                    raise OSError("multicast address required")
                sock.bind(("", self.port))
                membership = packed + socket.inet_aton("0.0.0.0")
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            else:
                sock.bind((self.host, self.port))
            sock.settimeout(0.5)
        except OSError:
            sock.close()
            raise
        return sock


def _logged_qsos_from_datagram(payload: bytes) -> list[dict[str, Any]]:
    if len(payload) < 12:
        return []
    magic, schema, message_type = struct.unpack_from(">III", payload, 0)
    if magic != MAGIC:
        return []
    _ident, offset = _read_utf8(payload, 12)
    if message_type == TYPE_LOGGED_ADIF:
        adif, _offset = _read_utf8(payload, offset)
        return _qsos_from_logged_adif(adif)
    if message_type == TYPE_QSO_LOGGED and schema >= 2:
        qso = _qso_from_qso_logged(payload, offset)
        return [qso] if qso is not None else []
    return []


def _qsos_from_logged_adif(adif: str) -> list[dict[str, Any]]:
    provider = program_id_from_adif(adif) or "WSJT-X/JTDX"
    qsos: list[dict[str, Any]] = []
    for qso in adif_text_to_qsos(adif):
        record = dict(qso)
        record["lookup_provider"] = provider
        qsos.append(record)
    return qsos


def _qso_from_qso_logged(data: bytes, offset: int) -> dict[str, Any] | None:
    time_off, offset = _read_qdatetime(data, offset)
    dx_call, offset = _read_utf8(data, offset)
    dx_grid, offset = _read_utf8(data, offset)
    if offset + 8 > len(data):
        raise ValueError("short frequency")
    (hz,) = struct.unpack_from(">Q", data, offset)
    offset += 8
    mode, offset = _read_utf8(data, offset)
    report_sent, offset = _read_utf8(data, offset)
    report_received, offset = _read_utf8(data, offset)
    _tx_power, offset = _read_utf8(data, offset)
    comments, offset = _read_utf8(data, offset)
    name, offset = _read_utf8(data, offset)
    time_on, offset = _read_qdatetime(data, offset)
    exchange_received = ""
    try:
        _operator_call, offset = _read_utf8(data, offset)
        _my_call, offset = _read_utf8(data, offset)
        _my_grid, offset = _read_utf8(data, offset)
        _exchange_sent, offset = _read_utf8(data, offset)
        exchange_received, offset = _read_utf8(data, offset)
    except ValueError:
        exchange_received = ""
    callsign = dx_call.strip().upper()
    if not callsign:
        return None
    return {
        "callsign": callsign,
        "started_at": _utc_stamp(time_on),
        "ended_at": _utc_stamp(time_off),
        "frequency": hz_to_mhz(hz),
        "mode": mode.strip() or "DATA",
        "received_report": report_received.strip(),
        "sent_report": report_sent.strip(),
        "operator_name": name.strip(),
        "qth": "",
        "country": "",
        "dxcc": "",
        "gridsquare": dx_grid.strip().upper(),
        "station_code": "",
        "cq_zone": "",
        "itu_zone": "",
        "qsl_sent": "",
        "qsl_received": "",
        "qsl_via": "",
        "notes": comments.strip(),
        "remarks2": exchange_received.strip(),
        "is_dx": False,
        "lookup_provider": "WSJT-X/JTDX",
    }


def _utc_stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _read_utf8(data: bytes, offset: int) -> tuple[str, int]:
    if offset + 4 > len(data):
        raise ValueError("short string")
    (length,) = struct.unpack_from(">I", data, offset)
    offset += 4
    if length == 0xFFFFFFFF:
        return "", offset
    if length > len(data) - offset:
        raise ValueError("short string bytes")
    raw = data[offset : offset + length]
    return raw.decode("utf-8", errors="replace"), offset + length


def _read_qdatetime(data: bytes, offset: int) -> tuple[datetime, int]:
    """Read a Qt 5.2/5.4 QDateTime. Schema 2 and 3 use this layout."""
    if offset + 13 > len(data):
        raise ValueError("short qdatetime")
    julian, msecs, spec = struct.unpack_from(">qIB", data, offset)
    offset += 13
    zone_offset = 0
    if spec == 2:
        if offset + 4 > len(data):
            raise ValueError("short utc offset")
        (zone_offset,) = struct.unpack_from(">i", data, offset)
        offset += 4
    elif spec == 3:
        raise ValueError("time zone qdatetime is not used")
    elif spec not in (0, 1):
        raise ValueError("bad timespec")
    if msecs == 0xFFFFFFFF:
        msecs = 0
    clock = datetime(1970, 1, 1) + timedelta(
        days=int(julian) - _UNIX_JULIAN_DAY, milliseconds=int(msecs)
    )
    if spec == 1:
        aware = clock.replace(tzinfo=timezone.utc)
    elif spec == 2:
        aware = clock.replace(tzinfo=timezone(timedelta(seconds=zone_offset)))
    else:
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        aware = clock.replace(tzinfo=local_tz)
    return aware, offset
