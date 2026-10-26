"""US House district returns, 1976-2024, from MEDSL's "U.S. House 1976-2024" (doi:10.7910/DVN/IG0UN2).

Dataverse asks for a guestbook response before it serves this file, so a person downloads it into
data/sources/medsl/ and collect() reads it from there (see that folder's README). Dataverse's metadata
needs no guestbook, so collect() checks that copy's MD5 against the cited version and stops when MEDSL
publishes a new one.

Source columns: year,state,state_po,state_fips,state_cen,state_ic,office,district,stage,runoff,special,
candidate,party,writein,mode,candidatevotes,totalvotes,unofficial,version,fusion_ticket

One row per candidate per ballot line per race, as MEDSL publishes it. What the transform changes:
- district becomes a number; 0 is an at-large seat, as in MEDSL's file.
- MEDSL marks an uncontested race with no reported count (Arkansas, Florida, Louisiana and Oklahoma)
  with a single row whose candidatevotes and totalvotes are 1 (or 0 or -1 in Florida 2016, 2020 and
  2024). Those become null.
- party and runoff use NA for "none" or "not recorded"; those become null. runoff is only recorded
  outside 2006-2018.
- race_row numbers the rows of each race in MEDSL's order. A race can list several unnamed WRITEIN
  rows, and Texas 1996 lists the November special open primary and the December runoff under one race.
- candidate_total_votes adds up a candidate's ballot lines in a race, for fusion voting (New York,
  Connecticut, New Jersey, South Carolina).
"""

from __future__ import annotations

import io

import polars as pl

from datasites_core import geo

from . import medsl

DOI = "doi:10.7910/DVN/IG0UN2"
FILENAME = "1976-2024-house.csv"
DATAVERSE_NAME = "1976-2024-house.tab"  # MEDSL uploaded the CSV under this name
SOURCE_COLUMNS = [
    "year", "state", "state_po", "state_fips", "state_cen", "state_ic", "office", "district", "stage", "runoff",
    "special", "candidate", "party", "writein", "mode", "candidatevotes", "totalvotes", "unofficial", "version",
    "fusion_ticket",
]  # fmt: skip
RACE = ["year", "state_po", "district", "stage", "special"]
FLAGS = {"true", "false"}
WHAT = "MEDSL House"


def collect(ctx) -> dict[str, bytes]:
    body = medsl.local_file(FILENAME)
    meta = ctx.get_json(medsl.dataset_api_url(), params={"persistentId": DOI})
    medsl.check_local_copy(meta, DOI, DATAVERSE_NAME, ctx.contract.source.attribution, body)
    return {"house": body}


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    df = pl.read_csv(io.BytesIO(raw["house"]), infer_schema_length=0)
    medsl.require_columns(df, SOURCE_COLUMNS, WHAT)
    df = df.with_columns(pl.col(c).str.strip_chars() for c in df.columns)
    df = df.with_columns(pl.when(pl.col(c).is_in(["", "NA"])).then(None).otherwise(pl.col(c)).alias(c) for c in df.columns)
    for column in ("runoff", "special", "writein", "unofficial", "fusion_ticket"):
        medsl.require_values(df.with_columns(pl.col(column).str.to_lowercase()), column, FLAGS, WHAT)
    for column in ("special", "writein", "unofficial", "fusion_ticket"):
        if df[column].null_count():
            raise ValueError(f"{WHAT}: {column} has missing values")
    medsl.require_values(df.with_columns(pl.col("stage").str.to_lowercase()), "stage", {"gen", "pri"}, WHAT)
    medsl.require_values(df.with_columns(pl.col("mode").str.to_lowercase()), "mode", {"total"}, WHAT)
    medsl.require_values(df, "office", {"US HOUSE"}, WHAT)
    bad = df.filter(~pl.col("year").str.contains(r"^\d{4}$") | ~pl.col("district").str.contains(r"^\d{1,2}$"))
    if len(bad):
        raise ValueError(f"{WHAT}: unexpected year or district values {bad.select('year', 'district').unique().rows()[:5]}")

    states = {loc["usps"]: loc_id for loc_id, loc in geo.load().items() if loc["level"] == "us-state"}
    unknown = sorted(set(df["state_po"].unique().to_list()) - set(states))
    if unknown:
        raise ValueError(f"{WHAT}: state codes not in the registry: {unknown}")

    votes = medsl.whole_number("candidatevotes", WHAT, df)
    total = medsl.whole_number("totalvotes", WHAT, df)
    df = df.with_columns(
        pl.col("district").cast(pl.Int64),
        pl.col("stage").str.to_uppercase(),
        pl.when(pl.col("runoff").is_not_null()).then(medsl.flag("runoff")).alias("runoff"),
        medsl.flag("special").alias("special"),
        medsl.flag("writein").alias("writein"),
        medsl.flag("unofficial").alias("unofficial"),
        medsl.flag("fusion_ticket").alias("fusion_ticket"),
        medsl.version_date(df, "version", WHAT).alias("version"),
        votes.alias("candidatevotes"),
        total.alias("totalvotes"),
    )
    # The codebook's marker for an uncontested race with no reported count.
    no_count = pl.col("totalvotes").is_in([1, 0, -1]) & pl.col("candidatevotes").is_in([1, 0, -1]) & (pl.len().over(RACE) == 1)
    negative = df.filter(~no_count & ((pl.col("candidatevotes") < 0) | (pl.col("totalvotes") < 0)))
    if len(negative):
        raise ValueError(f"{WHAT}: negative vote counts in {negative.select(RACE).unique().rows()[:5]}")
    df = df.with_columns(
        pl.when(no_count).then(None).otherwise(pl.col("candidatevotes")).alias("candidatevotes"),
        pl.when(no_count).then(None).otherwise(pl.col("totalvotes")).alias("totalvotes"),
    )
    line = [*RACE, "runoff", "candidate"]
    candidate_total = (
        pl.when((pl.col("candidate") == "WRITEIN") | (pl.col("candidatevotes").is_null().sum().over(line) > 0))
        .then(None)
        .otherwise(pl.col("candidatevotes").sum().over(line))
    )
    return df.select(
        "year",
        "state",
        "state_po",
        pl.col("state_fips").cast(pl.Int64),
        pl.col("state_cen").cast(pl.Int64),
        pl.col("state_ic").cast(pl.Int64),
        pl.col("state_po").replace_strict(states).alias("state_id"),
        "office",
        "district",
        "stage",
        "runoff",
        "special",
        (pl.int_range(pl.len()).over(RACE) + 1).alias("race_row"),
        "candidate",
        "party",
        "writein",
        "fusion_ticket",
        "mode",
        "candidatevotes",
        "totalvotes",
        candidate_total.alias("candidate_total_votes"),
        "unofficial",
        "version",
    )
