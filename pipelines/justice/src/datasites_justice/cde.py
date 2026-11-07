"""The FBI Crime Data Explorer (CDE) API on api.data.gov, shared by the justicedata pipelines.

Requests carry the api.data.gov key in the X-Api-Key header, never in the URL, so the key can't end up in an
archived name, an error message or a published status. api.data.gov allows 1,000 requests an hour per key; the
pipelines make one request per state and offense, a few hundred a run.

Offense codes (the API's own): V violent crime, P property crime, HOM homicide, RPE rape, ROB robbery,
ASS aggravated assault, BUR burglary, LAR larceny-theft, MVT motor vehicle theft, ARS arson.
"""

from __future__ import annotations

import json
from typing import Any

from datasites_core import geo

API = "https://api.usa.gov/crime/fbi/cde"
KEY_ENV = "DATA_GOV_API_KEY"
OFFENSES = {
    "V": "violent-crime",
    "P": "property-crime",
    "HOM": "homicide",
    "RPE": "rape",
    "ROB": "robbery",
    "ASS": "aggravated-assault",
    "BUR": "burglary",
    "LAR": "larceny-theft",
    "MVT": "motor-vehicle-theft",
    "ARS": "arson",
}


def states() -> dict[str, str]:
    """USPS code -> registry id for the 50 states and DC (the CDE has no territories)."""
    return {
        loc["usps"]: loc_id
        for loc_id, loc in geo.load().items()
        if loc["level"] == "us-state" and loc.get("kind") in ("state", "district")
    }


def get(ctx, path: str, params: dict[str, str]) -> bytes:
    key = ctx.env(KEY_ENV)
    body = ctx.get(f"{API}/{path}", params=params, headers={"X-Api-Key": key})
    try:
        doc = json.loads(body)
    except ValueError as err:
        raise ValueError(f"CDE {path}: not JSON ({body[:80]!r})") from err
    if isinstance(doc, dict) and "error" in doc:
        raise RuntimeError(
            f"CDE {path}: {doc['error'].get('code')}: {doc['error'].get('message')}"
        )
    return body


def series_by_name(block: dict[str, Any], name: str, what: str) -> dict[str, Any]:
    if name not in block:
        raise ValueError(f"{what}: no {name!r} in {sorted(block)}")
    return block[name]
