"""votingdata/roll-calls: House Clerk and Senate roll-call XML, member by member.

Fixtures (fixtures/), captured 2026-11-26:
- house_1990_roll001.xml, house_2025_roll362.xml: https://clerk.house.gov/evs/<year>/roll<NNN>.xml, verbatim;
- senate_101_1_00001.xml: https://www.senate.gov/legislative/LIS/roll_call_votes/vote1011/vote_101_1_00001.xml;
- senate_menu_101_1_excerpt.xml: vote_menu_101_1.xml with its first three listed votes;
- legislators_*_1989_1990.json: unitedstates/congress-legislators (CC0), only people serving in 1989-1990,
  only the fields the matcher reads (IDs, name, terms).
"""

from __future__ import annotations

import collections
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from datasites_core.contracts import load_contract
from datasites_core.validate import validate_files
from datasites_voting import roll_calls as rc

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture(scope="module")
def members():
    return rc.Members(fixture("legislators_current_1989_1990.json"), fixture("legislators_historical_1989_1990.json"))


def test_a_2025_house_vote(members):
    r = rc.parse_house(fixture("house_2025_roll362.xml"), members, "u")
    assert (r["congress"], r["session"], r["roll"]) == (119, 1, 362)
    assert all(v["member_id"] for v in r["votes"])  # the Clerk gives Bioguide IDs since 2003
    assert r["votes"][0] == {"member_id": "A000370", "name": "Adams", "party": "D", "state": "NC", "vote": r["votes"][0]["vote"], "position": r["votes"][0]["position"]}
    assert sum(rc.totals(r).values()) == len(r["votes"])


def test_1990_house_members_are_matched_by_name_state_and_term(members):
    r = rc.parse_house(fixture("house_1990_roll001.xml"), members, "u")
    assert (r["date"], r["question"], r["type"]) == ("1990-01-23", "Call of the House", "QUORUM")
    ids = [v["member_id"] for v in r["votes"] if v["member_id"]]
    assert len(ids) == len(set(ids))  # no member matched twice
    unmatched = [(v["name"], v["state"]) for v in r["votes"] if not v["member_id"]]
    assert unmatched == [("Long", "IN")]  # Jill Long: congress-legislators files her under another surname
    smiths = {v["name"]: v["member_id"] for v in r["votes"] if v["state"] == "OR" and v["name"].startswith("Smith")}
    assert smiths == {"Smith, Denny (OR)": "S000527", "Smith (OR)": "S000607"}  # by first name, then by elimination
    # A quorum call: everyone present answers "Present". The file's own party totals: 151 R + 225 D.
    assert rc.totals(r)["present"] == 151 + 225


def test_a_1989_senate_vote(members):
    r = rc.parse_senate(fixture("senate_101_1_00001.xml"), members, "u")
    assert (r["congress"], r["session"], r["roll"], r["date"]) == (101, 1, 1, "1989-01-25")
    assert r["result"] == "Nomination Confirmed (99-0)" and r["measure"] == "PN128"
    adams = next(v for v in r["votes"] if v["name"] == "Adams (D-WA)")
    assert adams["member_id"] == "A000031" and adams["position"] == "yea"  # LIS S180 -> Bioguide A000031 (Brock Adams)
    assert rc.totals(r)["yea"] == 99 and all(v["member_id"] for v in r["votes"])


@pytest.mark.parametrize(
    ("text", "parsed"),
    [("Smith (NJ)", ("Smith", None)), ("Smith, Denny (OR)", ("Smith", "Denny")), ("Brown (OH), Sherrod", ("Brown", "Sherrod")), ("Ackerman", ("Ackerman", None))],
)
def test_house_display_names(text, parsed):
    assert rc.house_name(text) == parsed


@pytest.mark.parametrize(("vote", "position"), [("Yea", "yea"), ("Aye", "yea"), ("No", "nay"), ("Nay", "nay"), ("Present", "present"), ("Not Voting", "not-voting"), ("Jeffries", "other")])
def test_positions(vote, position):
    assert rc.position(vote) == position


def test_congress_and_session_from_the_year():
    assert rc.congress_of(1990) == (101, 2) and rc.congress_of(2025) == (119, 1) and rc.congress_of(2026) == (119, 2)
    assert rc.house_url(119, 2, 7) == "https://clerk.house.gov/evs/2026/roll007.xml"


class FakeChambers:
    """ctx serving the fixtures: House 2026 has `house_2026` votes, 2025 has 3, earlier years none; the Senate
    has three 119-2 votes; everything else is the chambers' 'no such vote' answers."""

    now = datetime(2026, 11, 26, tzinfo=timezone.utc)

    def __init__(self, previous=None, house_2026=5):
        self.previous = previous or {}
        self.house_2026 = house_2026
        self.urls: list[str] = []

    def previous_files(self):
        return {path: {"blob": f"blob:{path}"} for path in self.previous}

    def read_blob(self, sha):
        return self.previous.get(sha.removeprefix("blob:"))

    def get(self, url, timeout=120):
        self.urls.append(url)
        if "congress-legislators" in url:
            return fixture("legislators_current_1989_1990.json" if "current" in url else "legislators_historical_1989_1990.json")
        if "clerk.house.gov" in url:
            year, roll = int(url.split("/evs/")[1][:4]), int(url.rsplit("roll", 1)[1][:-4])
            limit = {2026: self.house_2026, 2025: 3}.get(year, 0)
            if roll > limit:
                return b'<xml>Error sanitizing file "x". Please try again.</xml>'
            body = fixture("house_2025_roll362.xml").decode()
            congress, session = rc.congress_of(year)
            body = body.replace("<rollcall-num>362</rollcall-num>", f"<rollcall-num>{roll}</rollcall-num>")
            body = body.replace("<session>1st</session>", f"<session>{'1st' if session == 1 else '2nd'}</session>")
            return body.encode()
        if "/roll_call_lists/vote_menu_119_2" in url:
            return fixture("senate_menu_101_1_excerpt.xml").replace(b"<congress>101</congress>", b"<congress>119</congress>")
        if "/roll_call_lists/" in url:
            return b"<html>moved</html>"
        if "vote_119_2_" in url:
            n = int(url.rsplit("_", 1)[1][:-4])
            body = fixture("senate_101_1_00001.xml")
            for a, b in ((b"<congress>101</congress>", b"<congress>119</congress>"), (b"<session>1</session>", b"<session>2</session>"), (b"<vote_number>1</vote_number>", f"<vote_number>{n}</vote_number>".encode())):
                body = body.replace(a, b)
            return body
        raise httpx.HTTPStatusError("404", request=httpx.Request("GET", url), response=httpx.Response(404))


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(rc, "PAUSE_S", {"house": 0, "senate": 0})


def test_missing_votes_are_recognised_by_their_body():
    ctx = FakeChambers()
    assert rc._get(ctx, "https://clerk.house.gov/evs/2026/roll999.xml") is None
    assert rc._get(ctx, "https://clerk.house.gov/evs/2026/roll001.xml") is not None
    assert rc.house_rolls(ctx, 2026, 0) == 5 and rc.house_rolls(ctx, 2026, 5) == 5


def test_first_run_takes_the_newest_votes_first_and_counts_the_rest(monkeypatch):
    monkeypatch.setattr(rc, "PER_RUN", {"house": 3, "senate": 3})
    raw = rc.collect(FakeChambers())
    fetched = sorted(k for k in raw if k.startswith("vote/"))
    # The chambers take turns, newest first: the three Senate votes aren't starved by the House's eight.
    assert len(fetched) == 6
    assert json.loads(raw[rc.LISTING])["missing"] == (5 + 3 + 3) - 6
    assert fetched == sorted(["vote/house/119/2/5", "vote/house/119/2/4", "vote/house/119/2/3", "vote/senate/119/2/3", "vote/senate/119/2/2", "vote/senate/119/2/1"])


def test_the_archive_grows_and_old_votes_are_carried(monkeypatch):
    monkeypatch.setattr(rc, "PER_RUN", {"house": 100, "senate": 100})
    monkeypatch.setattr(rc, "REFRESH_RECENT", 2)
    ctx = FakeChambers()
    files, stats = rc.build(rc.collect(ctx), ctx)
    assert stats["votes"] == 11
    assert json.loads(files["index.json"])["history_complete"] is True
    # Next day: two new House votes. Only those and the refreshed recent ones are fetched; the rest is read back.
    previous = {p: b for p, b in files.items() if p.count("/") == 3}
    ctx2 = FakeChambers(previous=previous, house_2026=7)
    raw2 = rc.collect(ctx2)
    assert not any(u.endswith("roll001.xml") and "/2025/" in u for u in ctx2.urls)  # an old vote isn't refetched
    files2, stats2 = rc.build(raw2, ctx2)
    assert stats2["votes"] == 13
    index = json.loads(files2["house/119/index.json"])["votes"]
    assert [(v["session"], v["roll"]) for v in index] == [(1, 1), (1, 2), (1, 3)] + [(2, n) for n in range(1, 8)]
    rows = list(csv.DictReader(io.StringIO(files2["house/119/member-votes.csv"].decode())))
    assert len(rows) == 10 * len(json.loads(files2["house/119/2/0007.json"])["votes"])
    assert collections.Counter(r["position"] for r in rows)["yea"] > 0
    coverage = json.loads(files2["index.json"])["chambers"]
    assert coverage["house"]["119"]["votes"] == 10 and coverage["senate"]["119"]["votes"] == 3


def test_a_vote_file_that_is_another_vote_is_rejected(monkeypatch):
    monkeypatch.setattr(rc, "PER_RUN", {"house": 100, "senate": 100})
    ctx = FakeChambers()
    raw = rc.collect(ctx)
    raw["vote/house/119/2/4"] = raw["vote/house/119/2/5"]
    with pytest.raises(ValueError, match="the file is vote 119-2 #5"):
        rc.build(raw, ctx)


def test_a_throttled_chamber_is_left_alone_and_the_rest_is_kept(monkeypatch):
    monkeypatch.setattr(rc, "PER_RUN", {"house": 100, "senate": 100})

    class Throttled(FakeChambers):
        def get(self, url, timeout=120):
            if "vote_119_2_00002" in url:
                raise httpx.HTTPStatusError("403", request=httpx.Request("GET", url), response=httpx.Response(403))
            return super().get(url, timeout)

    ctx = Throttled()
    raw = rc.collect(ctx)
    senate = sorted(k for k in raw if k.startswith("vote/senate/"))
    assert senate == ["vote/senate/119/2/3"]  # vote 3 fetched, then 403 on vote 2: vote 1 isn't even asked for
    assert not any("vote_119_2_00001" in u for u in ctx.urls)
    assert len([k for k in raw if k.startswith("vote/house/")]) == 8
    assert json.loads(raw[rc.LISTING])["missing"] == 2


def test_output_passes_the_contract(monkeypatch):
    monkeypatch.setattr(rc, "PER_RUN", {"house": 100, "senate": 100})
    contract = load_contract("votingdata/roll-calls")
    ctx = FakeChambers()
    files, stats = rc.build(rc.collect(ctx), ctx)
    report = validate_files(contract, set(files), stats)
    assert report.status == "pass", report.as_dict()


def test_fetching_stops_at_the_time_budget_and_keeps_what_it_has(monkeypatch):
    monkeypatch.setattr(rc, "PER_RUN", {"house": 100, "senate": 100})
    monkeypatch.setattr(rc, "FETCH_BUDGET_S", 0)  # the deadline has passed before the first vote
    raw = rc.collect(FakeChambers())
    assert not any(k.startswith("vote/") for k in raw)
    assert json.loads(raw[rc.LISTING])["missing"] == 11  # counted, for the next run
    assert "legislators/current" in raw
