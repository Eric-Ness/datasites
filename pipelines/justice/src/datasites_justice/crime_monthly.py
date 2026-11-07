"""justicedata/crime-monthly: offenses and clearances reported to the FBI each month, by state (2018-).

One CDE request per state and offense: /summarized/state/{XX}/{V|P}?from=01-2018&to=MM-YYYY. Each answer holds,
by month ("MM-YYYY"):
  offenses.actuals."<State> Offenses" / "<State> Clearances"   counts reported by the state's agencies
  offenses.rates."<State> Offenses" / "<State> Clearances"     per 100,000 people those agencies cover
  offenses.rates."United States Offenses" / "... Clearances"   the same rates for the whole country
  tooltips."Percent of Population Coverage"."<State>" / "United States"
  populations.population / participated_population ."<State>" / "United States"
These are reported figures, not estimates: they cover only the agencies that sent data that month, so the
coverage columns travel with every value. The national rows come from the same answers (they repeat in each),
and the CDE gives no national counts there, so national rows have rates and coverage only.
"""

from __future__ import annotations

import json

import polars as pl

from .cde import get, series_by_name, states

FIRST = "01-2018"
CODES = {"V": "violent-crime", "P": "property-crime"}


def collect(ctx) -> dict[str, bytes]:
    to = f"{ctx.now.month:02d}-{ctx.now.year}"
    return {
        f"{usps}-{code}": get(
            ctx, f"summarized/state/{usps}/{code}", {"from": FIRST, "to": to}
        )
        for usps in sorted(states())
        for code in CODES
    }


def _month(key: str, what: str) -> str:
    mm, _, yyyy = key.partition("-")
    if not (mm.isdigit() and yyyy.isdigit() and len(mm) == 2 and len(yyyy) == 4):
        raise ValueError(f"{what}: unexpected month {key!r}")
    return f"{yyyy}-{mm}"


def _place(doc: dict, place: str, with_counts: bool, what: str) -> list[dict]:
    rates = doc["offenses"]["rates"]
    off_rate = series_by_name(rates, f"{place} Offenses", what)
    clr_rate = series_by_name(rates, f"{place} Clearances", what)
    coverage = series_by_name(
        doc["tooltips"]["Percent of Population Coverage"], place, what
    )
    population = series_by_name(doc["populations"]["population"], place, what)
    covered = series_by_name(doc["populations"]["participated_population"], place, what)
    actuals = doc["offenses"].get("actuals", {})
    offenses = series_by_name(actuals, f"{place} Offenses", what) if with_counts else {}
    clearances = (
        series_by_name(actuals, f"{place} Clearances", what) if with_counts else {}
    )
    rows = []
    for key in sorted(set(off_rate) | set(offenses)):
        rows.append(
            {
                "date": _month(key, what),
                "offenses": offenses.get(key),
                "clearances": clearances.get(key),
                "offense_rate": off_rate.get(key),
                "clearance_rate": clr_rate.get(key),
                "coverage_pct": coverage.get(key),
                "population": population.get(key),
                "covered_population": covered.get(key),
            }
        )
    return rows


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    ids = states()
    rows = []
    national: dict[tuple[str, str], dict] = {}
    for name in sorted(raw):
        usps, _, code = name.partition("-")
        if usps not in ids or code not in CODES:
            raise ValueError(f"CDE summarized: unexpected raw file {name}")
        what = f"CDE summarized/state/{usps}/{code}"
        doc = json.loads(raw[name])
        coverage = doc["tooltips"]["Percent of Population Coverage"]
        places = [p for p in coverage if p != "United States"]
        if len(places) != 1:
            raise ValueError(
                f"{what}: expected one state in the answer, found {places}"
            )
        for row in _place(doc, places[0], True, what):
            rows.append({"series": CODES[code], "location": ids[usps], **row})
        for row in _place(doc, "United States", False, what):
            key = (CODES[code], row["date"])
            if key in national and national[key] != row:
                raise ValueError(
                    f"{what}: the national figures for {row['date']} differ between states' answers"
                )
            national[key] = row
    rows += [
        {"series": series, "location": "usa", **row}
        for (series, _), row in sorted(national.items())
    ]
    df = pl.DataFrame(rows, infer_schema_length=None)
    return df.with_columns(
        pl.col("offenses", "clearances", "population", "covered_population").cast(
            pl.Int64
        ),
        pl.col("offense_rate", "clearance_rate", "coverage_pct").cast(pl.Float64),
    ).select(
        "series",
        "location",
        "date",
        "offenses",
        "clearances",
        "offense_rate",
        "clearance_rate",
        "coverage_pct",
        "population",
        "covered_population",
    )
