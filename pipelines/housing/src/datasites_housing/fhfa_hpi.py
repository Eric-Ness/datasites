"""Annual house price indexes for every county and state from FHFA's all-transactions HPI (1975-).

FHFA publishes them as two spreadsheets, hpi_at_county.xlsx and hpi_at_state.xlsx, each with four lines of
notes, a header row and these columns:
  county: State, County, FIPS code, Year, Annual Change (%), HPI, HPI with 1990 base, HPI with 2000 base
  state:  State, Abbreviation, FIPS, Year, Annual Change (%), HPI, HPI with 1990 base, HPI with 2000 base

The index measures cumulative nominal appreciation, from sales prices and appraisals of mortgages bought or
guaranteed by Fannie Mae and Freddie Mac. "HPI" is 100 in the first year FHFA recorded the area, so its level
can't be compared between places; the 1990- and 2000-based indexes can, and annual change can. FHFA calls
these annual indexes developmental and revises them with each release. Where the sample is too small FHFA
leaves the value empty, which stays null here.
"""

from __future__ import annotations

import io

import polars as pl

from datasites_core import geo

BASE = "https://www.fhfa.gov/hpi/download/annual"
RESOURCES = {
    "county": f"{BASE}/hpi_at_county.xlsx",
    "state": f"{BASE}/hpi_at_state.xlsx",
}
SERIES = "all-transactions"
HEADERS = {
    "county": ["State", "County", "FIPS code", "Year", "Annual Change (%)", "HPI", "HPI with 1990 base", "HPI with 2000 base"],
    "state": ["State", "Abbreviation", "FIPS", "Year", "Annual Change (%)", "HPI", "HPI with 1990 base", "HPI with 2000 base"],
}
VALUES = {
    "Annual Change (%)": "annual_change",
    "HPI": "hpi",
    "HPI with 1990 base": "hpi_1990",
    "HPI with 2000 base": "hpi_2000",
}
def read_sheet(body: bytes, level: str) -> pl.DataFrame:
    """The sheet below its header row. FHFA puts a few lines of notes above it, so the row is found, not assumed."""
    df = pl.read_excel(io.BytesIO(body), has_header=False, infer_schema_length=0)
    cells = lambda i: [str(v).strip() if v is not None else "" for v in df.row(i)]  # noqa: E731
    starts = [i for i in range(min(len(df), 20)) if cells(i)[:1] == ["State"]]
    if not starts:
        raise ValueError(f"FHFA {level} HPI: no header row starting with 'State' in the first 20 rows")
    header = cells(starts[0])
    if header != HEADERS[level]:
        raise ValueError(f"FHFA {level} HPI: header changed, expected {HEADERS[level]}, got {header}")
    df = df.slice(starts[0] + 1)
    df.columns = header
    return df


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    counties = {loc["fips"]: loc_id for loc_id, loc in geo.load().items() if loc["level"] == "us-county"}
    states = {loc["fips"]: loc_id for loc_id, loc in geo.load().items() if loc["level"] == "us-state"}
    frames = []
    for level, fips_col, known in (("county", "FIPS code", counties), ("state", "FIPS", states)):
        df = read_sheet(raw[level], level)
        df = df.with_columns(pl.col(c).str.strip_chars() for c in df.columns)
        # Missing values: an empty cell, blank text, or "." (FHFA's marker for too small a sample).
        df = df.with_columns(pl.when(pl.col(c).is_in(["", "."])).then(None).otherwise(pl.col(c)).alias(c) for c in VALUES)
        width = 5 if level == "county" else 2
        bad = df.filter(~pl.col(fips_col).str.contains(rf"^\d{{{width}}}$") | ~pl.col("Year").str.contains(r"^\d{4}$"))
        if len(bad):
            raise ValueError(f"FHFA {level} HPI: unexpected FIPS or year values {bad.select(fips_col, 'Year').head(5).rows()}")
        unknown = sorted(set(df[fips_col].unique()) - set(known))
        if unknown:
            raise ValueError(f"FHFA {level} HPI: FIPS codes not in the registry: {unknown[:10]}")
        values = []
        for source, name in VALUES.items():
            parsed = pl.col(source).cast(pl.Float64, strict=False)
            junk = df.filter(parsed.is_null() & pl.col(source).is_not_null())
            if len(junk):
                raise ValueError(f"FHFA {level} HPI: non-numeric {source} values {junk[source].unique().to_list()[:5]}")
            values.append(parsed.alias(name))
        frames.append(
            df.select(
                pl.lit(SERIES).alias("series"),
                pl.col(fips_col).replace_strict(known).alias("location"),
                pl.col("Year").alias("date"),
                *values,
            )
        )
    out = pl.concat(frames)
    dupes = out.select("location", "date").is_duplicated().sum()
    if dupes:
        raise ValueError(f"FHFA HPI: {dupes} rows repeat a place and year")
    # A year before an area's first index value carries nothing; FHFA lists none, but guard against blank rows.
    return out.filter(pl.any_horizontal(pl.col(c).is_not_null() for c in VALUES.values()))
