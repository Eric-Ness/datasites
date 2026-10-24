"""The Space-Track GP adapter, offline. Its fixture is schema-derived (see the fixture's header): Space-Track
shows example responses only to logged-in users."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from datasites_core import cli
from datasites_core.contracts import load_contract
from datasites_core.fetch import CollectContext
from datasites_core.layout import blob_key, version_key
from datasites_core.publish import run_dataset
from datasites_core.store import get_json
from datasites_satellite import spacetrack_gp
from satellite_helpers import NOW, fixture, relaxed, rows_by

REF = "satellitedata/gp-current"
GP = fixture("spacetrack_gp_schema_derived.json")


class FakeSpaceTrack:
    """Answers like www.space-track.org: a login that sets a session cookie, then queries that need it."""

    def __init__(self, *, login_ok: bool = True, gp: bytes = GP, gp_status: int = 200):
        self.login_ok, self.gp, self.gp_status = login_ok, gp, gp_status
        self.calls: list[tuple[str, str]] = []
        self.form: dict | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.raw_path.decode()))
        if request.url.path == spacetrack_gp.LOGIN_PATH:
            self.form = parse_qs(request.content.decode())
            if not self.login_ok:
                return httpx.Response(200, json={"Login": "Failed"})
            return httpx.Response(200, text='""', headers={"Set-Cookie": "chocolatechip=abc; path=/"})
        if "chocolatechip=abc" not in request.headers.get("cookie", ""):
            return httpx.Response(401, json={"error": "You must be logged in"})
        if request.url.path == spacetrack_gp.LOGOUT_PATH:
            return httpx.Response(200, text='"Successfully logged out"')
        return httpx.Response(self.gp_status, content=self.gp)


class FakeClock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("SPACETRACK_USERNAME", "test-user")
    monkeypatch.setenv("SPACETRACK_PASSWORD", "test-password")


def ctx():
    return CollectContext(load_contract(REF), NOW)


def test_columns_match_the_contract():
    assert spacetrack_gp.transform({"gp": GP}).columns == load_contract(REF).columns


def test_parses_omm_fields_and_six_digit_numbers():
    rows = rows_by(spacetrack_gp.transform({"gp": GP}), "norad_cat_id")
    assert sorted(rows) == [5, 25544, 100001]
    iss = rows[25544]
    assert (iss["object_name"], iss["object_id"], iss["epoch"]) == ("ISS (ZARYA)", "1998-067A", "2026-10-24T03:41:22.817280Z")
    assert (iss["mean_motion"], iss["eccentricity"], iss["inclination"], iss["bstar"]) == (15.49812345, 0.0007123, 51.6312, 0.000215)
    assert (iss["element_set_no"], iss["rev_at_epoch"], iss["classification_type"], iss["ref_frame"]) == (999, 54321, "U", "TEME")
    assert rows[100001]["object_id"] == "2026-160A"  # beyond the five digits a TLE can carry


def test_derived_orbit_sizes():
    iss = rows_by(spacetrack_gp.transform({"gp": GP}), "norad_cat_id")[25544]
    assert iss["period_min"] == pytest.approx(1440 / 15.49812345)
    assert iss["semimajor_axis_km"] == pytest.approx(6795.2, abs=0.5)
    assert 400 < iss["perigee_km"] < iss["apogee_km"] < 430


def test_missing_mandatory_omm_keyword_is_rejected():
    broken = json.loads(GP)
    del broken[0]["EPOCH"]
    with pytest.raises(ValueError, match="lacks \\['EPOCH'\\]"):
        spacetrack_gp.transform({"gp": json.dumps(broken).encode()})


def test_collect_logs_in_queries_once_and_logs_out(credentials):
    server = FakeSpaceTrack()
    clock = FakeClock()
    raw = spacetrack_gp.collect(ctx(), transport=httpx.MockTransport(server), limiter=spacetrack_gp.RateLimiter(clock=clock, sleep=clock.sleep))
    assert raw == {"gp": GP}
    assert server.calls == [
        ("POST", "/ajaxauth/login"),
        ("GET", "/basicspacedata/query/class/gp/decay_date/null-val/epoch/%3Enow-10/orderby/norad_cat_id/format/json"),
        ("GET", "/ajaxauth/logout"),
    ]
    assert server.form == {"identity": ["test-user"], "password": ["test-password"]}


def test_failed_login_stops_before_any_query(credentials):
    server = FakeSpaceTrack(login_ok=False)
    with pytest.raises(spacetrack_gp.SpaceTrackError, match="login failed"):
        spacetrack_gp.collect(ctx(), transport=httpx.MockTransport(server))
    assert server.calls == [("POST", "/ajaxauth/login")]
    assert "test-password" not in repr(server.calls)


def test_error_answers_are_not_published(credentials):
    for server in (FakeSpaceTrack(gp=b'{"error": "query timed out"}'), FakeSpaceTrack(gp=b"Too many requests", gp_status=429)):
        with pytest.raises(spacetrack_gp.SpaceTrackError):
            spacetrack_gp.collect(ctx(), transport=httpx.MockTransport(server))
        assert sum(1 for method, path in server.calls if "basicspacedata" in path) == 1  # never retried in a loop


def test_rate_limiter_stays_under_the_documented_limits():
    clock = FakeClock()
    limiter = spacetrack_gp.RateLimiter(clock=clock, sleep=clock.sleep)
    for _ in range(29):
        limiter.wait()
    assert clock.sleeps == []  # 29 within a minute is "less than 30"
    limiter.wait()
    assert clock.sleeps == [60.0]
    for _ in range(300):
        limiter.wait()
    times = sorted(limiter.sent)
    assert all(sum(1 for t in times if start <= t < start + 60) < 30 for start in times)
    assert all(sum(1 for t in times if start <= t < start + 3600) < 300 for start in times)


def test_without_credentials_collect_names_the_missing_variable(monkeypatch):
    monkeypatch.delenv("SPACETRACK_USERNAME", raising=False)
    monkeypatch.delenv("SPACETRACK_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="SPACETRACK_USERNAME"):
        spacetrack_gp.collect(ctx(), transport=httpx.MockTransport(FakeSpaceTrack()))


def test_runner_reports_needs_credentials(monkeypatch, store, capsys):
    monkeypatch.delenv("SPACETRACK_USERNAME", raising=False)
    monkeypatch.delenv("SPACETRACK_PASSWORD", raising=False)
    monkeypatch.setattr(cli, "open_store", lambda target: store)
    assert cli.main(["run", REF, "--target", "local"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out.splitlines()[0]) == {"dataset": REF, "outcome": "needs-credentials",
                                              "missing": ["SPACETRACK_USERNAME", "SPACETRACK_PASSWORD"]}
    assert "RUN OK 1 datasets" in out and "needs credentials 1" in out


def test_end_to_end_publish(store, monkeypatch, credentials):
    server = FakeSpaceTrack()
    real_collect = spacetrack_gp.collect
    monkeypatch.setattr(spacetrack_gp, "collect", lambda c: real_collect(c, transport=httpx.MockTransport(server)))
    contract = relaxed(REF)
    result = run_dataset(contract, store, now=NOW)
    assert result.outcome == "published", result.report and result.report.errors
    assert [method for method, _ in server.calls] == ["POST", "GET", "GET"]
    doc = get_json(store, version_key(contract.site, contract.id, result.version))[0]
    assert {p for p in doc["files"] if p.startswith("table/") and p.endswith(".json")} == {
        "table/0.json", "table/25000.json", "table/100000.json", "table/all.json"}
    block = json.loads(store.get(blob_key(doc["files"]["table/25000.json"]["blob"]))[0])
    assert [r["norad_cat_id"] for r in block["rows"]] == [25544]
    assert block["meta"]["attribution"] == "Orbital data from Space-Track.org, provided by USSPACECOM (18th Space Defense Squadron)."


def test_validation_blocks_impossible_elements(store, monkeypatch, credentials):
    elements = json.loads(GP)
    elements[1]["ECCENTRICITY"] = "1.3000000"  # hyperbolic: not an Earth-orbiting element set
    server = FakeSpaceTrack(gp=json.dumps(elements).encode())
    real_collect = spacetrack_gp.collect
    monkeypatch.setattr(spacetrack_gp, "collect", lambda c: real_collect(c, transport=httpx.MockTransport(server)))
    result = run_dataset(relaxed(REF), store, now=NOW)
    assert result.outcome == "blocked" and any(e.startswith("eccentricity: 1 values outside") for e in result.report.errors)
