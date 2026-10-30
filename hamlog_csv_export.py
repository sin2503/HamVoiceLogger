"""Export completed QSOs as Turbo HAMLOG-compatible CSV.

Column order follows the classic main-window / CSV layout used by
Turbo HAMLOG/Win import (without record numbers, so rows append):

Call, Date, Time, HisRTS, MyRTS, Freq, Mode, Code, G/L, QSL,
Name, QTH, Remarks1, Remarks2

Date/time are written in JST as ``yy/mm/dd`` and ``HH:MMJ``, matching
common HAMLOG CSV samples. Encoding is CP932 (Shift_JIS) with CRLF,
which Japanese Windows HAMLOG expects.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any

_JST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class HamlogCsvExportResult:
    exported: int
    skipped_incomplete: int


def _jst_date_time(value: str) -> tuple[str, str]:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.astimezone()
    moment = moment.astimezone(_JST)
    return moment.strftime("%y/%m/%d"), moment.strftime("%H:%MJ")


def _qsl_code(sent: str, received: str) -> str:
    """Map stored QSL flags into a compact HAMLOG QSL cell."""
    sent = (sent or "").strip().upper()
    received = (received or "").strip().upper()
    if sent and received:
        return f"{sent}{received}"
    return sent or received


def export_hamlog_csv(
    records: list[dict[str, Any]], destination: str | Path
) -> HamlogCsvExportResult:
    """Write completed QSOs to a Turbo HAMLOG importable CSV file."""
    destination = Path(destination)
    exported = 0
    skipped_incomplete = 0
    rows: list[list[str]] = []

    for record in records:
        callsign = str(record.get("callsign", "")).strip().upper()
        started_at = record.get("started_at")
        if not record.get("ended_at") or not callsign or not started_at:
            skipped_incomplete += 1
            continue

        date_text, time_text = _jst_date_time(str(started_at))
        is_dx = str(record.get("is_dx", "")).strip().lower() in {"1", "true", "yes", "y"}
        remarks1 = (
            str(record.get("notes", "")).replace("\r", " ").replace("\n", " ").strip()
        )
        if is_dx:
            remarks1 = f"[DX] {remarks1}".strip() if remarks1 else "[DX]"
        rows.append(
            [
                callsign,
                date_text,
                time_text,
                str(record.get("received_report", "")).strip(),
                str(record.get("sent_report", "")).strip(),
                str(record.get("frequency", "")).strip(),
                str(record.get("mode", "")).strip().upper() or "FM",
                str(record.get("station_code", "")).strip(),
                str(record.get("gridsquare", "")).strip().upper(),
                _qsl_code(
                    str(record.get("qsl_sent", "")),
                    str(record.get("qsl_received", "")),
                ),
                str(record.get("operator_name", "")).strip(),
                str(record.get("qth", "")).strip(),
                remarks1,
                str(record.get("remarks2", "")).replace("\r", " ").replace("\n", " ").strip(),
            ]
        )
        exported += 1

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Binary write keeps CRLF intact on every platform.
    buffer = []
    for row in rows:
        line = StringIO()
        csv.writer(line, lineterminator="").writerow(row)
        buffer.append(line.getvalue().rstrip("\r\n").encode("cp932", errors="replace"))
    destination.write_bytes(b"\r\n".join(buffer) + (b"\r\n" if buffer else b""))

    return HamlogCsvExportResult(exported, skipped_incomplete)
