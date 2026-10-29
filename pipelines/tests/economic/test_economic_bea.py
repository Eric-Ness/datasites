import io
import zipfile

import polars as pl
import pytest

from datasites_core.validate import validate
from datasites_economic import bea_cainc1
from economic_helpers import contract, fixture

EXCERPT = "bea_cainc1_all_areas_excerpt.csv"  # seven areas of BEA's every-area CSV, 2023-2024


def zipped(csv: bytes, name: str = "CAINC1__ALL_AREAS_1969_2024.csv") -> bytes:
    """BEA's zip: the every-area CSV next to the per-state ones, which must be ignored."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, csv)
        zf.writestr("CAINC1_DE_1969_2024.csv", b"not read")
        zf.writestr("CAINC1__Footnotes.html", b"<html></html>")
    return buf.getvalue()


def test_keyless_bulk_download():
    assert bea_cainc1.RESOURCES == {"cainc1": "https://apps.bea.gov/regional/zip/CAINC1.zip"}
    assert contract("bea-county-income").requires_env == ()


def test_reads_the_every_area_csv_from_the_zip():
    table = bea_cainc1.transform({"cainc1": zipped(fixture(EXCERPT))})
    assert table.columns == ["series", "location", "date", "personal_income", "population", "per_capita_personal_income", "location_name"]
    assert table.schema["per_capita_personal_income"] == pl.Int64
    rows = {(r["location"], r["date"]): r for r in table.to_dicts()}
    assert rows[("usa", "2024")] == {
        "series": "annual", "location": "usa", "date": "2024", "personal_income": 24897613000,
        "population": 340110988, "per_capita_personal_income": 73204, "location_name": "United States",
    }  # fmt: skip
    assert rows[("us-county-10001", "2023")]["per_capita_personal_income"] == 52818
    assert rows[("us-county-35013", "2024")]["location_name"] == "Doña Ana, NM"  # Latin-1 in BEA's file
    assert rows[("bea-51901", "2024")]["location_name"] == "Albemarle + Charlottesville, VA"  # BEA's * dropped


def test_markers_regions_and_notes_are_left_out():
    table = bea_cainc1.transform({"cainc1": zipped(fixture(EXCERPT))})
    locations = set(table["location"])
    assert locations == {"usa", "us-de", "us-county-10001", "us-county-35013", "bea-51901", "us-county-09110"}
    # Capitol planning region is (NA) for 2023 (before BEA published planning regions): no row, not zeros.
    assert table.filter(pl.col("location") == "us-county-09110")["date"].to_list() == ["2024"]
    assert len(table) == 11


def test_change_detection_reads_through_the_zip():
    csv = fixture(EXCERPT)
    assert bea_cainc1.normalize("cainc1", zipped(csv)) == csv
    assert bea_cainc1.normalize("cainc1", csv) == csv


def test_a_zip_without_the_every_area_csv_is_refused():
    with pytest.raises(ValueError, match="CAINC1__ALL_AREAS"):
        bea_cainc1.transform({"cainc1": zipped(fixture(EXCERPT), name="CAINC1_CT_1969_2024.csv")})


def test_another_unit_under_a_line_is_refused():
    csv = fixture(EXCERPT).replace(b"Per capita personal income (dollars) 2/,Dollars", b"Per capita personal income (dollars) 2/,Thousands of dollars")
    with pytest.raises(ValueError, match="line 3: expected Dollars"):
        bea_cainc1.transform({"cainc1": csv})


def test_geography_codes():
    fips = ["00000", "01000", "97000", "98000", "10001", "51901", "15901", "09001"]
    ids = pl.DataFrame({"fips": fips}).select(bea_cainc1.location_ids(pl.col("fips")).alias("id"))["id"].to_list()
    assert dict(zip(fips, ids)) == {
        "00000": "usa",
        "01000": "us-al",
        "97000": None,  # BEA region (Rocky Mountain): left out
        "98000": None,  # BEA region (Far West)
        "10001": "us-county-10001",
        "51901": "bea-51901",  # Albemarle + Charlottesville, a BEA combination area
        "15901": "bea-15901",  # Maui + Kalawao
        "09001": "us-county-09001",  # Fairfield County CT, legacy county
    }


def test_validation_blocks_a_partial_table():
    # Line 3 only: personal income and population are entirely null, so the contract's null limits stop it.
    lines = fixture(EXCERPT).split(b"\n")
    only_line3 = b"\n".join(l for l in lines if b",CAINC1," not in l or b",CAINC1,3," in l)
    table = bea_cainc1.transform({"cainc1": only_line3})
    report = validate(contract("bea-county-income", min_rows=None), table.drop("location_name"), None)
    assert report.errors == ["personal_income: 100.0% null, limit 5.0%", "population: 100.0% null, limit 5.0%"]
