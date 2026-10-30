"""Import completed QSO records from ADIF ADI files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from log_store import LogStore

_TAG_RE = re.compile(
    r"<([A-Za-z0-9_]+)(?::(\d+)(?::[A-Za-z])?)?\s*(?:>|/>)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AdifImportResult:
    imported: int
    skipped_incomplete: int
    skipped_duplicate: int


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp932", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_records(text: str) -> list[dict[str, str]]:
    upper = text.upper()
    body = text
    eoh = upper.find("<EOH>")
    if eoh >= 0:
        body = text[eoh + 5 :]

    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    index = 0
    while True:
        match = _TAG_RE.search(body, index)
        if match is None:
            break
        name = match.group(1).upper()
        length_text = match.group(2)
        value_start = match.end()
        if name in {"EOR", "EOH"}:
            if name == "EOR" and current:
                records.append(current)
                current = {}
            index = value_start
            continue
        if length_text is not None:
            length = int(length_text)
            value = body[value_start : value_start + length]
            index = value_start + length
        else:
            next_tag = _TAG_RE.search(body, value_start)
            end = next_tag.start() if next_tag is not None else len(body)
            value = body[value_start:end]
            index = end
        current[name] = value.strip()
    if current:
        records.append(current)
    return records


def _combine_utc(date_text: str, time_text: str) -> str | None:
    date_text = (date_text or "").strip()
    time_text = (time_text or "000000").strip()
    if not date_text or len(date_text) < 8:
        return None
    if len(time_text) == 4:
        time_text = time_text + "00"
    if len(time_text) < 6:
        time_text = time_text.ljust(6, "0")
    try:
        moment = datetime.strptime(
            date_text[:8] + time_text[:6], "%Y%m%d%H%M%S"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return moment.isoformat(timespec="seconds")


def adif_record_to_qso(fields: dict[str, str]) -> dict[str, Any] | None:
    callsign = (fields.get("CALL") or "").strip().upper()
    started = _combine_utc(fields.get("QSO_DATE", ""), fields.get("TIME_ON", ""))
    if not callsign or not started:
        return None
    ended = _combine_utc(
        fields.get("QSO_DATE_OFF") or fields.get("QSO_DATE", ""),
        fields.get("TIME_OFF") or fields.get("TIME_ON", ""),
    ) or started

    comment = (fields.get("COMMENT") or "").strip()
    is_dx = False
    if comment.upper().startswith("DX"):
        is_dx = True
        comment = comment[2:].lstrip(" :-")

    return {
        "callsign": callsign,
        "started_at": started,
        "ended_at": ended,
        "frequency": (fields.get("FREQ") or "").strip(),
        "mode": (fields.get("MODE") or "FM").strip() or "FM",
        "received_report": (fields.get("RST_RCVD") or "").strip(),
        "sent_report": (fields.get("RST_SENT") or "").strip(),
        "operator_name": (fields.get("NAME") or "").strip(),
        "qth": (fields.get("QTH") or "").strip(),
        "country": (fields.get("COUNTRY") or "").strip(),
        "dxcc": (fields.get("DXCC") or "").strip(),
        "gridsquare": (fields.get("GRIDSQUARE") or "").strip(),
        "station_code": (fields.get("CNTY") or "").strip(),
        "cq_zone": (fields.get("CQZ") or "").strip(),
        "itu_zone": (fields.get("ITUZ") or "").strip(),
        "qsl_sent": (fields.get("QSL_SENT") or "").strip().upper(),
        "qsl_received": (fields.get("QSL_RCVD") or "").strip().upper(),
        "qsl_via": (fields.get("QSL_VIA") or "").strip(),
        "notes": comment,
        "remarks2": (fields.get("NOTES") or "").strip(),
        "is_dx": is_dx,
        "lookup_provider": "ADIF import",
    }


def import_adif(
    source: str | Path,
    store: LogStore,
    *,
    skip_duplicates: bool = True,
) -> AdifImportResult:
    """Parse an ADI file and insert completed QSOs into ``store``."""
    path = Path(source)
    records = _parse_records(_read_text(path))
    imported = 0
    skipped_incomplete = 0
    skipped_duplicate = 0
    for fields in records:
        qso = adif_record_to_qso(fields)
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
    return AdifImportResult(imported, skipped_incomplete, skipped_duplicate)
