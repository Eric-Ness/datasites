"""World Bank World Development Indicators: a core set of annual macro indicators for every economy.

Source: the keyless Indicators API v2 (https://datahelpdesk.worldbank.org/knowledgebase/articles/
889392-about-the-indicators-api). Each indicator for every economy and year fits in one page
(per_page 32767 is the API's maximum; the transform refuses a response that says it has more pages).
The economy list (/v2/country) tells real economies apart from World Bank aggregates.

Locations:
  - economies with an ISO 3166-1 alpha-3 code -> that code, lowercase ("usa");
  - the WLD aggregate -> "world";
  - Kosovo (XKX) and the Channel Islands (CHI), which the World Bank reports as economies but which
    have no ISO 3166-1 code -> "wb-xkx" and "wb-chi", declared explicitly in the contract;
  - every other aggregate (regions, income groups, lending groups, IDA/IBRD, ...) is left out.
"""

from __future__ import annotations

import json

import polars as pl

from datasites_core import geo

BASE = "https://api.worldbank.org/v2/"
PER_PAGE = 32767
INDICATORS = {
    "NY.GDP.MKTP.CD": "gdp_usd",
    "NY.GDP.MKTP.KD.ZG": "gdp_growth_pct",
    "NY.GDP.PCAP.CD": "gdp_per_capita_usd",
    "FP.CPI.TOTL.ZG": "inflation_cpi_pct",
    "SL.UEM.TOTL.ZS": "unemployment_pct",
    "SP.POP.TOTL": "population",
}
RESOURCES = {
    "countries": BASE + f"country?format=json&per_page={PER_PAGE}",
    **{code: BASE + f"country/all/indicator/{code}?format=json&per_page={PER_PAGE}" for code in INDICATORS},
}
NON_ISO = {"XKX": "wb-xkx", "CHI": "wb-chi"}
WORLD = "WLD"


def _pages(body: bytes, what: str) -> list[dict]:
    doc = json.loads(body)
    if not isinstance(doc, list) or len(doc) < 2 or not isinstance(doc[0], dict):
        message = doc[0].get("message") if isinstance(doc, list) and doc and isinstance(doc[0], dict) else doc
        raise ValueError(f"World Bank API returned no data for {what}: {str(message)[:300]}")
    header = doc[0]
    if int(header.get("pages", 1)) != 1:
        raise ValueError(f"World Bank {what}: {header.get('total')} rows span {header.get('pages')} pages; raise PER_PAGE")
    return doc[1] or []


def economy_locations(countries: bytes) -> dict[str, str]:
    """World Bank code -> location id for every economy we publish."""
    iso = {loc_id for loc_id, loc in geo.load().items() if loc["level"] == "country"}
    out: dict[str, str] = {}
    for c in _pages(countries, "the economy list"):
        code = c["id"]
        if code == WORLD:
            out[code] = "world"
        elif c["region"]["value"].strip() == "Aggregates":
            continue
        elif code in NON_ISO:
            out[code] = NON_ISO[code]
        elif code.lower() in iso:
            out[code] = code.lower()
        else:  # a new non-ISO economy: publish it under wb-<code> so validation flags it for review
            out[code] = f"wb-{code.lower()}"
    return out


def read_indicator(body: bytes, code: str) -> pl.DataFrame:
    rows = [
        (r["countryiso3code"], r["date"], r["value"])
        for r in _pages(body, code)
        if r["indicator"]["id"] == code and r["countryiso3code"]
    ]
    df = pl.DataFrame(rows, schema={"code": pl.String, "date": pl.String, "value": pl.Float64}, orient="row")
    if df.select("code", "date").is_duplicated().any():
        raise ValueError(f"World Bank {code}: duplicate economy-year rows")
    return df.rename({"value": INDICATORS[code]})


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    locations = economy_locations(raw["countries"])
    names = {c["id"]: c["name"] for c in _pages(raw["countries"], "the economy list")}
    wide: pl.DataFrame | None = None
    for code in INDICATORS:
        df = read_indicator(raw[code], code)
        wide = df if wide is None else wide.join(df, on=["code", "date"], how="full", coalesce=True)
    assert wide is not None
    columns = list(INDICATORS.values())
    wide = wide.filter(pl.col("code").is_in(list(locations)) & pl.any_horizontal(pl.col(columns).is_not_null()))
    return wide.select(
        pl.lit("annual").alias("series"),
        pl.col("code").replace_strict(locations).alias("location"),
        "date",
        *[pl.col(c) for c in columns if c != "population"],
        pl.col("population").round(0).cast(pl.Int64),
        pl.col("code").replace_strict(names, default=None).alias("location_name"),
    )
