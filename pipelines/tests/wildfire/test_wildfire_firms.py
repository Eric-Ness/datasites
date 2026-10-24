"""FIRMS parsing, cross-satellite deduplication and clustering."""

import csv
import io
import math
import random
from datetime import datetime, timedelta

import polars as pl
import pytest
from wildfire_helpers import firms_csv, firms_raw

from datasites_wildfire import firms


def run(rows):
    """detections -> dedupe -> cluster for constructed rows; returns the frame sorted by input order."""
    det = firms.detections({"firms-test": firms_csv(rows)})
    return firms.cluster(firms.mark_duplicates(det))


def groups(det):
    """Clusters as sets of (lat, lon, time) keys, independent of label values."""
    out = {}
    for row in det.iter_rows(named=True):
        out.setdefault(row["cluster"], set()).add((row["latitude"], row["longitude"], row["acq_time"], row["acq_date"]))
    return sorted(sorted(g) for g in out.values())


def km_east(lat, km):
    return km / (111.195 * math.cos(math.radians(lat)))


def test_urls_are_the_keyless_7_day_files():
    urls = firms.file_urls()
    assert len(urls) == 9
    assert urls["firms-noaa21-canada"] == "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_Canada_7d.csv"
    assert urls["firms-snpp-usa_contiguous_and_hawaii"].endswith("/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_USA_contiguous_and_Hawaii_7d.csv")
    assert all("MAP_KEY" not in u and "/api/" not in u for u in urls.values())


def test_overlapping_region_files_are_deduplicated():
    raw = firms_raw()
    keys, total = set(), 0
    for name, body in raw.items():
        for row in csv.DictReader(io.StringIO(body.decode())):
            total += 1
            moment = datetime.strptime(row["acq_date"] + row["acq_time"].zfill(4), "%Y-%m-%d%H%M")
            keys.add((row["satellite"], moment, float(row["latitude"]), float(row["longitude"])))
    newest = max(k[1] for k in keys)
    in_window = {k for k in keys if k[1] > newest - timedelta(days=7)}
    det = firms.detections(raw)
    # The US file's box reaches north to Canada and the Canada file's south into Oregon: many rows twice.
    assert total == 2575 and len(keys) == 1379 and len(in_window) == 1363
    assert det.height == len(in_window)
    assert det["det"].to_list() == list(range(det.height))
    assert det["t"].dtype == pl.Datetime("us")


def test_acq_time_is_utc_hhmm_and_zero_padded():
    det = firms.detections({"firms-test": firms_csv([(44.0, -118.8, "2026-10-21", "47", "N20", 3.0)])})
    assert det["t"][0].isoformat() == "2026-10-21T00:47:00"


def test_header_only_file_is_an_empty_day_not_an_error():
    det = firms.detections({"firms-a": b"latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti5,frp,daynight\n"})
    assert det.is_empty()
    assert firms.cluster(firms.mark_duplicates(det)).is_empty()


def test_broken_files_raise():
    with pytest.raises(ValueError, match="missing columns"):
        firms.detections({"firms-a": b"<html>Service Unavailable</html>\n"})
    with pytest.raises(ValueError, match="empty response"):
        firms.detections({"firms-a": b""})
    with pytest.raises(ValueError, match="satellite codes"):
        firms.detections({"firms-a": firms_csv([(44.0, -118.8, "2026-10-21", "0947", "T", 3.0)])})


def test_window_keeps_the_last_7_days_before_the_newest_detection():
    rows = [(44.0, -118.8, "2026-10-14", "1000", "N", 1.0), (44.0, -118.8, "2026-10-14", "1100", "N", 1.0), (44.0, -118.8, "2026-10-21", "1030", "N", 1.0)]
    det = firms.detections({"firms-a": firms_csv(rows)})
    assert det["acq_time"].to_list() == ["1100", "1030"]


# --- cross-satellite deduplication -------------------------------------------------------------------


def test_one_overpass_sequence_keeps_the_satellite_with_most_pixels():
    lat, lon = 44.03, -118.77
    rows = [
        (lat, lon, "2026-10-21", "0933", "N21", 10.0),
        (lat, lon + km_east(lat, 0.05), "2026-10-21", "1009", "N", 12.0),
        (lat + 0.0003, lon, "2026-10-21", "1009", "N", 8.0),  # a second S-NPP pixel in the same 0.5 km cell
        (lat, lon, "2026-10-21", "1031", "N20", 11.0),
    ]
    det = run(rows).sort("t")
    # The cells must coincide for this test to mean anything.
    cells = det.with_columns(*firms.cell_keys(firms.DEDUPE_CELL_KM, "d")).select("dx", "dy", "dz").unique()
    assert len(cells) == 1
    assert det["kept"].to_list() == [False, True, True, False]  # S-NPP had 2 pixels; NOAA-21 and NOAA-20 had 1


def test_ties_prefer_snpp_then_noaa20():
    rows = [(44.03, -118.77, "2026-10-21", "0933", "N21", 1.0), (44.03, -118.77, "2026-10-21", "1031", "N20", 1.0)]
    det = run(rows).sort("t")
    assert det["kept"].to_list() == [False, True]


def test_next_orbit_is_a_new_sequence():
    rows = [
        (44.03, -118.77, "2026-10-21", "0933", "N21", 1.0),
        (44.03, -118.77, "2026-10-21", "1009", "N", 1.0),
        (44.03, -118.77, "2026-10-21", "1114", "N21", 1.0),  # 101 minutes after the first: next orbit
    ]
    det = run(rows).sort("t")
    # NOAA-21 and S-NPP tie in the first sequence (S-NPP is kept); the next NOAA-21 pass starts a new one.
    assert det["kept"].to_list() == [False, True, True]


def test_distinct_spots_are_never_duplicates():
    rows = [(44.03, -118.77, "2026-10-21", "0933", "N21", 1.0), (44.03, -118.77 + km_east(44.03, 1.2), "2026-10-21", "1009", "N", 1.0)]
    assert run(rows)["kept"].to_list() == [True, True]


def test_dedupe_on_the_real_excerpt_matches_a_plain_python_reference():
    """The polars implementation against a straightforward re-implementation of the rule."""
    det = firms.mark_duplicates(firms.detections(firms_raw()))
    r = 6371.0088
    cells = {}
    for row in det.iter_rows(named=True):
        phi, lam = math.radians(row["latitude"]), math.radians(row["longitude"])
        xyz = (r * math.cos(phi) * math.cos(lam), r * math.cos(phi) * math.sin(lam), r * math.sin(phi))
        cell = tuple(math.floor(v / firms.DEDUPE_CELL_KM) for v in xyz)
        cells.setdefault(cell, []).append((row["t"], firms.SATELLITE_RANK[row["satellite"]], row["latitude"], row["longitude"]))
    expected = 0
    for members in cells.values():
        members.sort()
        sequences, anchor = [], None
        for m in members:
            if anchor is None or (m[0] - anchor).total_seconds() > firms.OVERPASS_MINUTES * 60:
                sequences.append([])
                anchor = m[0]
            sequences[-1].append(m)
        for seq in sequences:
            per_sat = {}
            for m in seq:
                per_sat[m[1]] = per_sat.get(m[1], 0) + 1
            expected += max(per_sat.values())
    assert 0 < expected < det.height
    assert det["kept"].sum() == expected


# --- clustering ----------------------------------------------------------------------------------------


def test_close_detections_join_and_far_ones_do_not():
    lat, lon = 44.0, -118.8
    rows = [
        (lat, lon, "2026-10-20", "1000", "N", 1.0),
        (lat, lon + km_east(lat, 0.8), "2026-10-20", "1000", "N", 1.0),
        (lat, lon + km_east(lat, 5.0), "2026-10-20", "1000", "N", 1.0),
    ]
    det = run(rows)
    label = dict(zip(det["longitude"].to_list(), det["cluster"].to_list()))
    assert label[rows[0][1]] == label[rows[1][1]] != label[rows[2][1]]


def test_chains_are_transitive():
    lat, lon = 44.0, -118.8
    rows = [(lat, lon + km_east(lat, 0.7 * i), "2026-10-20", "1000", "N", 1.0) for i in range(12)]  # 7.7 km line
    assert run(rows)["cluster"].n_unique() == 1


def test_time_gap_of_48_hours_is_the_limit():
    lat, lon = 44.0, -118.8
    joined = run([(lat, lon, "2026-10-18", "1000", "N", 1.0), (lat, lon, "2026-10-20", "1000", "N20", 1.0)])
    split = run([(lat, lon, "2026-10-18", "1000", "N", 1.0), (lat, lon, "2026-10-20", "1001", "N20", 1.0)])
    assert joined["cluster"].n_unique() == 1
    assert split["cluster"].n_unique() == 2


def test_adjacent_cell_runs_join_through_their_time_spans():
    """A run in one cell spanning days joins a neighbour seen once mid-way, and one seen 49 h after it ends
    does not."""
    lat, lon = 44.0, -118.8
    a = [(lat, lon, f"2026-10-{d}", "1000", "N", 1.0) for d in (17, 18, 19)]
    b_mid = [(lat, lon + km_east(lat, 0.9), "2026-10-18", "2200", "N", 1.0)]
    b_late = [(lat, lon + km_east(lat, 0.9), "2026-10-21", "1100", "N", 1.0)]
    assert run(a + b_mid)["cluster"].n_unique() == 1
    assert run(a + b_late)["cluster"].n_unique() == 2


def test_dateline_and_high_latitudes_have_no_seams():
    # 0.0002 degrees either side of the 180th meridian at 52 N is ~30 m apart.
    across = run([(52.0, 179.9999, "2026-10-20", "1000", "N", 1.0), (52.0, -179.9999, "2026-10-20", "1000", "N", 1.0)])
    assert across["cluster"].n_unique() == 1
    # Near the pole a degree of longitude is tiny: 82 N, 0.05 degrees apart is ~0.77 km.
    polar = run([(82.0, -70.0, "2026-10-20", "1000", "N", 1.0), (82.0, -70.05, "2026-10-20", "1000", "N", 1.0), (82.0, -71.0, "2026-10-20", "1000", "N", 1.0)])
    label = dict(zip(polar["longitude"].to_list(), polar["cluster"].to_list()))
    assert label[-70.0] == label[-70.05] != label[-71.0]


def test_clustering_is_deterministic_under_input_order():
    raw = firms_raw()
    first = groups(firms.cluster(firms.mark_duplicates(firms.detections(raw))))
    shuffled = {}
    rng = random.Random(7)
    for name, body in raw.items():
        header, *rows = body.decode().strip().split("\n")
        rng.shuffle(rows)
        shuffled[name[::-1]] = ("\n".join([header, *rows]) + "\n").encode()  # file order changes too
    second = groups(firms.cluster(firms.mark_duplicates(firms.detections({f"firms-{k}": v for k, v in shuffled.items()}))))
    assert first == second


def test_event_id_is_stable_and_compact():
    assert firms.event_id("N20", "2026-10-17T09:24:00Z", 44.0, -118.8) == firms.event_id("N20", "2026-10-17T09:24:00Z", 44.000001, -118.800001)
    assert firms.event_id("N20", "2026-10-17T09:24:00Z", 44.0, -118.8).startswith("vf-")
    assert len(firms.event_id("N", "2026-10-17T09:24:00Z", 44.0, -118.8)) == 19
