"""satellitedata/gp-current: the newest general perturbations (GP) element set for every object in orbit,
from Space-Track.org, the US Space Force's public SSA site (https://www.space-track.org/documentation).

Space-Track needs a free account (SPACETRACK_USERNAME / SPACETRACK_PASSWORD); without one the runner
reports this dataset as needs-credentials. One run is a login, one bulk GP query and a logout:

- the query is the one Space-Track's API guidelines recommend for hourly pulls of current element sets,
  '/decay_date/null-val/epoch/>now-10/' (propagable element sets of on-orbit objects only), asked for
  every object at once instead of satellite by satellite;
- every request goes through a limiter that keeps under the documented 30 requests a minute and 300 an
  hour, and the contract's check_every (P1D) keeps GP pulls well under the once-an-hour ceiling;
- JSON, not TLE: TLE cannot carry catalog numbers above 99,999, JSON carries all nine digits.

The response uses the CCSDS Orbit Mean-Elements Message (OMM) keywords (CCSDS 502.0-B-3), which
Space-Track documents as the field set of the GP class.
"""

from __future__ import annotations

import json
import math
import time
from collections import deque
from typing import Any, Callable

import httpx
import polars as pl

from datasites_core.fetch import user_agent

BASE = "https://www.space-track.org"
LOGIN_PATH = "/ajaxauth/login"
LOGOUT_PATH = "/ajaxauth/logout"
GP_PATH = "/basicspacedata/query/class/gp/decay_date/null-val/epoch/%3Enow-10/orderby/norad_cat_id/format/json"

PER_MINUTE = 30  # documented: "less than 30 requests per 1 minute(s)"
PER_HOUR = 300  # documented: "and 300 requests per 1 hour(s)"

# WGS-72 constants, the ones SGP4 element sets are defined with.
MU_KM3_S2 = 398600.8
EARTH_RADIUS_KM = 6378.135

COLUMNS = [
    "norad_cat_id", "norad_block", "object_name", "object_id", "epoch", "mean_motion", "eccentricity", "inclination",
    "ra_of_asc_node", "arg_of_pericenter", "mean_anomaly", "bstar", "mean_motion_dot", "mean_motion_ddot",
    "ephemeris_type", "classification_type", "element_set_no", "rev_at_epoch",
    "period_min", "semimajor_axis_km", "perigee_km", "apogee_km",
    "center_name", "ref_frame", "time_system", "mean_element_theory", "originator", "creation_date",
]
REQUIRED = ["NORAD_CAT_ID", "OBJECT_NAME", "OBJECT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION",
            "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY"]
FLOATS = {"MEAN_MOTION": "mean_motion", "ECCENTRICITY": "eccentricity", "INCLINATION": "inclination",
          "RA_OF_ASC_NODE": "ra_of_asc_node", "ARG_OF_PERICENTER": "arg_of_pericenter", "MEAN_ANOMALY": "mean_anomaly",
          "BSTAR": "bstar", "MEAN_MOTION_DOT": "mean_motion_dot", "MEAN_MOTION_DDOT": "mean_motion_ddot"}
INTS = {"NORAD_CAT_ID": "norad_cat_id", "EPHEMERIS_TYPE": "ephemeris_type", "ELEMENT_SET_NO": "element_set_no",
        "REV_AT_EPOCH": "rev_at_epoch"}
TEXTS = {"OBJECT_NAME": "object_name", "OBJECT_ID": "object_id", "CLASSIFICATION_TYPE": "classification_type",
         "CENTER_NAME": "center_name", "REF_FRAME": "ref_frame", "TIME_SYSTEM": "time_system",
         "MEAN_ELEMENT_THEORY": "mean_element_theory", "ORIGINATOR": "originator"}
TIMES = {"EPOCH": "epoch", "CREATION_DATE": "creation_date"}


class SpaceTrackError(RuntimeError):
    pass


class RateLimiter:
    """Keeps a sliding window under Space-Track's documented limits (fewer than 30/min and 300/hour)."""

    def __init__(self, per_minute: int = PER_MINUTE, per_hour: int = PER_HOUR,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.limits = ((60.0, per_minute - 1), (3600.0, per_hour - 1))  # "less than": stay one below
        self.clock, self.sleep = clock, sleep
        self.sent: deque[float] = deque()

    def wait(self) -> None:
        while True:
            now = self.clock()
            while self.sent and now - self.sent[0] >= 3600.0:
                self.sent.popleft()
            delay = 0.0
            for window, allowed in self.limits:
                recent = [t for t in self.sent if now - t < window]
                if len(recent) >= allowed:
                    delay = max(delay, window - (now - recent[len(recent) - allowed]))
            if delay <= 0:
                self.sent.append(now)
                return
            self.sleep(delay)


LIMITER = RateLimiter()  # one per process: every session in a run counts against the same account limits


class SpaceTrack:
    """A logged-in Space-Track session. Credentials are only ever sent to the login endpoint."""

    def __init__(self, username: str, password: str, *, transport: httpx.BaseTransport | None = None,
                 limiter: RateLimiter | None = None, timeout: float = 300):
        self.limiter = limiter or LIMITER
        self.client = httpx.Client(base_url=BASE, headers={"User-Agent": user_agent()}, timeout=timeout,
                                   follow_redirects=True, transport=transport)
        self._credentials = {"identity": username, "password": password}

    def __enter__(self) -> "SpaceTrack":
        try:
            self._login()
        except BaseException:
            self.client.close()  # no session was opened, so there is nothing to log out of
            raise
        return self

    def _login(self) -> None:
        self.limiter.wait()
        res = self.client.post(LOGIN_PATH, data=self._credentials)
        self._credentials = {}
        if res.status_code != 200:
            raise SpaceTrackError(f"Space-Track login answered HTTP {res.status_code}")
        try:
            answer = res.json()
        except ValueError:
            answer = None
        if isinstance(answer, dict) and answer.get("Login") == "Failed":
            raise SpaceTrackError("Space-Track login failed: check SPACETRACK_USERNAME and SPACETRACK_PASSWORD")

    def __exit__(self, *exc: Any) -> None:
        try:
            self.limiter.wait()
            self.client.get(LOGOUT_PATH)
        except httpx.HTTPError:
            pass  # the session expires on its own; never retry against the server just to log out
        finally:
            self.client.close()

    def query(self, path: str) -> bytes:
        """One query. A failure stops the run: no retry loops against a rate-limited server."""
        self.limiter.wait()
        res = self.client.get(path)
        if res.status_code != 200:
            raise SpaceTrackError(f"Space-Track query answered HTTP {res.status_code}: {res.text[:200]}")
        body = res.content
        try:
            payload = json.loads(body)
        except ValueError as err:
            raise SpaceTrackError(f"Space-Track query did not return JSON: {body[:200]!r}") from err
        if not isinstance(payload, list):
            raise SpaceTrackError(f"Space-Track query returned {type(payload).__name__}, not a list: {str(payload)[:200]}")
        return body


def collect(ctx, *, transport: httpx.BaseTransport | None = None, limiter: RateLimiter | None = None) -> dict[str, bytes]:
    username = ctx.env("SPACETRACK_USERNAME")
    password = ctx.env("SPACETRACK_PASSWORD")
    with SpaceTrack(username, password, transport=transport, limiter=limiter) as session:
        return {"gp": session.query(GP_PATH)}


def _iso(value: str | None) -> str | None:
    """'2026-10-24T03:12:45.123456' (UTC per TIME_SYSTEM) -> '2026-10-24T03:12:45.123456Z'."""
    if value is None or value == "":
        return None
    text = str(value).strip().replace(" ", "T")
    return text if text.endswith("Z") else text + "Z"


def _num(value: Any, kind: type) -> Any:
    if value is None or value == "":
        return None
    return kind(float(value)) if kind is int else kind(value)


def records(body: bytes) -> list[dict[str, Any]]:
    payload = json.loads(body)
    if not isinstance(payload, list):
        raise ValueError("GP response is not a JSON list")
    rows = []
    for item in payload:
        missing = [k for k in REQUIRED if item.get(k) in (None, "")]
        if missing:
            raise ValueError(f"GP record {item.get('NORAD_CAT_ID')!r} lacks {missing}")
        row: dict[str, Any] = {}
        for key, col in FLOATS.items():
            row[col] = _num(item.get(key), float)
        for key, col in INTS.items():
            row[col] = _num(item.get(key), int)
        for key, col in TEXTS.items():
            value = item.get(key)
            row[col] = None if value in (None, "") else str(value)
        for key, col in TIMES.items():
            row[col] = _iso(item.get(key))
        rows.append(row)
    return rows


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    schema = {c: pl.Float64 for c in FLOATS.values()} | {c: pl.Int64 for c in INTS.values()} | {
        c: pl.Utf8 for c in [*TEXTS.values(), *TIMES.values()]}
    frame = pl.DataFrame(records(raw["gp"]), schema=schema)
    n = pl.col("mean_motion")  # rev/day
    rad_per_s = n * (2 * math.pi / 86400.0)
    a = pl.when(n > 0).then((MU_KM3_S2 / rad_per_s.pow(2)).pow(1 / 3)).otherwise(None)
    frame = frame.with_columns(
        pl.when(n > 0).then(1440.0 / n).otherwise(None).alias("period_min"),
        a.alias("semimajor_axis_km"),
        (a * (1 - pl.col("eccentricity")) - EARTH_RADIUS_KM).alias("perigee_km"),
        (a * (1 + pl.col("eccentricity")) - EARTH_RADIUS_KM).alias("apogee_km"),
        # partition: catalog numbers in blocks of 1000, so table/25000.json holds 25000-25999 (the ISS is 25544)
        (pl.col("norad_cat_id") // 1000 * 1000).alias("norad_block"),
    )
    return frame.select(COLUMNS)
