"""US Senate statewide returns, 1976-2024, from MEDSL's "U.S. Senate statewide 1976-2024" (doi:10.7910/DVN/PEJ5QU).

Source columns: year,state,state_po,state_fips,state_cen,state_ic,office,district,stage,special,candidate,
party_detailed,writein,mode,candidatevotes,totalvotes,unofficial,version,party_simplified

One row per candidate per ballot line per race, as MEDSL publishes it. What the transform changes:
- Codes are made consistent. Older rows say gen/total/statewide and newer ones GEN/TOTAL; all become
  upper case, and the 2022 Georgia "GEN RUNOFF" becomes RUNOFF like the 2021 one.
- MEDSL marks a race with no reported count (Louisiana 1978, 1984, 1990 and 1992, where the seat was
  settled without a November vote) with candidatevotes = totalvotes = 1, per its codebook. Those
  become null.
- Version stamps (20210114, 11/20/25) become dates.
- race_row numbers the rows of each race in MEDSL's order. MEDSL has no unique key of its own: a race
  can list several unnamed write-in rows, and Louisiana 2002 lists both rounds' votes for the same
  candidates under one race.
- candidate_total_votes adds up a candidate's ballot lines in a race, for fusion voting
  (New York, Connecticut).
"""

from __future__ import annotations

import io

import polars as pl

from datasites_core import geo

from . import medsl

DOI = "doi:10.7910/DVN/PEJ5QU"
FILENAME = "1976-2024-senate-state.csv"
SOURCE_COLUMNS = [
    "year", "state", "state_po", "state_fips", "state_cen", "state_ic", "office", "district", "stage", "special",
    "candidate", "party_detailed", "writein", "mode", "candidatevotes", "totalvotes", "unofficial", "version",
    "party_simplified",
]  # fmt: skip
RACE = ["year", "state_po", "special", "stage"]
STAGES = {"gen": "GEN", "runoff": "RUNOFF", "gen runoff": "RUNOFF", "pre": "PRE"}
FLAGS = {"true", "false"}
WHAT = "MEDSL Senate"


def resources(ctx) -> dict[str, str]:
    """One small metadata request finds the data file of the version the contract cites."""
    meta = ctx.get_json(medsl.dataset_api_url(), params={"persistentId": DOI})
    file_id = medsl.resolve_file(meta, DOI, FILENAME, ctx.contract.source.attribution)
    return {"senate": medsl.access_url(file_id)}


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    df = pl.read_csv(io.BytesIO(raw["senate"]), infer_schema_length=0)
    medsl.require_columns(df, SOURCE_COLUMNS, WHAT)
    df = df.with_columns(pl.col(c).str.strip_chars() for c in df.columns)
    df = df.with_columns(pl.when(pl.col(c) == "").then(None).otherwise(pl.col(c)).alias(c) for c in df.columns)
    for column in ("special", "writein", "unofficial"):
        medsl.require_values(df.with_columns(pl.col(column).str.to_lowercase()), column, FLAGS, WHAT)
    medsl.require_values(df.with_columns(pl.col("stage").str.to_lowercase()), "stage", set(STAGES), WHAT)
    medsl.require_values(df.with_columns(pl.col("mode").str.to_lowercase()), "mode", {"total"}, WHAT)
    medsl.require_values(df, "office", {"US SENATE"}, WHAT)
    medsl.require_values(df.with_columns(pl.col("district").str.to_lowercase()), "district", {"statewide"}, WHAT)
    bad_years = df.filter(~pl.col("year").str.contains(r"^\d{4}$"))
    if len(bad_years):
        raise ValueError(f"{WHAT}: unexpected year values {bad_years['year'].unique().to_list()[:5]}")

    states = {loc["usps"]: loc_id for loc_id, loc in geo.load().items() if loc["level"] == "us-state"}
    unknown = sorted(set(df["state_po"].unique().to_list()) - set(states))
    if unknown:
        raise ValueError(f"{WHAT}: state codes not in the registry: {unknown}")

    votes = medsl.whole_number("candidatevotes", WHAT, df)
    total = medsl.whole_number("totalvotes", WHAT, df)
    df = df.with_columns(
        pl.col("stage").str.to_lowercase().replace_strict(STAGES).alias("stage"),
        medsl.flag("special").alias("special"),
        medsl.flag("writein").alias("writein"),
        medsl.flag("unofficial").alias("unofficial"),
        medsl.version_date(df, "version", WHAT).alias("version"),
        votes.alias("candidatevotes"),
        total.alias("totalvotes"),
    )
    # The codebook's marker for a race with no reported count is a vote total of 1.
    no_count = pl.col("totalvotes") == 1
    df = df.with_columns(
        pl.when(no_count).then(None).otherwise(pl.col("candidatevotes")).alias("candidatevotes"),
        pl.when(no_count).then(None).otherwise(pl.col("totalvotes")).alias("totalvotes"),
    )
    line = [*RACE, "candidate"]
    candidate_total = (
        pl.when(pl.col("candidate").is_null() | (pl.col("candidatevotes").is_null().sum().over(line) > 0))
        .then(None)
        .otherwise(pl.col("candidatevotes").sum().over(line))
    )
    return df.select(
        "year",
        "state",
        "state_po",
        pl.col("state_fips").cast(pl.Int64),
        medsl.whole_number("state_cen", WHAT, df).alias("state_cen"),
        pl.col("state_ic").cast(pl.Int64),
        pl.col("state_po").replace_strict(states).alias("state_id"),
        "office",
        pl.col("district").str.to_uppercase(),
        "stage",
        "special",
        (pl.int_range(pl.len()).over(RACE) + 1).alias("race_row"),
        "candidate",
        "party_detailed",
        "party_simplified",
        "writein",
        pl.col("mode").str.to_uppercase(),
        "candidatevotes",
        "totalvotes",
        candidate_total.alias("candidate_total_votes"),
        "unofficial",
        "version",
    )
