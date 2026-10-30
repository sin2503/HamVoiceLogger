"""Import completed QSOs from Turbo HAMLOG classic CSV."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any

from log_store import LogStore

_JST = timezone(timedelta(hours=9))
_DATE_RE = re.compile(r"^(\d{2}|\d{4})[/-](\d{1,2})[/-](\d{1,2})$")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?:J)?$", re.IGNORECASE)


@dataclass(frozen=True)
class HamlogCsvImportResult:
    imported: int
    skipped_incomplete: int
    skipped_duplicate: int


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp932", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp932", errors="replace")


def _parse_qsl(cell: str) -> tuple[str, str]:
    text = (cell or "").strip().upper()
    if not text:
        return "", ""
    if len(text) == 1:
        return text, ""
    return text[0], text[1]


def _parse_jst(date_text: str, time_text: str) -> str | None:
    date_match = _DATE_RE.match((date_text or "").strip())
    time_match = _TIME_RE.match((time_text or "").strip())
    if date_match is None or time_match is None:
        return None
    year_raw, month, day = date_match.groups()
    hour, minute = time_match.groups()
    year = int(year_raw)
    if year < 100:
        year += 2000 if year < 80 else 1900
    try:
        moment = datetime(
            year,
            int(month),
            int(day),
            int(hour),
            int(minute),
            0,
            tzinfo=_JST,
        )
    except ValueError:
        return None
    return moment.isoformat(timespec="seconds")


def _normalize_row(cells: list[str]) -> list[str] | None:
    """Return 14 data columns, stripping an optional leading record number."""
    if len(cells) < 14:
        return None
    if len(cells) >= 15 and cells[0].strip().isdigit() and not cells[1].strip().isdigit():
        return cells[1:15]
    return cells[:14]


def hamlog_row_to_qso(cells: list[str]) -> dict[str, Any] | None:
    row = _normalize_row(cells)
    if row is None:
        return None
    callsign = row[0].strip().upper()
    started = _parse_jst(row[1], row[2])
    if not callsign or not started:
        return None
    qsl_sent, qsl_received = _parse_qsl(row[9])
    remarks1 = row[12].strip()
    is_dx = False
    if remarks1.upper().startswith("[DX]"):
        is_dx = True
        remarks1 = remarks1[4:].lstrip()
    elif remarks1.upper().startswith("DX "):
        is_dx = True
        remarks1 = remarks1[3:].lstrip()
    return {
        "callsign": callsign,
        "started_at": started,
        "ended_at": started,
        "received_report": row[3].strip(),
        "sent_report": row[4].strip(),
        "frequency": row[5].strip(),
        "mode": row[6].strip().upper() or "FM",
        "station_code": row[7].strip(),
        "gridsquare": row[8].strip().upper(),
        "qsl_sent": qsl_sent,
        "qsl_received": qsl_received,
        "operator_name": row[10].strip(),
        "qth": row[11].strip(),
        "notes": remarks1,
        "remarks2": row[13].strip(),
        "is_dx": is_dx,
        "lookup_provider": "HAMLOG CSV import",
    }


def import_hamlog_csv(
    source: str | Path,
    store: LogStore,
    *,
    skip_duplicates: bool = True,
) -> HamlogCsvImportResult:
    """Parse a Turbo HAMLOG-style CSV and insert completed QSOs."""
    path = Path(source)
    text = _read_text(path)
    reader = csv.reader(StringIO(text))
    imported = 0
    skipped_incomplete = 0
    skipped_duplicate = 0
    for cells in reader:
        if not cells or all(not cell.strip() for cell in cells):
            continue
        # Skip a possible header row.
        first = cells[0].strip().upper()
        if first in {"CALL", "コール", "コールサイン"}:
            continue
        qso = hamlog_row_to_qso(cells)
        if qso is None:
            skipped_incomplete += 1
            continue
        if skip_duplicates:
            existing = store.find_qso_id(
                qso["callsign"],
                qso["started_at"],
                qso.get("frequency", ""),
                qso.get("mode", ""),
            )
            if existing is not None:
                skipped_duplicate += 1
                continue
        store.add_completed_qso(qso)
        imported += 1
    return HamlogCsvImportResult(imported, skipped_incomplete, skipped_duplicate)
