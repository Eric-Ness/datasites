from datasites_climate.noaa_gml_co2 import normalize, transform


def rows(table, series, location):
    return {r["date"]: r for r in table.filter((table["series"] == series) & (table["location"] == location)).iter_rows(named=True)}


def test_transform_shape(contract, raw):
    table = transform(raw)
    assert table.columns == contract.columns
    # 12 Mauna Loa months x 2 series + 4 global months x 2 series
    assert len(table) == 12 * 2 + 4 * 2
    assert set(table["series"]) == {"average", "trend"}
    assert set(table["location"]) == {"noaa-mlo", "world"}


def test_mauna_loa_values_and_flags(raw):
    mlo = rows(transform(raw), "average", "noaa-mlo")

    # Scripps era: no interpolation flag or uncertainty is known.
    assert mlo["1958-03"]["value"] == 315.71
    assert mlo["1958-03"]["interpolated"] is None
    assert mlo["1958-03"]["uncertainty"] is None

    # First NOAA month has real day counts and uncertainty.
    assert mlo["1974-05"]["interpolated"] is False
    assert mlo["1974-05"]["uncertainty"] == 0.16

    # NOAA marks interpolated months with a negative stdev and a meaningless 0.00 uncertainty.
    assert mlo["1975-12"]["interpolated"] is True
    assert mlo["1975-12"]["uncertainty"] is None
    assert mlo["1984-04"]["interpolated"] is True

    assert mlo["2026-09"] == {
        "series": "average",
        "location": "noaa-mlo",
        "date": "2026-09",
        "value": 426.07,
        "uncertainty": 0.23,
        "interpolated": False,
    }


def test_trend_and_global(raw):
    table = transform(raw)
    assert rows(table, "trend", "noaa-mlo")["2026-09"]["value"] == 429.55
    assert rows(table, "trend", "noaa-mlo")["2026-09"]["uncertainty"] is None
    world = rows(table, "average", "world")
    assert world["1979-01"]["value"] == 336.56
    assert world["1979-01"]["uncertainty"] == 0.10
    assert world["1979-01"]["interpolated"] is None
    assert rows(table, "trend", "world")["2026-07"]["uncertainty"] == 0.06


def test_normalize_ignores_file_creation_timestamp(raw):
    body = raw["noaa-mlo"]
    regenerated = body.replace(b"Fri Oct  9 13:08:07 2026", b"Sat Oct 10 02:00:00 2026")
    assert regenerated != body
    assert normalize("noaa-mlo", regenerated) == normalize("noaa-mlo", body)
