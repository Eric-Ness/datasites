"""healthdata/kindergarten-vaccination: CDC SchoolVaxView (verbatim excerpt, captured 2026-11-12)."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import polars as pl
import pytest
from health_helpers import body, text

from datasites_core import publish
from datasites_core.contracts import load_contract
from datasites_core.fetch import Fetched
from datasites_core.layout import version_key
from datasites_core.store import get_json
from datasites_core.validate import validate
from datasites_health import kindergarten_vaccination as kv


@pytest.fixture
def table():
    return kv.transform({"kindergarten": body("schoolvaxview_excerpt.csv")})


def at(table, series, location, year):
    rows = table.filter((pl.col("series") == series) & (pl.col("location") == location) & (pl.col("date") == year))
    assert len(rows) == 1, (series, location, year)
    return rows.row(0, named=True)


def lenient(contract):
    return dataclasses.replace(contract, checks=dataclasses.replace(contract.checks, min_rows=5, max_null_fraction={}))


def test_shape_and_validation(table):
    contract = load_contract("healthdata/kindergarten-vaccination")
    assert table.columns == contract.columns
    assert set(table["series"]) <= set(contract.series)
    assert validate(lenient(contract), table, None).status == "pass"


def test_national_and_state_figures(table):
    us = at(table, "mmr", "usa", "2025")
    assert (us["estimate"], us["kindergartners"], us["percent_surveyed"], us["survey_type"]) == (92.4, 3692432, 96.0, None)
    idaho = at(table, "mmr", "us-id", "2025")
    assert (idaho["estimate"], idaho["school_year"], idaho["status"], idaho["survey_type"]) == (75.2, "2025-26", "reported", "Census")
    assert at(table, "mmr", "us-id", "2024")["estimate"] == 78.5
    assert at(table, "mmr-pac", "us-id", "2025")["estimate"] == 83.4
    assert at(table, "varicella-2-doses", "us-id", "2025")["estimate"] == 74.6
    assert at(table, "dtap", "us-id", "2025")["estimate"] == 74.8


def test_exemptions_carry_their_counts(table):
    non = at(table, "exemption-nonmedical", "usa", "2025")
    assert (non["estimate"], non["exemptions"], non["kindergartners"]) == (4.0, 148608, 3725852)
    idaho = [at(table, f"exemption-{k}", "us-id", "2025")["exemptions"] for k in ("medical", "nonmedical", "any")]
    assert idaho == [92, 3704, 3796] and idaho[0] + idaho[1] == idaho[2]
    assert at(table, "mmr", "us-id", "2025")["exemptions"] is None


def test_codes_become_a_status_with_no_estimate(table):
    for series, loc, year, status in [
        ("mmr", "us-ks", "2025", "not-available"),
        ("varicella-1-dose", "us-ar", "2022", "not-required"),
        ("hepb", "us-me", "2023", "not-required"),  # CDC writes it "Nreq" here
        ("mmr", "us-ak", "2019", "not-reported"),
    ]:
        row = at(table, series, loc, year)
        assert (row["estimate"], row["status"]) == (None, status), (series, loc, year)
    assert at(table, "mmr", "us-ak", "2019")["kindergartners"] == 10381  # counted, but no figure reported


def test_cities_and_median_are_dropped_and_survey_type_cleaned(table):
    assert set(table["location"]) == {"usa", "us-id", "us-ks", "us-ny", "us-or", "us-ar", "us-me", "us-ak", "us-dc", "us-al", "us-ia"}
    assert at(table, "mmr", "us-ny", "2025")["estimate"] == 97.7  # the whole state, not New York City's 97.1
    assert at(table, "mmr", "us-al", "2010")["survey_type"] is None  # "NA"
    assert at(table, "mmr", "us-ks", "2009")["survey_type"] == "Stratified 1-stage sample (Public); Census (Private)"


def test_the_all_vaccines_mark_becomes_a_flag(table):
    # Alabama counted a child up to date only with every required vaccine until 2024-25 ("≥"); Iowa still does (">=").
    assert at(table, "mmr", "us-al", "2019")["all_vaccines"] is True
    assert at(table, "mmr", "us-al", "2025")["all_vaccines"] is False
    assert at(table, "mmr", "us-ia", "2025")["all_vaccines"] is True
    assert at(table, "mmr", "us-id", "2025")["all_vaccines"] is False  # "*" is another mark


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda t: t.replace("States,Oregon,", "States,Oregone,"), "unknown places"),
        (lambda t: t.replace("Varicella,UTD (or disease history)", "Varicella,3 Doses"), "unknown vaccine and dose"),
        (lambda t: t.replace("Idaho,2025-26,75.2,", "Idaho,2025-26,see note,"), "unknown estimate codes"),
        (lambda t: t.replace("Idaho,2024-25,", "Idaho,2024-26,"), "don't run one year"),
        (lambda t: t.replace("Number of Exemptions", "Exemptions (N)"), "columns changed"),
        (lambda t: t.replace(",148608,", ",148608.5,"), "fractional"),
    ],
)
def test_realistic_bad_inputs_stop_the_run(edit, message):
    with pytest.raises(ValueError, match=message):
        kv.transform({"kindergarten": edit(text("schoolvaxview_excerpt.csv")).encode("utf-8")})


def test_a_repeated_row_stops_the_run():
    t = text("schoolvaxview_excerpt.csv")
    line = next(x for x in t.splitlines() if x.startswith("MMR,,States,Oregon,"))
    with pytest.raises(ValueError, match="repeat"):
        kv.transform({"kindergarten": (t + line + "\n").encode("utf-8")})


def test_end_to_end_publish(store):
    contract = lenient(load_contract("healthdata/kindergarten-vaccination"))
    etag = 'W/"svv"'

    def fetcher(requested, if_etag, last_modified):
        assert requested == kv.URL
        if if_etag == etag:
            return Fetched(requested, 304, None, etag, last_modified)
        return Fetched(requested, 200, body("schoolvaxview_excerpt.csv"), etag, "Fri, 28 Aug 2026 14:06:33 GMT")

    now = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)
    first = publish.run_dataset(contract, store, fetcher=fetcher, now=now)
    assert first.outcome == "published", first.report.as_dict()
    doc, _ = get_json(store, version_key("healthdata", "kindergarten-vaccination", first.version))
    assert doc["source"]["id"] == "cdc-schoolvaxview" and doc["source"]["commercial_ok"] is True
    assert publish.run_dataset(contract, store, fetcher=fetcher, now=now.replace(day=13)).outcome == "unchanged"
