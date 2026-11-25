"""JST month calendar helpers for the QSO history tab."""

from __future__ import annotations

import unicodedata
from calendar import Calendar
from datetime import date

from time_format import JST, parse_iso_datetime


def month_weeks(year: int, month: int) -> list[list[int | None]]:
    """Sunday-first weeks. Padding cells are None."""
    cal = Calendar(firstweekday=6)
    weeks: list[list[int | None]] = []
    for week in cal.monthdayscalendar(year, month):
        weeks.append([day or None for day in week])
    return weeks


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def jst_date_of(started_at: str | None) -> date | None:
    """Calendar date in Japan time. Naive timestamps follow the local zone."""
    moment = parse_iso_datetime(started_at)
    if moment is None:
        return None
    return moment.astimezone(JST).date()


def counts_for_month(
    started_values: list[str], year: int, month: int
) -> dict[int, int]:
    """Day-of-month → QSO count, using the JST calendar date."""
    counts: dict[int, int] = {}
    for value in started_values:
        day = jst_date_of(value)
        if day is None or day.year != year or day.month != month:
            continue
        counts[day.day] = counts.get(day.day, 0) + 1
    return counts


def received_report_sign(value: str | None) -> str:
    """Return ``+`` or ``-`` when the other station's report has that sign."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = text.replace("−", "-").replace("–", "-")
    if not text:
        return ""
    if text[0] == "+":
        return "+"
    if text[0] == "-":
        return "-"
    return ""


def report_sign_counts(
    samples: list[tuple[str, str]],
    *,
    year: int | None = None,
    month: int | None = None,
) -> tuple[int, int]:
    """Count plus and minus received reports. Optional year/month use JST dates."""
    plus = 0
    minus = 0
    for started_at, report in samples:
        if year is not None and month is not None:
            day = jst_date_of(started_at)
            if day is None or day.year != year or day.month != month:
                continue
        sign = received_report_sign(report)
        if sign == "+":
            plus += 1
        elif sign == "-":
            minus += 1
    return plus, minus


def format_report_balance(plus: int, minus: int) -> str:
    """Counts, and each share of the signed reports."""
    total = plus + minus
    if total == 0:
        return "＋0　ー0"
    plus_pct = round(plus * 100 / total)
    minus_pct = 100 - plus_pct
    return f"＋{plus}（{plus_pct}%）　ー{minus}（{minus_pct}%）"


def format_report_summary(
    samples: list[tuple[str, str]], year: int, month: int
) -> str:
    """Overall balance, then the balance for one JST month."""
    overall = report_sign_counts(samples)
    month_counts = report_sign_counts(samples, year=year, month=month)
    return (
        "相手レポート\n"
        f"全体 {format_report_balance(*overall)}\n"
        f"{month}月 {format_report_balance(*month_counts)}"
    )
