"""satellitedata/launches: GCAT's launch list, one row per launch, keyed by launch tag.

Covers every launch GCAT records, orbital or not (https://planet4589.org/space/gcat/web/launch/lcols.html).
The first letter of LaunchCode is the launch category and the rest its outcome: 'OS' a successful
orbital launch, 'OF' a failure, 'OS80' an orbital launch scored 80% successful, 'ME' a missile pad
explosion. Columns GCAT documents as not yet filled in (Perigee, Inc, Azimuth, OrbMass, FailCode) are
left out.
"""

from __future__ import annotations

import polars as pl

from .gcat import URLS, lookup, missing, normalize, number, org_name, org_names, read_tsv, updated_at  # noqa: F401  (normalize is part of the module interface)
from .vague import parse

RESOURCES = {"launch": URLS["launch"], "orgs": URLS["orgs"]}

LAUNCH_CLASSES = {  # LaunchCode letter 1
    "O": "orbital",
    "D": "deep-space",
    "M": "missile",
    "L": "lofted-missile",
    "N": "near-orbital",
    "T": "test-rocket",
    "A": "atmospheric",
    "S": "suborbital",
    "H": "high-altitude-sounding",
    "R": "reentry-test",
    "X": "off-earth",
    "Y": "crewed-suborbital",
    "!": "not-a-launch",  # placeholder entries GCAT lists as 'Not real' (unknown launches of debris, cargo)
}
OUTCOMES = {"S": "success", "F": "failure", "U": "unknown", "E": "pad-explosion", "A": "pad-abort"}

COLUMNS = [
    "launch_tag", "launch_class", "launch_date", "launch_time", "launch_date_text", "launch_date_precision",
    "launch_jd", "lv_type", "lv_variant", "flight_id", "flight", "mission", "flight_code",
    "platform", "launch_site", "launch_pad", "ascent_site", "ascent_pad",
    "apogee_km", "apogee_estimated", "orbital_payload_t", "agency", "agency_name",
    "launch_code", "outcome", "success_percent", "category",
]


def _date(text: str) -> str | None:
    parsed = parse(text)
    return parsed.day.isoformat() if parsed and parsed.day else None


def _time(text: str) -> str | None:
    parsed = parse(text)
    return parsed.timestamp if parsed else None


def _precision(text: str) -> str | None:
    parsed = parse(text)
    if parsed is None:
        return None
    return parsed.precision + (" (uncertain)" if parsed.uncertain else "") + (" (scheduled)" if parsed.scheduled else "")


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    launches = read_tsv(raw["launch"], "launch")
    names = org_names(raw["orgs"])

    code = pl.col("LaunchCode")
    letters = set(launches["LaunchCode"].str.slice(0, 1).unique().to_list())
    unknown = letters - set(LAUNCH_CLASSES)
    if unknown:
        raise ValueError(f"undocumented GCAT launch categories: {sorted(unknown)}")
    shapes = launches.filter(~code.str.contains(r"^(!|[A-Z][SFUEA](\d{2})?)$"))
    if len(shapes):
        raise ValueError(f"undocumented LaunchCode {shapes['LaunchCode'][0]!r} for {shapes['Launch_Tag'][0]}")

    fraction = code.str.extract(r"^[A-Z][SF](\d{2})$", 1).cast(pl.Int64)
    out = launches.select(
        pl.col("Launch_Tag").alias("launch_tag"),
        code.str.slice(0, 1).replace_strict(LAUNCH_CLASSES, return_dtype=pl.Utf8).alias("launch_class"),
        lookup(missing("Launch_Date"), _date).alias("launch_date"),
        lookup(missing("Launch_Date"), _time).alias("launch_time"),
        missing("Launch_Date").alias("launch_date_text"),
        lookup(missing("Launch_Date"), _precision).alias("launch_date_precision"),
        number("Launch_JD").alias("launch_jd"),
        missing("LV_Type").alias("lv_type"),
        missing("Variant").alias("lv_variant"),
        missing("Flight_ID").alias("flight_id"),
        missing("Flight").alias("flight"),
        missing("Mission").alias("mission"),
        missing("FlightCode").alias("flight_code"),
        missing("Platform").alias("platform"),
        missing("Launch_Site").alias("launch_site"),
        missing("Launch_Pad").alias("launch_pad"),
        missing("Ascent_Site").alias("ascent_site"),
        missing("Ascent_Pad").alias("ascent_pad"),
        number("Apogee").alias("apogee_km"),
        pl.when(number("Apogee").is_not_null()).then((pl.col("Apoflag") == "?").fill_null(False)).otherwise(None).alias("apogee_estimated"),
        number("OrbPay").alias("orbital_payload_t"),
        missing("Agency").alias("agency"),
        lookup(missing("Agency"), lambda c: org_name(c, names)).alias("agency_name"),
        code.alias("launch_code"),
        code.str.slice(1, 1).replace_strict(OUTCOMES, default=None, return_dtype=pl.Utf8).alias("outcome"),
        # 'OS80' / 'OF40': GCAT's percentage success for reliability statistics; plain S and F carry no number
        fraction.alias("success_percent"),
        missing("Category").alias("category"),
    )
    cutoff = updated_at(raw["launch"])
    if cutoff:  # a date after GCAT regenerated the file cannot be a launch that happened: keep only the text
        future = pl.col("launch_date") > cutoff
        out = out.with_columns(
            pl.when(future).then(None).otherwise(pl.col("launch_time")).alias("launch_time"),
            pl.when(future).then(None).otherwise(pl.col("launch_date")).alias("launch_date"),
        )
    return out.select(COLUMNS)
