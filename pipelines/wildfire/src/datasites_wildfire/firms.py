"""NASA FIRMS VIIRS 375 m near-real-time detections: the keyless regional text files, cross-satellite
deduplication and clustering into fire events.

Files (no MAP_KEY): https://firms.modaps.eosdis.nasa.gov/data/active_fire/<product>/csv/<prefix>_<region>_7d.csv
for the three VIIRS satellites (S-NPP "N", NOAA-20 "N20", NOAA-21 "N21") and the regions that cover the
US and Canada. The regions are bounding boxes that overlap (the US file reaches into southern Canada and
the Canada file into the northern US), so the same detection appears in two files; exact repeats are
dropped first.

Columns: latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti5,frp,daynight
acq_time is HHMM in UTC.
"""

from __future__ import annotations

import hashlib
import io
from datetime import timedelta

import polars as pl

from ._common import cell_keys, unit_xyz

BASE = "https://firms.modaps.eosdis.nasa.gov/data/active_fire"
SATELLITES = {  # FIRMS folder, file prefix, satellite code in the file
    "snpp": ("suomi-npp-viirs-c2", "SUOMI_VIIRS_C2", "N"),
    "noaa20": ("noaa-20-viirs-c2", "J1_VIIRS_C2", "N20"),
    "noaa21": ("noaa-21-viirs-c2", "J2_VIIRS_C2", "N21"),
}
REGIONS = ("USA_contiguous_and_Hawaii", "Alaska", "Canada")
SATELLITE_NAMES = {"N": "S-NPP", "N20": "NOAA-20", "N21": "NOAA-21"}
SATELLITE_RANK = {"N": 0, "N20": 1, "N21": 2}  # deterministic tie-breaks only

WINDOW = timedelta(days=7)  # rolling window, measured back from the newest detection in the files
DEDUPE_CELL_KM = 0.5  # one VIIRS I-band pixel is 375 m at nadir and ~0.8 km at the swath edge
OVERPASS_MINUTES = 75  # S-NPP, NOAA-20 and NOAA-21 pass within ~60 min of each other; one orbit is ~101 min
CLUSTER_CELL_KM = 1.0  # same or adjacent 1 km cells: always linked within 1 km, up to ~2-3 km apart
CLUSTER_GAP = timedelta(hours=48)

COLUMNS = {
    "latitude": pl.Float64,
    "longitude": pl.Float64,
    "scan": pl.Float64,
    "track": pl.Float64,
    "acq_date": pl.Utf8,
    "acq_time": pl.Utf8,
    "satellite": pl.Utf8,
    "confidence": pl.Utf8,
    "frp": pl.Float64,
    "daynight": pl.Utf8,
}


def file_urls() -> dict[str, str]:
    return {
        f"firms-{sat}-{region.lower()}": f"{BASE}/{folder}/csv/{prefix}_{region}_7d.csv"
        for sat, (folder, prefix, _code) in SATELLITES.items()
        for region in REGIONS
    }


def read(name: str, body: bytes) -> pl.DataFrame:
    """One FIRMS CSV. A header-only file (no detections) is valid and gives an empty frame."""
    if not body.strip():
        raise ValueError(f"{name}: empty response (FIRMS always sends at least the header)")
    header = body.split(b"\n", 1)[0].decode("utf-8", "replace").strip().split(",")
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        raise ValueError(f"{name}: FIRMS file is missing columns {missing} (header: {header})")
    df = pl.read_csv(io.BytesIO(body), schema_overrides=COLUMNS, columns=list(COLUMNS))
    return df.with_columns(
        pl.col("acq_time").str.strip_chars().str.zfill(4),
        pl.col("confidence").str.strip_chars().str.to_lowercase(),
        pl.col("satellite").str.strip_chars(),
    )


def detections(raw: dict[str, bytes]) -> pl.DataFrame:
    """Every distinct detection of the window, with its time (t, UTC) and Earth-centred coordinates."""
    frames = [read(name, body) for name, body in sorted(raw.items()) if name.startswith("firms-")]
    if not frames:
        raise ValueError("no FIRMS files")
    df = pl.concat(frames, how="vertical")
    unknown = sorted(set(df["satellite"].unique().to_list()) - set(SATELLITE_NAMES))
    if unknown:
        raise ValueError(f"unexpected FIRMS satellite codes {unknown}")
    df = df.with_columns(
        (pl.col("acq_date") + " " + pl.col("acq_time")).str.strptime(pl.Datetime("us"), "%Y-%m-%d %H%M").alias("t")
    )
    bad = df.filter(
        pl.col("t").is_null() | pl.col("latitude").is_null() | pl.col("longitude").is_null()
        | (pl.col("latitude").abs() > 90) | (pl.col("longitude").abs() > 180)
    )
    if len(bad):
        raise ValueError(f"{len(bad)} FIRMS rows have unusable time or coordinates (e.g. {bad.row(0)})")
    # The same detection in two overlapping regional files: keep one copy.
    df = df.sort(["t", "satellite", "latitude", "longitude", "frp", "confidence"], nulls_last=True)
    df = df.unique(["satellite", "t", "latitude", "longitude"], keep="first", maintain_order=True)
    if not df.is_empty():
        df = df.filter(pl.col("t") > df["t"].max() - WINDOW)
    return df.with_columns(*unit_xyz(pl.col("latitude"), pl.col("longitude"))).with_row_index("det")


def mark_duplicates(df: pl.DataFrame) -> pl.DataFrame:
    """Adds `kept`: False for detections that repeat another satellite's view of the same spot.

    Within each 0.5 km cell, detections are grouped into overpass sequences: a sequence starts at a
    detection and takes in everything up to 75 minutes after it (S-NPP, NOAA-20 and NOAA-21 cross a
    place within about an hour; the same satellite comes back ~101 minutes later). In each sequence the
    satellite with the most detections in the cell is kept (ties: S-NPP, NOAA-20, NOAA-21) and the other
    satellites' detections there are marked as duplicates. A one-satellite sequence keeps everything."""
    if df.is_empty():
        return df.with_columns(pl.lit(True).alias("kept"))
    keyed = df.with_columns(*cell_keys(DEDUPE_CELL_KM, "d")).with_columns(
        pl.col("satellite").replace_strict(SATELLITE_RANK, return_dtype=pl.Int8).alias("rank"),
        (pl.col("t").dt.epoch("s") // 60).alias("minute"),
    ).sort(["dx", "dy", "dz", "t", "rank", "latitude", "longitude"])
    dx, dy, dz = keyed["dx"].to_list(), keyed["dy"].to_list(), keyed["dz"].to_list()
    minute = keyed["minute"].to_list()
    sequence = [0] * len(dx)
    seq = -1
    anchor = None
    previous_cell = None
    for i in range(len(dx)):
        cell = (dx[i], dy[i], dz[i])
        if cell != previous_cell or minute[i] - anchor > OVERPASS_MINUTES:
            seq += 1
            anchor = minute[i]
            previous_cell = cell
        sequence[i] = seq
    keyed = keyed.with_columns(pl.Series("seq", sequence, dtype=pl.Int64))
    per_sat = keyed.group_by("seq", "rank").len()
    best = per_sat.sort(["seq", "len", "rank"], descending=[False, True, False]).unique("seq", keep="first").select(
        "seq", pl.col("rank").alias("best_rank")
    )
    keyed = keyed.join(best, on="seq", how="left").with_columns((pl.col("rank") == pl.col("best_rank")).alias("kept"))
    return keyed.drop("dx", "dy", "dz", "rank", "minute", "seq", "best_rank").sort("det")


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, a: int) -> int:
        parent = self.parent
        root = a
        while parent[root] != root:
            root = parent[root]
        while parent[a] != root:
            parent[a], a = root, parent[a]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # The smaller root wins, so labels don't depend on the order edges are seen in.
            if ra < rb:
                self.parent[rb] = ra
            else:
                self.parent[ra] = rb


# Half of the 26 neighbours of a cell: each adjacent pair is joined once.
HALF_NEIGHBOURS = [
    (i, j, k)
    for i in (-1, 0, 1)
    for j in (-1, 0, 1)
    for k in (-1, 0, 1)
    if (i, j, k) > (0, 0, 0)
]


def cluster(df: pl.DataFrame) -> pl.DataFrame:
    """Adds `cluster` (an integer label) to every detection.

    Detections belong to the same fire when they lie in the same or adjacent 1 km cells of an
    Earth-centred grid and are at most 48 hours apart, transitively (union-find). Inside a cell,
    detections sorted by time form runs that break at gaps over 48 h; two runs in adjacent cells are
    joined when their time spans, widened by 48 h, overlap, which is exactly "some pair of detections at
    most 48 h apart". Working on runs instead of detection pairs keeps dense fires cheap."""
    if df.is_empty():
        return df.with_columns(pl.lit(None, dtype=pl.Int64).alias("cluster"))
    gap_s = int(CLUSTER_GAP.total_seconds())
    keyed = df.with_columns(*cell_keys(CLUSTER_CELL_KM, "c"), pl.col("t").dt.epoch("s").alias("ts")).sort(["cx", "cy", "cz", "ts", "det"])
    keyed = keyed.with_columns(
        (
            (pl.col("cx") != pl.col("cx").shift(1))
            | (pl.col("cy") != pl.col("cy").shift(1))
            | (pl.col("cz") != pl.col("cz").shift(1))
            | ((pl.col("ts") - pl.col("ts").shift(1)) > gap_s)
        ).fill_null(True).cum_sum().cast(pl.Int64).sub(1).alias("run")
    )
    runs = keyed.group_by("run").agg(
        pl.col("cx").first(), pl.col("cy").first(), pl.col("cz").first(),
        pl.col("ts").min().alias("start"), pl.col("ts").max().alias("end"),
    )
    uf = _UnionFind(len(runs))
    for i, j, k in HALF_NEIGHBOURS:
        shifted = runs.select(
            pl.col("run").alias("other"),
            (pl.col("cx") - i).alias("cx"), (pl.col("cy") - j).alias("cy"), (pl.col("cz") - k).alias("cz"),
            pl.col("start").alias("o_start"), pl.col("end").alias("o_end"),
        )
        pairs = runs.join(shifted, on=["cx", "cy", "cz"], how="inner").filter(
            (pl.col("o_start") <= pl.col("end") + gap_s) & (pl.col("start") <= pl.col("o_end") + gap_s)
        )
        for a, b in zip(pairs["run"].to_list(), pairs["other"].to_list()):
            uf.union(a, b)
    labels = pl.DataFrame({"run": list(range(len(runs))), "cluster": [uf.find(r) for r in range(len(runs))]}, schema={"run": pl.Int64, "cluster": pl.Int64})
    return keyed.join(labels, on="run", how="left").drop("cx", "cy", "cz", "ts", "run").sort("det")


def event_id(satellite: str, t: str, latitude: float, longitude: float) -> str:
    """Stable id from the event's earliest detection (it changes only when that detection leaves the window)."""
    key = f"{satellite}|{t}|{latitude:.5f}|{longitude:.5f}"
    return "vf-" + hashlib.sha256(key.encode()).hexdigest()[:16]
