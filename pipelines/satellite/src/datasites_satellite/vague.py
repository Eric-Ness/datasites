"""GCAT's Vague Date format (https://planet4589.org/space/gcat/web/intro/vague.html).

A vague date names an interval, not an instant: '2016 Jun 8' is the whole day, '2016 Jun' the whole
month, and a trailing '?' widens the interval by one unit of the last field on each side ('2016 Jun 8?'
is Jun 7 to Jun 10). A trailing 's' marks a scheduled (unconfirmed) date. '-' means none, '?' unknown,
'*' now.

The rule this module enforces: a calendar date or a timestamp is only ever derived when the interval
GCAT gives is that precise. Nothing is rounded, and missing fields are never filled in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone

MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
QUARTERS = {"Q1": 1, "Q2": 4, "Q3": 7, "Q4": 10}
NO_DATE = {"", "-", "?", "*"}

_YEAR = re.compile(r"^\d{1,4}$")
_DECADE = re.compile(r"^(\d{3})0s$")
_CENTURY = re.compile(r"^(\d{1,2})([CM])$")
_DAY = re.compile(r"^(\d{1,2})(?:\.(\d{1,2}))?$")
_HOUR = re.compile(r"^(\d{2})h$")
_HMS = re.compile(r"^(\d{2})(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$")


@dataclass(frozen=True)
class VagueDate:
    text: str
    start: datetime  # inclusive, UTC
    end: datetime  # exclusive, UTC
    precision: str  # millisecond, second, minute, hour, centiday, deciday, day, month, quarter, year, decade, century, millennium
    uncertain: bool  # trailing '?'
    scheduled: bool  # trailing 's'
    _clock: tuple[int, int, int | None, int | None] | None = None  # hour, minute, second, millisecond as written

    @property
    def day(self) -> date | None:
        """The UTC calendar day, only when the whole interval falls inside one day and the date is not just scheduled."""
        if self.scheduled:
            return None
        last = self.end - timedelta(microseconds=1)
        return self.start.date() if self.start.date() == last.date() else None

    @property
    def timestamp(self) -> str | None:
        """ISO 8601 UTC at exactly the precision written (minute, second or millisecond), or None.

        '1957 Oct  4 1928:34' -> '1957-10-04T19:28:34Z'; '2026 Oct 23 0711' -> '2026-10-23T07:11Z'.
        Uncertain ('?'), scheduled, hour-only, decimal-day and leap-second (:60) values give None.
        """
        if self._clock is None or self.uncertain or self.scheduled:
            return None
        hour, minute, second, millis = self._clock
        if second == 60:
            return None
        day = self.start.date().isoformat()
        if second is None:
            return f"{day}T{hour:02d}:{minute:02d}Z"
        if millis is None:
            return f"{day}T{hour:02d}:{minute:02d}:{second:02d}Z"
        return f"{day}T{hour:02d}:{minute:02d}:{second:02d}.{millis:03d}Z"


def _add_months(moment: datetime, months: int) -> datetime:
    index = moment.year * 12 + (moment.month - 1) + months
    return moment.replace(year=index // 12, month=index % 12 + 1)


def _utc(year: int, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


@lru_cache(maxsize=None)
def parse(text: str | None) -> VagueDate | None:
    """Parses one vague date. Returns None for '-', '?', '*', BC dates and anything not in the documented format."""
    if text is None:
        return None
    raw = " ".join(text.split())
    if raw in NO_DATE:
        return None
    body = raw
    uncertain = body.endswith("?")
    if uncertain:
        body = body[:-1].rstrip()
    tokens = body.split(" ")
    if tokens and tokens[0] in ("AD", "BC"):
        if tokens[0] == "BC":
            return None
        tokens = tokens[1:]
    if not tokens:
        return None

    scheduled = False
    if len(tokens) > 1 and tokens[-1].endswith("s"):
        scheduled = True
        tokens[-1] = tokens[-1][:-1]
    try:
        return _build(raw, tokens, uncertain, scheduled)
    except (ValueError, OverflowError):  # e.g. 'Feb 30': not a real date, so no interval at all
        return None


def _build(raw: str, tokens: list[str], uncertain: bool, scheduled: bool) -> VagueDate | None:
    year_token = tokens[0]
    if len(tokens) == 1:
        if m := _DECADE.match(year_token):
            start = _utc(int(m.group(1)) * 10)
            return _span(raw, start, "decade", 120, uncertain, scheduled, months=True)
        if m := _CENTURY.match(year_token):
            size = 100 if m.group(2) == "C" else 1000
            start = _utc((int(m.group(1)) - 1) * size + 1)
            return _span(raw, start, "century" if size == 100 else "millennium", size * 12, uncertain, scheduled, months=True)
        if _YEAR.match(year_token):
            return _span(raw, _utc(int(year_token)), "year", 12, uncertain, scheduled, months=True)
        return None
    if not _YEAR.match(year_token):
        return None
    year = int(year_token)

    month_token = tokens[1]
    if month_token in QUARTERS:
        if len(tokens) != 2:
            return None
        return _span(raw, _utc(year, QUARTERS[month_token]), "quarter", 3, uncertain, scheduled, months=True)
    if month_token not in MONTHS:
        return None
    month = MONTHS[month_token]
    if len(tokens) == 2:
        return _span(raw, _utc(year, month), "month", 1, uncertain, scheduled, months=True)

    m = _DAY.match(tokens[2])
    if not m:
        return None
    day_start = _utc(year, month, int(m.group(1)))
    if m.group(2) is not None:  # decimal day: one digit is a tenth of a day, two digits a hundredth
        if len(tokens) != 3:
            return None
        digits = m.group(2)
        unit = timedelta(days=10 ** -len(digits))
        start = day_start + int(digits) * unit
        return _span(raw, start, "deciday" if len(digits) == 1 else "centiday", unit, uncertain, scheduled)
    if len(tokens) == 3:
        return _span(raw, day_start, "day", timedelta(days=1), uncertain, scheduled)
    if len(tokens) != 4:
        return None

    clock = tokens[3]
    if m := _HOUR.match(clock):
        hour = int(m.group(1))
        if hour > 23:
            return None
        return _span(raw, day_start + timedelta(hours=hour), "hour", timedelta(hours=1), uncertain, scheduled)
    m = _HMS.match(clock)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    second = int(m.group(3)) if m.group(3) is not None else None
    millis = None
    if m.group(4) is not None:
        millis = int(m.group(4).ljust(3, "0"))
    if hour > 23 or minute > 59 or (second is not None and second > 60):
        return None
    # A leap second (:60) is placed in the last ordinary second's slot, so it stays inside its own UTC day.
    seconds = 59 if second == 60 else (second or 0)
    start = day_start + timedelta(hours=hour, minutes=minute, seconds=seconds, milliseconds=millis or 0)
    if second is None:
        precision, unit = "minute", timedelta(minutes=1)
    elif millis is None:
        precision, unit = "second", timedelta(seconds=1)
    else:
        precision, unit = "millisecond", timedelta(milliseconds=1)
    span = _span(raw, start, precision, unit, uncertain, scheduled)
    return VagueDate(span.text, span.start, span.end, span.precision, span.uncertain, span.scheduled, (hour, minute, second, millis))


def _span(raw, start, precision, unit, uncertain, scheduled, *, months: bool = False) -> VagueDate:
    """The interval [start, start + unit), widened by one unit on each side when the value ends in '?'."""
    if months:
        end = _add_months(start, unit)
        if uncertain:
            start, end = _add_months(start, -unit), _add_months(end, unit)
    else:
        end = start + unit
        if uncertain:
            start, end = start - unit, end + unit
    return VagueDate(raw, start, end, precision, uncertain, scheduled)


def iso_day(text: str | None) -> str | None:
    """'1998 Nov 20' -> '1998-11-20'; None unless GCAT pins the value to a single, confirmed UTC day."""
    parsed = parse(text)
    day = parsed.day if parsed else None
    return day.isoformat() if day else None
