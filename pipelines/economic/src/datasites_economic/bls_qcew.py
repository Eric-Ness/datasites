"""BLS Quarterly Census of Employment and Wages (QCEW): quarterly covered employment, wages and
establishments for every county, state and the nation, all industries, all ownerships combined.

Source: the QCEW open-data CSV industry slices (https://www.bls.gov/cew/additional-resources/open-data/
csv-data-slices.htm). One slice, https://data.bls.gov/cew/data/api/<year>/<qtr>/industry/10.csv,
holds every area for NAICS 10 "Total, all industries" in one quarter, so the whole national panel
costs one conditional GET per quarter. The open-data slices start in 2014.

Rows kept: own_code 0 (total covered) at aggregation levels 10 (national), 50 (state) and 70
(county), size_code 0. Dropped: the "Unknown Or Undefined" pseudo-counties (<state>999), whose jobs
BLS could not place in a county, so county sums fall slightly short of state totals.

Geography follows the quarter: Connecticut is reported for its eight legacy counties through
2023 Q4 and its nine planning regions from 2024 Q1. Disclosure code N marks values withheld for
confidentiality; BLS writes zeros for employment and wages there, which become nulls (the
establishment count is still published).
"""

from __future__ import annotations

import io
import time
from datetime import date
from typing import Any

import httpx
import polars as pl

from datasites_core.fetch import user_agent

from ._common import state_ids_by_fips

URL = "https://data.bls.gov/cew/data/api/{year}/{quarter}/industry/10.csv"
FIRST_YEAR = 2014  # earliest year the open-data slices cover
LEVELS = {"10": "national", "50": "state", "70": "county"}
WITHHELD = {  # zero in the file when disclosure_code is N
    "month1_emplvl": "month1_employment",
    "month2_emplvl": "month2_employment",
    "month3_emplvl": "month3_employment",
    "total_qtrly_wages": "total_wages",
    "avg_wkly_wage": "avg_weekly_wage",
}


def _quarters_through(year: int, quarter: int) -> list[tuple[int, int]]:
    return [(y, q) for y in range(FIRST_YEAR, year + 1) for q in range(1, 5) if (y, q) <= (year, quarter)]


def _previous(year: int, quarter: int) -> tuple[int, int]:
    return (year, quarter - 1) if quarter > 1 else (year - 1, 4)


def _published(url: str, attempts: int = 3) -> bool:
    """HEAD request: 200 if BLS has published the slice, 404 if not yet. Anything else is an error,
    after a few tries: guessing "not yet" on a server error would drop the newest quarter."""
    for attempt in range(attempts):
        try:
            res = httpx.head(url, headers={"User-Agent": user_agent()}, timeout=60, follow_redirects=True)
        except httpx.TransportError:
            if attempt == attempts - 1:
                raise
        else:
            if res.status_code == 404:
                return False
            if res.status_code < 500 or attempt == attempts - 1:
                res.raise_for_status()
                return True
        time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def latest_quarter(today: date, published=_published) -> tuple[int, int]:
    """The newest published quarter. BLS releases a quarter about five to six months after it ends,
    so start from the quarter that ended at least four months ago and step back until one exists."""
    year, quarter = today.year, (today.month - 1) // 3 + 1  # the current quarter
    for _ in range(2):  # skip the current and the previous quarter: neither can be out yet
        year, quarter = _previous(year, quarter)
    for _ in range(8):
        if published(URL.format(year=year, quarter=quarter)):
            return year, quarter
        year, quarter = _previous(year, quarter)
    raise RuntimeError(f"no QCEW industry slice found in the two years before {today}")


def resources(ctx: Any) -> dict[str, str]:
    year, quarter = latest_quarter(ctx.now.date())
    return {f"{y}-q{q}": URL.format(year=y, quarter=q) for y, q in _quarters_through(year, quarter)}


def read_slice(body: bytes) -> pl.DataFrame:
    df = pl.read_csv(io.BytesIO(body), infer_schema=False)
    needed = {"area_fips", "own_code", "industry_code", "agglvl_code", "size_code", "year", "qtr", "disclosure_code"}
    missing = (needed | {"qtrly_estabs", *WITHHELD}) - set(df.columns)
    if missing:
        raise ValueError(f"QCEW slice is missing columns {sorted(missing)}")
    df = df.filter(
        (pl.col("own_code") == "0")
        & (pl.col("industry_code") == "10")
        & (pl.col("size_code") == "0")
        & pl.col("agglvl_code").is_in(list(LEVELS))
        & ~pl.col("area_fips").str.ends_with("999")
    )
    suppressed = pl.col("disclosure_code").fill_null("") == "N"
    fips = pl.col("area_fips")
    level = pl.col("agglvl_code")
    location = (
        pl.when(level == "10")
        .then(pl.lit("usa"))
        .when(level == "50")
        .then(fips.str.slice(0, 2).replace_strict(state_ids_by_fips(), default=None))
        .otherwise(pl.lit("us-county-") + fips)
    )
    return df.select(
        location.alias("location"),
        pl.format("{}-{}", "year", ((pl.col("qtr").cast(pl.Int64) - 1) * 3 + 1).cast(pl.String).str.zfill(2)).alias("date"),
        pl.col("qtrly_estabs").cast(pl.Int64).alias("establishments"),  # counts are never withheld
        *[pl.when(suppressed).then(None).otherwise(pl.col(src).cast(pl.Int64)).alias(dst) for src, dst in WITHHELD.items()],
        suppressed.alias("suppressed"),
    )


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    frames = []
    for name in sorted(raw):
        df = read_slice(raw[name])
        year, quarter = name.split("-q")
        expected = f"{year}-{(int(quarter) - 1) * 3 + 1:02d}"
        if df.is_empty() or (df["date"] != expected).any():
            raise ValueError(f"QCEW slice {name} does not hold {year} Q{quarter} totals")
        frames.append(df)
    out = pl.concat(frames)
    if out["location"].null_count():
        raise ValueError("QCEW state rows with a FIPS code missing from the registry")
    return out.select(pl.lit("total-covered").alias("series"), pl.all())
