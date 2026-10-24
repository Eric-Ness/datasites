import polars as pl
import pytest
from air_helpers import airdata_zip, fixture

from datasites_air import aqi


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "Good"),
        (50, "Good"),
        (51, "Moderate"),
        (100, "Moderate"),
        (101, "Unhealthy for Sensitive Groups"),
        (150, "Unhealthy for Sensitive Groups"),
        (151, "Unhealthy"),
        (200, "Unhealthy"),
        (201, "Very Unhealthy"),
        (300, "Very Unhealthy"),
        (301, "Hazardous"),
        (500, "Hazardous"),
        (
            22897,
            "Hazardous",
        ),  # AirData's highest value (Inyo County PM10, 2001-05-02) is still Hazardous
    ],
)
def test_category_boundaries(value, expected):
    assert aqi.category(value) == expected
    assert (
        pl.DataFrame({"aqi": [value]}).select(aqi.category_expr("aqi")).item()
        == expected
    )


def test_negative_aqi_is_rejected():
    with pytest.raises(ValueError):
        aqi.category(-1)


def test_category_expr_matches_every_airdata_row_in_the_fixtures():
    for year in (1980, 2000, 2025, 2026):
        df = aqi.read_airdata_csv(
            airdata_zip(f"daily_aqi_by_county_{year}.csv"),
            {"AQI": "aqi", "Category": "category"},
            "t",
        )
        df = df.with_columns(pl.col("aqi").cast(pl.Int64))
        assert df.select(
            (aqi.category_expr("aqi") == pl.col("category")).all()
        ).item(), year


def test_years_come_from_the_download_page():
    page = fixture("download_files.html").decode()
    assert aqi.airdata_years(page, "daily_aqi_by_county") == list(range(1980, 2027))
    assert aqi.airdata_years(page, "annual_aqi_by_county") == list(range(1980, 2027))


def test_a_page_missing_a_year_is_rejected():
    page = (
        fixture("download_files.html")
        .decode()
        .replace('href="daily_aqi_by_county_1995.zip"', 'href="x.zip"')
    )
    with pytest.raises(ValueError, match="every year"):
        aqi.airdata_years(page, "daily_aqi_by_county")


def test_normalize_ignores_a_rezip_with_new_timestamps():
    a = airdata_zip("daily_aqi_by_county_2026.csv", date_time=(2026, 6, 26, 7, 28, 0))
    b = airdata_zip("daily_aqi_by_county_2026.csv", date_time=(2026, 12, 18, 3, 1, 0))
    assert a != b
    assert (
        aqi.normalize_zip("airdata-2026", a)
        == aqi.normalize_zip("airdata-2026", b)
        == fixture("daily_aqi_by_county_2026.csv")
    )


def test_renamed_columns_are_reported():
    body = fixture("daily_aqi_by_county_2026.csv").replace(
        b'"Defining Parameter"', b'"Main Pollutant"'
    )
    with pytest.raises(ValueError, match="Defining Parameter"):
        aqi.read_airdata_csv(
            body, {"Defining Parameter": "p"}, "daily_aqi_by_county_2026"
        )
