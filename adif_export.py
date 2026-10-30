"""Export completed QSO records as interoperable ADIF 3.1.4 ADI / ADX files."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from xml.dom import minidom


@dataclass(frozen=True)
class AdifExportResult:
    exported: int
    skipped_incomplete: int
    omitted_non_ascii_fields: int


def _field(name: str, value: str) -> str:
    return f"<{name}:{len(value)}>{value}"


def _text_field(name: str, value: str, *, allow_unicode: bool) -> tuple[str, bool]:
    normalized = value.replace("\r", " ").replace("\n", " ").strip()
    if not normalized:
        return "", False
    if not allow_unicode and not normalized.isascii():
        return "", True
    return _field(name, normalized), False


def _utc_date_time(value: str) -> tuple[str, str]:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.astimezone()
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y%m%d"), moment.strftime("%H%M%S")


def _frequency_parts(value: str) -> tuple[str, str]:
    normalized = value.strip()
    if not normalized:
        return "", ""
    try:
        frequency = Decimal(normalized)
    except InvalidOperation:
        return "", ""
    if not frequency.is_finite() or frequency <= 0:
        return "", ""

    frequency_text = format(frequency, "f")
    bands = (
        (Decimal("0.472"), Decimal("0.479"), "630M"),
        (Decimal("1.8"), Decimal("2.0"), "160M"),
        (Decimal("3.5"), Decimal("4.0"), "80M"),
        (Decimal("5.06"), Decimal("5.45"), "60M"),
        (Decimal("7.0"), Decimal("7.3"), "40M"),
        (Decimal("10.1"), Decimal("10.15"), "30M"),
        (Decimal("14.0"), Decimal("14.35"), "20M"),
        (Decimal("18.068"), Decimal("18.168"), "17M"),
        (Decimal("21.0"), Decimal("21.45"), "15M"),
        (Decimal("24.89"), Decimal("24.99"), "12M"),
        (Decimal("28.0"), Decimal("29.7"), "10M"),
        (Decimal("50"), Decimal("54"), "6M"),
        (Decimal("144"), Decimal("148"), "2M"),
        (Decimal("420"), Decimal("450"), "70CM"),
    )
    band = next(
        (name for lower, upper, name in bands if lower <= frequency <= upper),
        "",
    )
    return frequency_text, band


def _frequency_fields(value: str) -> tuple[str, str]:
    frequency_text, band = _frequency_parts(value)
    if not frequency_text:
        return "", ""
    return (
        _field("FREQ", frequency_text),
        _field("BAND", band) if band else "",
    )


def _iter_exportable(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    ready: list[dict[str, Any]] = []
    skipped = 0
    for record in records:
        callsign = str(record.get("callsign", "")).strip().upper()
        started_at = record.get("started_at")
        if not record.get("ended_at") or not callsign or not started_at:
            skipped += 1
            continue
        ready.append(record)
    return ready, skipped


def _optional_values(record: dict[str, Any]) -> list[tuple[str, str]]:
    is_dx = str(record.get("is_dx", "")).strip().lower() in {"1", "true", "yes", "y"}
    comment = str(record.get("notes", "")).strip()
    if is_dx:
        comment = f"DX {comment}".strip() if comment else "DX"
    return [
        ("RST_RCVD", str(record.get("received_report", "") or "")),
        ("RST_SENT", str(record.get("sent_report", "") or "")),
        ("NAME", str(record.get("operator_name", "") or "")),
        ("QTH", str(record.get("qth", "") or "")),
        ("COUNTRY", str(record.get("country", "") or "")),
        ("DXCC", str(record.get("dxcc", "") or "")),
        ("GRIDSQUARE", str(record.get("gridsquare", "") or "")),
        ("CNTY", str(record.get("station_code", "") or "")),
        ("CQZ", str(record.get("cq_zone", "") or "")),
        ("ITUZ", str(record.get("itu_zone", "") or "")),
        ("QSL_SENT", str(record.get("qsl_sent", "") or "")),
        ("QSL_RCVD", str(record.get("qsl_received", "") or "")),
        ("QSL_VIA", str(record.get("qsl_via", "") or "")),
        ("COMMENT", comment),
        ("NOTES", str(record.get("remarks2", "") or "")),
    ]


def _write_bytes(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def export_adif(
    records: list[dict[str, Any]],
    destination: str | Path,
    *,
    allow_unicode: bool = False,
) -> AdifExportResult:
    """Write completed QSOs to an ADI file.

    ASCII mode (default) omits non-ASCII optional fields for classic ADI tools.
    Unicode mode writes UTF-8 and keeps Japanese Name/QTH/etc.
    """
    destination = Path(destination)
    created = datetime.now(timezone.utc).strftime("%Y%m%d %H%M%S")
    output = [
        "HAM Voice Logger ADIF export",
        _field("ADIF_VER", "3.1.4"),
        _field("PROGRAMID", "HAMVoiceLogger"),
        _field("CREATED_TIMESTAMP", created),
        "<EOH>",
    ]
    ready, skipped_incomplete = _iter_exportable(records)
    omitted_non_ascii_fields = 0

    for record in ready:
        callsign = str(record.get("callsign", "")).strip().upper()
        qso_date, time_on = _utc_date_time(str(record["started_at"]))
        fields = [
            _field("QSO_DATE", qso_date),
            _field("TIME_ON", time_on),
            _field("CALL", callsign),
        ]
        qso_date_off, time_off = _utc_date_time(str(record["ended_at"]))
        fields.extend((_field("QSO_DATE_OFF", qso_date_off), _field("TIME_OFF", time_off)))

        frequency, band = _frequency_fields(str(record.get("frequency", "")))
        if frequency:
            fields.append(frequency)
        if band:
            fields.append(band)

        mode = str(record.get("mode", "")).strip().upper()
        if mode and mode != "OTHER":
            fields.append(_field("MODE", mode))

        for field_name, value in _optional_values(record):
            rendered, omitted = _text_field(
                field_name, value, allow_unicode=allow_unicode
            )
            if rendered:
                fields.append(rendered)
            omitted_non_ascii_fields += int(omitted)

        output.append("".join(fields) + "<EOR>")

    encoding = "utf-8" if allow_unicode else "ascii"
    payload = ("\r\n".join(output) + "\r\n").encode(encoding)
    _write_bytes(destination, payload)
    return AdifExportResult(len(ready), skipped_incomplete, omitted_non_ascii_fields)


def export_adx(
    records: list[dict[str, Any]],
    destination: str | Path,
) -> AdifExportResult:
    """Write completed QSOs to an ADIF ADX (XML, UTF-8) file."""
    destination = Path(destination)
    ready, skipped_incomplete = _iter_exportable(records)
    created = datetime.now(timezone.utc).strftime("%Y%m%d %H%M%S")

    root = ET.Element("ADX")
    header = ET.SubElement(root, "HEADER")
    ET.SubElement(header, "ADIF_VER").text = "3.1.4"
    ET.SubElement(header, "PROGRAMID").text = "HAMVoiceLogger"
    ET.SubElement(header, "CREATED_TIMESTAMP").text = created
    records_el = ET.SubElement(root, "RECORDS")

    for record in ready:
        callsign = str(record.get("callsign", "")).strip().upper()
        qso_date, time_on = _utc_date_time(str(record["started_at"]))
        qso_date_off, time_off = _utc_date_time(str(record["ended_at"]))
        row = ET.SubElement(records_el, "RECORD")
        ET.SubElement(row, "QSO_DATE").text = qso_date
        ET.SubElement(row, "TIME_ON").text = time_on
        ET.SubElement(row, "QSO_DATE_OFF").text = qso_date_off
        ET.SubElement(row, "TIME_OFF").text = time_off
        ET.SubElement(row, "CALL").text = callsign

        frequency_text, band = _frequency_parts(str(record.get("frequency", "")))
        if frequency_text:
            ET.SubElement(row, "FREQ").text = frequency_text
        if band:
            ET.SubElement(row, "BAND").text = band

        mode = str(record.get("mode", "")).strip().upper()
        if mode and mode != "OTHER":
            ET.SubElement(row, "MODE").text = mode

        for field_name, value in _optional_values(record):
            text = value.replace("\r", " ").replace("\n", " ").strip()
            if text:
                ET.SubElement(row, field_name).text = text

    rough = ET.tostring(root, encoding="utf-8")
    pretty = minidom.parseString(rough).toprettyxml(indent="  ", encoding="utf-8")
    _write_bytes(destination, pretty)
    return AdifExportResult(len(ready), skipped_incomplete, 0)
