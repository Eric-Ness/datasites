"""healthdata/kindergarten-vaccination: CDC SchoolVaxView kindergarten vaccination coverage and exemptions.

One data.cdc.gov table, ijqb-a7ye: Vaccine/Exemption, Dose, Geography Type, Geography, School Year, Estimate (%),
Population Size, Percent Surveyed, Footnotes, Number of Exemptions, Survey Type. One row per vaccine (or exemption
type), place and school year, for the states, DC, New York City, Houston, the US and the US median, since 2009-10.

What the transform changes:
- Vaccine and dose become one series id (VACCINES). An unknown pair stops the run.
- Places: state names map to the geo registry, "United States" to usa. Dropped: New York City and Houston (parts
  of New York and Texas, which CDC reports whole), "NY-Rest of state" (one 2023-24 MMR (PAC) row) and "U.S.
  Median". Any other unknown place stops the run.
- Estimate codes become a status: NA, NReq (the state doesn't require the vaccine) and NR (not reported); the
  estimate is null for all three. Any other text stops the run.
- Footnotes are dropped except one mark: "≥" (or ">=" from 2020-21) says the state counts a child up to date only with
  all doses of every vaccine required for school entry, so the figure is that all-vaccines rate (CDC MMWR 66(40),
  2017). It becomes all_vaccines. The other marks (*, †, §) are keyed only on CDC's pages, some already U+FFFD.
- Survey type: spaces collapsed; "NA", "NaN" and blanks become null.
"""

from __future__ import annotations

import io

import polars as pl

from datasites_core import geo

URL = "https://data.cdc.gov/api/views/ijqb-a7ye/rows.csv?accessType=DOWNLOAD"
RESOURCES = {"kindergarten": URL}

COLUMNS = [
    "Vaccine/Exemption", "Dose", "Geography Type", "Geography", "School Year", "Estimate (%)", "Population Size",
    "Percent Surveyed", "Footnotes", "Number of Exemptions", "Survey Type",
]  # fmt: skip
VACCINES = {
    ("MMR", ""): "mmr",
    ("MMR (PAC)", "MMR (PAC)"): "mmr-pac",
    ("DTP, DTaP, or DT", ""): "dtap",
    ("Polio", ""): "polio",
    ("Hepatitis B", ""): "hepb",
    ("Varicella", "1 Dose (or disease history)"): "varicella-1-dose",
    ("Varicella", "2 Doses (or disease history)"): "varicella-2-doses",
    ("Varicella", "UTD (or disease history)"): "varicella-utd",
    ("Exemption", "Any Exemption"): "exemption-any",
    ("Exemption", "Medical Exemption"): "exemption-medical",
    ("Exemption", "Non-Medical Exemption"): "exemption-nonmedical",
}
DROPPED = {"NY-City of New York", "NY-Rest of state", "TX-City of Houston", "U.S. Median"}
STATUS = {"NA": "not-available", "NREQ": "not-required", "NR": "not-reported"}
WHAT = "SchoolVaxView ijqb-a7ye"


def number(df: pl.DataFrame, column: str) -> pl.Expr:
    """A numeric column (commas allowed, blank is null); other text stops the run."""
    parsed = pl.col(column).str.replace_all(",", "").cast(pl.Float64, strict=False)
    bad = df.filter(parsed.is_null() & pl.col(column).is_not_null() & (pl.col(column) != ""))
    if len(bad):
        raise ValueError(f"{WHAT}: non-numeric {column} values {bad[column].unique().to_list()[:5]}")
    return parsed


def whole(df: pl.DataFrame, column: str) -> pl.Expr:
    values = number(df, column)
    frac = df.filter(values.is_not_null() & (values != values.round(0)))
    if len(frac):
        raise ValueError(f"{WHAT}: fractional {column} values {frac[column].head(3).to_list()}")
    return values.cast(pl.Int64)


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    df = pl.read_csv(io.BytesIO(raw["kindergarten"]), infer_schema_length=0)
    if df.columns != COLUMNS:
        missing = [c for c in COLUMNS if c not in df.columns]
        extra = [c for c in df.columns if c not in COLUMNS]
        raise ValueError(f"{WHAT}: columns changed (missing {missing}, unexpected {extra})")
    df = df.with_columns(pl.col(c).str.strip_chars().fill_null("") for c in df.columns)

    pairs = sorted(set(zip(df["Vaccine/Exemption"], df["Dose"], strict=True)) - set(VACCINES))
    if pairs:
        raise ValueError(f"{WHAT}: unknown vaccine and dose {pairs}")
    years = df.filter(~pl.col("School Year").str.contains(r"^\d{4}-\d{2}$"))
    if len(years):
        raise ValueError(f"{WHAT}: unexpected school years {years['School Year'].unique().to_list()[:5]}")
    bad_years = df.filter(
        (pl.col("School Year").str.slice(0, 4).cast(pl.Int32) + 1) % 100 != pl.col("School Year").str.slice(5, 2).cast(pl.Int32)
    )
    if len(bad_years):
        raise ValueError(f"{WHAT}: school years that don't run one year {bad_years['School Year'].unique().to_list()[:5]}")

    states = {loc["name"]: loc_id for loc_id, loc in geo.load().items() if loc["level"] == "us-state"}
    places = {**states, "United States": "usa"}
    unknown = sorted(set(df["Geography"]) - set(places) - DROPPED)
    if unknown:
        raise ValueError(f"{WHAT}: unknown places {unknown}")
    df = df.filter(~pl.col("Geography").is_in(DROPPED))

    estimate = pl.col("Estimate (%)")
    code = estimate.str.to_uppercase()
    parsed = estimate.cast(pl.Float64, strict=False)
    odd = df.filter(parsed.is_null() & ~code.is_in(list(STATUS)))
    if len(odd):
        raise ValueError(f"{WHAT}: unknown estimate codes {odd['Estimate (%)'].unique().to_list()[:5]}")

    survey = pl.col("Survey Type").str.replace_all(r"\s+", " ").str.strip_chars()
    out = df.with_columns(
        (pl.col("Vaccine/Exemption") + "|" + pl.col("Dose")).replace_strict({f"{v}|{d}": s for (v, d), s in VACCINES.items()}).alias("series"),
        pl.col("Geography").replace_strict(places).alias("location"),
        pl.col("School Year").str.slice(0, 4).alias("date"),
        pl.col("School Year").alias("school_year"),
        parsed.alias("estimate"),
        pl.when(parsed.is_not_null()).then(pl.lit("reported")).otherwise(code.replace_strict(STATUS, default=None)).alias("status"),
        whole(df, "Population Size").alias("kindergartners"),
        number(df, "Percent Surveyed").alias("percent_surveyed"),
        whole(df, "Number of Exemptions").alias("exemptions"),
        pl.when(survey.is_in(["", "NA", "NaN"])).then(None).otherwise(survey).alias("survey_type"),
        (pl.col("Footnotes").str.contains("≥", literal=True) | pl.col("Footnotes").str.contains(">=", literal=True)).alias("all_vaccines"),
    ).select("series", "location", "date", "school_year", "estimate", "status", "kindergartners", "percent_surveyed", "exemptions", "survey_type", "all_vaccines")

    dupes = out.select("series", "location", "date").is_duplicated().sum()
    if dupes:
        raise ValueError(f"{WHAT}: {dupes} rows repeat a series, place and school year")
    return out
