"""JST / UTC display helpers for QSO timestamps."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
TIME_DISPLAY_MODES = ("jst", "utc", "both")
DEFAULT_TIME_DISPLAY = "both"


def normalize_time_display(value: str | None) -> str:
    mode = str(value or DEFAULT_TIME_DISPLAY).strip().lower()
    return mode if mode in TIME_DISPLAY_MODES else DEFAULT_TIME_DISPLAY


def parse_iso_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return moment


def format_timestamp(value: str | None, mode: str = DEFAULT_TIME_DISPLAY) -> str:
    """Format an ISO timestamp for UI lists according to display mode."""
    moment = parse_iso_datetime(value)
    if moment is None:
        return ""
    mode = normalize_time_display(mode)
    jst = moment.astimezone(JST)
    utc = moment.astimezone(timezone.utc)
    if mode == "jst":
        return jst.strftime("%Y-%m-%d %H:%M JST")
    if mode == "utc":
        return utc.strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"{jst.strftime('%m-%d %H:%M')} JST / "
        f"{utc.strftime('%H:%M')} UTC"
    )


def format_dual_hint(local_dt: datetime) -> str:
    """Short JST + UTC hint for the QSO start picker."""
    if local_dt.tzinfo is None:
        local_dt = local_dt.astimezone()
    jst = local_dt.astimezone(JST)
    utc = local_dt.astimezone(timezone.utc)
    return (
        f"表示: {jst.strftime('%Y-%m-%d %H:%M:%S')} JST"
        f"  /  {utc.strftime('%Y-%m-%d %H:%M:%S')} UTC"
        "（ADIFはUTC・HAMLOG CSVはJST）"
    )


def is_japan_entity(dxcc: str = "", country: str = "") -> bool:
    dxcc_text = str(dxcc or "").strip()
    country_text = str(country or "").strip().casefold()
    if dxcc_text == "339":
        return True
    return country_text in {"japan", "日本", "日本国"}
