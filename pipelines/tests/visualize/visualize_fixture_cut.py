"""How the fixtures in fixtures/ were cut (not a test; run by hand when a fixture needs re-cutting).

    uv run python tests/visualize/visualize_fixture_cut.py <folder with the downloaded source files>

The folder must hold the files exactly as downloaded on 2026-10-24 from
  https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_{state,county,cd119}_{20m,5m}.zip
  https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_{110m,50m}_admin_0_countries.geojson

Census: for each zip, the records whose GEOID is listed in CENSUS below are copied with pyshp (attributes
and shapes unchanged, fields as declared in the source .dbf) into a new shapefile with the source's member
names; the source .prj and .cpg are copied byte for byte. Natural Earth: the features named in NE below
are copied verbatim (json round trip) into a FeatureCollection with a "_fixture" member naming the source;
the source's other top-level members (name, crs, bbox) are kept as they are.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import shapefile

FIXTURES = Path(__file__).parent / "fixtures"
CAPTURED = "2026-10-24"

CENSUS = {
    # Los Angeles (islands: a MultiPolygon), Aleutians West (both sides of 180), Albemarle (a hole, filled by
    # Charlottesville), a Connecticut planning region.
    "cb_2025_us_county_20m": ["06037", "02016", "51003", "51540", "09110"],
    # plus an Island Area county-equivalent, which has no registry id
    "cb_2025_us_county_5m": ["06037", "60010"],
    "cb_2025_us_state_20m": ["11", "44"],
    "cb_2025_us_state_5m": ["11", "66"],
    # Delaware at-large (00) and the DC delegate district (98)
    "cb_2025_us_cd119_20m": ["1000", "1198"],
    "cb_2025_us_cd119_5m": ["1000", "6698"],
}
NE = {
    # Kosovo (no ISO code), Norway and France (ISO_A3 -99 but ISO_A3_EH set), Fiji (split at 180),
    # South Africa (a hole for Lesotho), Lesotho
    "ne_110m_admin_0_countries": ["Kosovo", "Norway", "France", "Fiji", "South Africa", "Lesotho"],
    # Indian Ocean Ter. (ISO_A3_EH is AUS: the -99 rule keeps it from clashing with Australia), Australia
    "ne_50m_admin_0_countries": ["Indian Ocean Ter.", "Australia", "Kosovo"],
}


def cut_census(src: Path, stem: str, geoids: list[str]) -> None:
    with zipfile.ZipFile(src / f"{stem}.zip") as zf:
        m = {n.rsplit(".", 1)[-1]: zf.read(n) for n in zf.namelist() if n.count(".") == 1}
    reader = shapefile.Reader(shp=io.BytesIO(m["shp"]), shx=io.BytesIO(m["shx"]), dbf=io.BytesIO(m["dbf"]), encoding="utf-8")
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    writer = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=reader.shapeType, encoding="utf-8")
    writer.fields = list(reader.fields[1:])
    found = []
    for sr in reader.iterShapeRecords():
        if sr.record["GEOID"] in geoids:
            writer.record(*sr.record)
            writer.shape(sr.shape)
            found.append(sr.record["GEOID"])
    writer.close()
    reader.close()
    assert sorted(found) == sorted(geoids), (stem, found)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.comment = (
            f"Fixture: records {', '.join(geoids)} of https://www2.census.gov/geo/tiger/GENZ2025/shp/{stem}.zip, "
            f"captured {CAPTURED}; cut by tests/visualize/visualize_fixture_cut.py"
        ).encode()
        for ext, body in (("shp", shp.getvalue()), ("shx", shx.getvalue()), ("dbf", dbf.getvalue()), ("prj", m["prj"]), ("cpg", m["cpg"])):
            zf.writestr(f"{stem}.{ext}", body)
    (FIXTURES / f"{stem}.zip").write_bytes(out.getvalue())
    (FIXTURES / f"{stem}.zip.source.txt").write_text(
        f"Source: https://www2.census.gov/geo/tiger/GENZ2025/shp/{stem}.zip\n"
        f"Captured: {CAPTURED}\n"
        f"Cut: records with GEOID {', '.join(geoids)} copied unchanged with pyshp into a new shapefile with the\n"
        "source's member names; .prj and .cpg copied byte for byte (tests/visualize/visualize_fixture_cut.py).\n",
        encoding="utf-8",
        newline="\n",
    )


def cut_ne(src: Path, stem: str, names: list[str]) -> None:
    doc = json.loads((src / f"{stem}.geojson").read_text(encoding="utf-8"))
    feats = [f for f in doc["features"] if f["properties"]["NAME"] in names]
    assert sorted(f["properties"]["NAME"] for f in feats) == sorted(names), stem
    url = f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/{stem}.geojson"
    out = {
        "_fixture": f"Verbatim features {', '.join(names)} of {url}, captured {CAPTURED}; cut by tests/visualize/visualize_fixture_cut.py",
        **{k: v for k, v in doc.items() if k != "features"},
        "features": feats,
    }
    (FIXTURES / f"{stem}.geojson").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    source = Path(sys.argv[1])
    FIXTURES.mkdir(exist_ok=True)
    for stem, ids in CENSUS.items():
        cut_census(source, stem, ids)
    for stem, names in NE.items():
        cut_ne(source, stem, names)
