"""fire-incidents: NIFC WFIGS current wildland fire incident locations, cleaned.

Source layer: WFIGS_Incident_Locations_Current (IRWIN incidents that are wildfires, prescribed fires or
complexes, not yet out, contained or certified, under WFIGS's "fall-off" rules). Paged with resultOffset
(2,000 records per page), ordered by OBJECTID. Times come as epoch milliseconds in UTC; points are
requested in WGS84 (outSR=4326; the layer itself is NAD83).
"""

from __future__ import annotations

from typing import Any

import polars as pl

from ._common import INCIDENTS_LAYER, MAX_RECORD_COUNT, irwin, iso_ms, number, page_urls, read_pages, us_state

FIELDS = [
    "OBJECTID", "IrwinID", "UniqueFireIdentifier", "IncidentName", "IncidentTypeCategory", "IncidentTypeKind",
    "POOState", "POOCounty", "POOFips", "GACC", "FireDiscoveryDateTime", "IncidentSize", "DiscoveryAcres",
    "PercentContained", "ContainmentDateTime", "ControlDateTime", "FireOutDateTime", "FireCause",
    "IncidentComplexityLevel", "TotalIncidentPersonnel", "EstimatedCostToDate", "IncidentManagementOrganization",
    "POOJurisdictionalAgency", "POOLandownerKind", "CpxName", "CpxID", "IsCpxChild", "ModifiedOnDateTime_dt",
]
QUERY = {"outFields": ",".join(FIELDS), "outSR": 4326, "returnGeometry": "true", "f": "json"}


def resources(ctx) -> dict[str, str]:
    return page_urls(ctx, INCIDENTS_LAYER, QUERY, MAX_RECORD_COUNT)


SCHEMA = {
    "irwin_id": pl.Utf8,
    "unique_fire_id": pl.Utf8,
    "name": pl.Utf8,
    "type": pl.Utf8,
    "state": pl.Utf8,
    "county": pl.Utf8,
    "county_fips": pl.Utf8,
    "gacc": pl.Utf8,
    "latitude": pl.Float64,
    "longitude": pl.Float64,
    "discovered": pl.Utf8,
    "acres": pl.Float64,
    "discovery_acres": pl.Float64,
    "percent_contained": pl.Float64,
    "contained": pl.Utf8,
    "controlled": pl.Utf8,
    "out": pl.Utf8,
    "cause": pl.Utf8,
    "complexity": pl.Utf8,
    "personnel": pl.Int64,
    "cost_to_date": pl.Float64,
    "management_organization": pl.Utf8,
    "jurisdictional_agency": pl.Utf8,
    "landowner_kind": pl.Utf8,
    "complex_name": pl.Utf8,
    "complex_irwin_id": pl.Utf8,
    "is_complex_child": pl.Boolean,
    "modified": pl.Utf8,
}


def _fips(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text.zfill(5) if text.isdigit() and len(text) <= 5 else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _personnel(value: Any) -> int | None:
    n = number(value)
    return int(n) if n is not None else None


def records(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for feature in features:
        a = feature.get("attributes") or {}
        g = feature.get("geometry") or {}
        lat, lon = number(g.get("y")), number(g.get("x"))
        out.append(
            {
                "irwin_id": irwin(a.get("IrwinID")),
                "unique_fire_id": _text(a.get("UniqueFireIdentifier")),
                "name": _text(a.get("IncidentName")),
                "type": _text(a.get("IncidentTypeCategory")),
                "state": us_state(a.get("POOState")),
                "county": _text(a.get("POOCounty")),
                "county_fips": _fips(a.get("POOFips")),
                "gacc": _text(a.get("GACC")),
                "latitude": round(lat, 6) if lat is not None else None,
                "longitude": round(lon, 6) if lon is not None else None,
                "discovered": iso_ms(a.get("FireDiscoveryDateTime")),
                "acres": number(a.get("IncidentSize")),
                "discovery_acres": number(a.get("DiscoveryAcres")),
                "percent_contained": number(a.get("PercentContained")),
                "contained": iso_ms(a.get("ContainmentDateTime")),
                "controlled": iso_ms(a.get("ControlDateTime")),
                "out": iso_ms(a.get("FireOutDateTime")),
                "cause": _text(a.get("FireCause")),
                "complexity": _text(a.get("IncidentComplexityLevel")),
                "personnel": _personnel(a.get("TotalIncidentPersonnel")),
                "cost_to_date": number(a.get("EstimatedCostToDate")),
                "management_organization": _text(a.get("IncidentManagementOrganization")),
                "jurisdictional_agency": _text(a.get("POOJurisdictionalAgency")),
                "landowner_kind": _text(a.get("POOLandownerKind")),
                "complex_name": _text(a.get("CpxName")),
                "complex_irwin_id": irwin(a.get("CpxID")),
                "is_complex_child": bool(a["IsCpxChild"]) if a.get("IsCpxChild") is not None else None,
                "modified": iso_ms(a.get("ModifiedOnDateTime_dt")),
            }
        )
    return out


def incidents(raw: dict[str, bytes], prefix: str = "page-") -> pl.DataFrame:
    """One row per IRWIN incident. If WFIGS lists an IRWIN id twice, the most recently modified record wins."""
    rows = records(read_pages(raw, prefix))
    df = pl.DataFrame(rows, schema=SCHEMA, orient="row") if rows else pl.DataFrame(schema=SCHEMA)
    if df["irwin_id"].null_count():
        raise ValueError(f"{df['irwin_id'].null_count()} WFIGS incidents have no IrwinID")
    return (
        df.sort(["irwin_id", "modified", "unique_fire_id"], descending=[False, True, False], nulls_last=True)
        .unique("irwin_id", keep="first", maintain_order=True)
        .with_columns(pl.col("state").fill_null("unknown"))
    )


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    return incidents(raw).select(list(SCHEMA))
