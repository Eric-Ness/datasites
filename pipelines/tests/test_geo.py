import io
import json
import zipfile

from datasites_core import geo


def fake_sources():
    """Tiny stand-ins for the CLDR, Census state and Census gazetteer files."""
    mappings = {
        "US": {"_numeric": "840", "_alpha3": "USA"},
        "TW": {"_numeric": "158", "_alpha3": "TWN"},
        "AA": {"_numeric": "958", "_alpha3": "AAA"},  # private use
        "QO": {"_numeric": "961", "_alpha3": "QOO"},  # private use
        "EU": {"_numeric": "967", "_alpha3": "QUU"},  # exceptionally reserved
        "AN": {"_numeric": "530", "_alpha3": "ANT"},  # deprecated
        "001": {"_numeric": "001"},  # a region, not a country
        "UK": None,
    }
    names = {"US": "United States", "TW": "Taiwan", "AA": "x", "QO": "x", "EU": "x", "AN": "x"}
    gazetteer = io.BytesIO()
    with zipfile.ZipFile(gazetteer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "2024_Gaz_counties_national.txt",
            "USPS\tGEOID\tANSICODE\tNAME\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG     \n"
            "CA\t06037\t00277283\tLos Angeles County\t1\t1\t1\t1\t34.196398\t-118.261862   \n"
            "PR\t72001\t01804480\tAdjuntas Municipio\t1\t1\t1\t1\t18.181611\t-66.758165   \n",
        )
    files = {
        geo.CLDR_BASE + "cldr-core/supplemental/codeMappings.json": {"supplemental": {"codeMappings": mappings}},
        geo.CLDR_BASE + "cldr-core/supplemental/aliases.json": {
            "supplemental": {"metadata": {"alias": {"territoryAlias": {"AN": {"_replacement": "CW SX BQ"}}}}}
        },
        geo.CLDR_BASE + "cldr-localenames-full/main/en/territories.json": {
            "main": {"en": {"localeDisplayNames": {"territories": names}}}
        },
    }

    def get(url: str) -> bytes:
        if url == geo.CENSUS_COUNTIES:
            return gazetteer.getvalue()
        if url == geo.CENSUS_STATES:
            return b"STATE|STUSAB|STATE_NAME|STATENS\n06|CA|California|01779778\n11|DC|District of Columbia|01702382\n72|PR|Puerto Rico|01779808\n"
        return json.dumps(files[url]).encode()

    return get


def test_build_filters_and_shapes_locations():
    doc = geo.build(fake_sources())
    by_id = {loc["id"]: loc for loc in doc["locations"]}
    assert sorted(by_id) == ["twn", "us-ca", "us-county-06037", "us-county-72001", "us-dc", "us-pr", "usa", "world"]
    assert by_id["usa"] == {"id": "usa", "level": "country", "name": "United States", "iso2": "US", "iso3": "USA", "numeric": "840", "parent": "world"}
    assert by_id["us-dc"]["kind"] == "district"
    assert by_id["us-pr"]["kind"] == "territory"
    assert by_id["us-county-06037"] == {
        "id": "us-county-06037",
        "level": "us-county",
        "name": "Los Angeles County",
        "fips": "06037",
        "parent": "us-ca",
        "lat": 34.196398,
        "lon": -118.261862,
    }
    # world first, then countries, states, counties
    assert [loc["level"] for loc in doc["locations"]][:3] == ["world", "country", "country"]


def test_dumps_is_deterministic_one_location_per_line():
    doc = geo.build(fake_sources())
    text = geo.dumps(doc)
    assert text == geo.dumps(geo.build(fake_sources()))
    assert json.loads(text) == json.loads(json.dumps(doc))
    assert text.count("\n") == len(doc["locations"]) + 3


def test_committed_registry_is_consistent():
    locations = geo.load()
    assert locations["us-county-06037"]["name"] == "Los Angeles County"
    assert sum(1 for loc in locations.values() if loc["level"] == "country") == 249
    assert all(loc["parent"] in locations for loc in locations.values() if "parent" in loc)
    assert geo.location("nowhere") is None
