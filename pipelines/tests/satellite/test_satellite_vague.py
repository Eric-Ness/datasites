"""GCAT's Vague Date format: intervals exactly as the format's documentation tabulates them, and dates or
times derived only when the interval is that precise."""

from datetime import datetime, timezone

import pytest

from datasites_satellite.vague import iso_day, parse


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


# Rows of the table at https://planet4589.org/space/gcat/web/intro/vague.html
@pytest.mark.parametrize(
    "text, start, end, precision",
    [
        ("2016 Jun  8 2355:57.345", utc(2016, 6, 8, 23, 55, 57, 345000), utc(2016, 6, 8, 23, 55, 57, 346000), "millisecond"),
        ("2016 Jun  8 2355:57", utc(2016, 6, 8, 23, 55, 57), utc(2016, 6, 8, 23, 55, 58), "second"),
        ("2016 Jun  8 2355:57?", utc(2016, 6, 8, 23, 55, 56), utc(2016, 6, 8, 23, 55, 59), "second"),
        ("2016 Jun  8 2355", utc(2016, 6, 8, 23, 55), utc(2016, 6, 8, 23, 56), "minute"),
        ("2016 Jun  8 2355?", utc(2016, 6, 8, 23, 54), utc(2016, 6, 8, 23, 57), "minute"),
        ("2016 Jun  8.98", utc(2016, 6, 8, 23, 31, 12), utc(2016, 6, 8, 23, 45, 36), "centiday"),
        ("2016 Jun  8.98?", utc(2016, 6, 8, 23, 16, 48), utc(2016, 6, 9), "centiday"),
        ("2016 Jun  8 23h", utc(2016, 6, 8, 23), utc(2016, 6, 9), "hour"),
        ("2016 Jun  8.9", utc(2016, 6, 8, 21, 36), utc(2016, 6, 9), "deciday"),
        ("2016 Jun  8", utc(2016, 6, 8), utc(2016, 6, 9), "day"),
        ("2016 Jun  8s", utc(2016, 6, 8), utc(2016, 6, 9), "day"),
        ("2016 Jun  8?", utc(2016, 6, 7), utc(2016, 6, 10), "day"),
        ("2016 Jun", utc(2016, 6, 1), utc(2016, 7, 1), "month"),
        ("2016 Jun?", utc(2016, 5, 1), utc(2016, 8, 1), "month"),
        ("2016 Q2", utc(2016, 4, 1), utc(2016, 7, 1), "quarter"),
        ("2016 Q2?", utc(2016, 1, 1), utc(2016, 10, 1), "quarter"),
        ("2016", utc(2016, 1, 1), utc(2017, 1, 1), "year"),
        ("2016?", utc(2015, 1, 1), utc(2018, 1, 1), "year"),
        ("2010s", utc(2010, 1, 1), utc(2020, 1, 1), "decade"),
        ("2010s?", utc(2000, 1, 1), utc(2030, 1, 1), "decade"),
        ("21C", utc(2001, 1, 1), utc(2101, 1, 1), "century"),
        ("21C?", utc(1901, 1, 1), utc(2201, 1, 1), "century"),
        ("3M", utc(2001, 1, 1), utc(3001, 1, 1), "millennium"),
    ],
)
def test_documented_intervals(text, start, end, precision):
    parsed = parse(text)
    assert (parsed.start, parsed.end, parsed.precision) == (start, end, precision)


@pytest.mark.parametrize("text", ["-", "?", "*", "", None, "BC 44 Mar 15"])
def test_markers_and_bc_have_no_date(text):
    assert parse(text) is None


@pytest.mark.parametrize(
    "text, day",
    [
        ("1957 Oct  4", "1957-10-04"),  # GCAT pads one-digit days with a second space
        ("1957 Oct  4 1933", "1957-10-04"),
        ("1957 Dec  1 1000?", "1957-12-01"),  # the '?' widens the minute, not the day
        ("1960 Apr 26 1730?", "1960-04-26"),
        ("1958 Jan  4?", None),  # Jan 3 to Jan 6: no single day
        ("1965 May 23 0000?", None),  # 2359 on May 22 to 0001 on May 23: straddles midnight
        ("2016 Jun  8s", None),  # scheduled, not confirmed
        ("1966 Jan", None),
        ("1971?", None),
        ("1970s?", None),
        ("2016 DEc 14", None),  # a typo in GCAT's launch list: not the documented format
        ("2015 Feb 30", None),  # no such day
    ],
)
def test_a_date_only_when_the_interval_is_one_confirmed_day(text, day):
    assert iso_day(text) == day


@pytest.mark.parametrize(
    "text, timestamp",
    [
        ("1957 Oct  4 1928:34", "1957-10-04T19:28:34Z"),
        ("1942 Jun 13 1052", "1942-06-13T10:52Z"),  # minute precision stays minute precision
        ("2016 Jun  8 2355:57.345", "2016-06-08T23:55:57.345Z"),
        ("2016 Jun  8 2355:57.3", "2016-06-08T23:55:57.300Z"),
        ("1962 Jul  9 0903:40?", None),
        ("2016 Jun  8 23h", None),
        ("2016 Jun  8.98", None),
        ("2016 Jun  8", None),
        ("2016 Dec 31 2359:60", None),  # leap second: real, but not representable as an ISO timestamp
    ],
)
def test_timestamps_never_gain_precision(text, timestamp):
    assert parse(text).timestamp == timestamp


def test_leap_second_stays_on_its_own_day():
    parsed = parse("2016 Dec 31 2359:60")
    assert parsed.start == utc(2016, 12, 31, 23, 59, 59) and parsed.end == utc(2017, 1, 1)
    assert parsed.day.isoformat() == "2016-12-31"
