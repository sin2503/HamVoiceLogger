"""JST month calendar helpers for the QSO history tab."""

from __future__ import annotations

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
