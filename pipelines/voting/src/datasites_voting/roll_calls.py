"""roll-calls: every recorded vote of the US House (1990 on) and Senate (1989 on), member by member, from the
chambers' own public-domain records.

Sources:
- House: the Clerk's roll-call XML, https://clerk.house.gov/evs/<year>/roll<NNN>.xml, one file per vote,
  numbered from 1 each year. From 2003 each legislator carries its Bioguide ID (name-id); before that only a
  name, party and state.
- Senate: the Senate's LIS XML, https://www.senate.gov/legislative/LIS/roll_call_votes/vote<C><S>/
  vote_<C>_<S>_<NNNNN>.xml, one file per vote, listed by https://www.senate.gov/legislative/LIS/
  roll_call_lists/vote_menu_<C>_<S>.xml. Each senator carries an LIS member ID.
- Member IDs: unitedstates/congress-legislators (CC0), whose Bioguide IDs, LIS IDs and terms let every
  vote name a member. Senate votes map LIS IDs to Bioguide IDs exactly. House votes before 2003 are matched by
  surname, state and the dates of House terms; a member gets an ID only when exactly one fits, otherwise the
  ID is left empty and the name, party and state are kept as the Clerk printed them.

Published files (kind: files):
    index.json                                   what is covered, per chamber and Congress
    <chamber>/<congress>/index.json              every vote of that Congress: date, question, result, totals
    <chamber>/<congress>/<session>/<NNNN>.json   one vote, with every member's position
    <chamber>/<congress>/member-votes.csv        one row per member per vote, for bulk use

Every vote file is written once and kept. collect() lists the published version's files (ctx.previous_files)
and fetches only votes it doesn't have yet: the newest first, then older Congresses, the two chambers side by
side, each up to PER_RUN votes and all within FETCH_BUDGET_S, so the history since 1989 fills in over a few
weeks of daily runs and each run stays short; a chamber that answers 403 or 429 is left alone for the rest of
the run. The newest
REFRESH_RECENT votes of each chamber are fetched again every run, in case a record is corrected soon after.
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Any

import httpx

HOUSE = "https://clerk.house.gov/evs/{year}/roll{roll:03d}.xml"
SENATE = "https://www.senate.gov/legislative/LIS/roll_call_votes/vote{c}{s}/vote_{c}_{s}_{n:05d}.xml"
SENATE_MENU = "https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{c}_{s}.xml"
LEGISLATORS = {
    "current": "https://unitedstates.github.io/congress-legislators/legislators-current.json",
    "historical": "https://unitedstates.github.io/congress-legislators/legislators-historical.json",
}
FIRST_HOUSE_YEAR = 1990
FIRST_SENATE_CONGRESS = 101  # 1989-1990
# Votes fetched per run, and the pause between them. senate.gov answered 403 after about 2,000 quick requests
# on 2026-11-26 (it has no robots.txt to say what it allows), so the Senate gets a small, slow share: its
# ~10,000 votes since 1989 take about 25 daily runs. The House Clerk had no objection to a faster pace.
PER_RUN = {"house": 2600, "senate": 400}
PAUSE_S = {"house": 0.05, "senate": 1.5}
# Fetching stops after this long whatever PER_RUN says, so a slow day publishes fewer votes instead of running
# into the job's 45-minute limit and publishing none (the first production run, 2026-11-27, did that).
FETCH_BUDGET_S = 25 * 60
REFRESH_RECENT = 10
LISTING = "previous.json"
ABOUT = (
    "Every recorded vote of the US House (1990 on) and Senate (1989 on), member by member, from the House Clerk's "
    "and the Senate's own roll-call records (public domain). member_id is the Bioguide ID (bioguide.congress.gov): "
    "Senate votes and House votes from 2003 carry it; earlier House votes are matched to congress-legislators by "
    "surname, state and House term, and left empty when more than one member fits. position is yea, nay, present, "
    "not-voting, or other (for example a name, in a vote for Speaker); vote is the chamber's own wording."
)
POSITIONS = {
    "yea": "yea", "aye": "yea", "yes": "yea", "guilty": "yea",
    "nay": "nay", "no": "nay", "not guilty": "nay",
    "present": "present", "present, giving live pair": "present",
    "not voting": "not-voting",
}
CSV_COLUMNS = ["chamber", "congress", "session", "roll", "date", "member_id", "name", "party", "state", "vote", "position"]


def congress_of(year: int) -> tuple[int, int]:
    """Calendar year -> (Congress, regular session). The 1st session is the odd year."""
    return (year - 1789) // 2 + 1, 1 if year % 2 == 1 else 2


def vote_path(chamber: str, congress: int, session: int, roll: int) -> str:
    return f"{chamber}/{congress}/{session}/{roll:04d}.json"


def house_url(congress: int, session: int, roll: int) -> str:
    """The Clerk numbers votes by calendar year; a Congress's 1st session is its odd year."""
    return HOUSE.format(year=1789 + 2 * (congress - 1) + (session - 1), roll=roll)


# --- collecting -------------------------------------------------------------------------------------------


MARKERS = {"clerk.house.gov": b"<rollcall-vote", "www.senate.gov": b"<roll_call_vote"}


def _get(ctx: Any, url: str, marker: bytes | None = None) -> bytes | None:
    """A vote or menu file, or None if it doesn't exist. Neither chamber answers 404 for a vote it hasn't
    taken: the Clerk sends 200 with '<xml>Error sanitizing file ...</xml>', the Senate redirects to a web
    page. So a vote file exists only if its body is a vote record."""
    try:
        body = ctx.get(url, timeout=60)
    except httpx.HTTPStatusError as err:
        if err.response.status_code == 404:
            return None
        raise
    if marker is None:
        marker = next((m for host, m in MARKERS.items() if f"//{host}/" in url and "/roll_call_lists/" not in url), None)
    if marker is not None and marker not in body[:2000]:
        return None
    if "/roll_call_lists/" in url and b"<vote_summary" not in body[:2000]:
        return None
    return body


def house_rolls(ctx: Any, year: int, known: int) -> int:
    """How many roll calls the House has taken in a year: the highest N with roll N present, searched upward
    from the highest one already published (one request when nothing is new)."""
    if known and _get(ctx, HOUSE.format(year=year, roll=known + 1)) is None:
        return known
    low, high = max(known, 0), max(known, 0) + 64
    while _get(ctx, HOUSE.format(year=year, roll=high)) is not None:
        low, high = high, high * 2
        if high > 4000:
            raise RuntimeError(f"House {year}: more than 4,000 roll calls?")
    while high - low > 1:  # roll `low` exists (or is 0), roll `high` doesn't
        mid = (low + high) // 2
        if _get(ctx, HOUSE.format(year=year, roll=mid)) is None:
            high = mid
        else:
            low = mid
    return low


def senate_rolls(ctx: Any, congress: int, session: int) -> list[int]:
    menu = _get(ctx, SENATE_MENU.format(c=congress, s=session))
    if menu is None:
        return []
    root = ET.fromstring(menu)
    return sorted(int(v.findtext("vote_number")) for v in root.iter("vote"))


def _throttled(err: httpx.HTTPStatusError, chamber: str, stopped: set[str], done: int = 0) -> None:
    """A 403 or 429 means the chamber's server is throttling us: leave it alone for the rest of this run and
    keep what we have; the next run carries on. Anything else is a real error."""
    if err.response.status_code not in (403, 429):
        raise err
    print(f"{chamber}: HTTP {err.response.status_code} after {done} votes; the rest waits for the next run", flush=True)
    stopped.add(chamber)


def previous_listing(ctx: Any) -> dict[str, str]:
    return {path: entry["blob"] for path, entry in ctx.previous_files().items() if re.fullmatch(r"(house|senate)/\d+/\d/\d{4}\.json", path)}


Vote = tuple[str, int, int, int, str]  # (chamber, congress, session, roll, url)


def _house_votes(ctx: Any, have: dict[tuple[str, int, int], set[int]]) -> list[Vote]:
    """Every House vote that exists, newest first."""
    wanted: list[Vote] = []
    this_year = ctx.now.year
    for year in range(this_year, FIRST_HOUSE_YEAR - 1, -1):
        congress, session = congress_of(year)
        known = have.get(("house", congress, session), set())
        past_complete = year < this_year - 1 and known and len(known) == max(known)
        count = max(known) if past_complete else house_rolls(ctx, year, max(known, default=0))
        wanted += [("house", congress, session, n, HOUSE.format(year=year, roll=n)) for n in range(count, 0, -1)]
    return wanted


def _senate_votes(ctx: Any, have: dict[tuple[str, int, int], set[int]]) -> list[Vote]:
    """Every Senate vote that exists, newest first."""
    wanted: list[Vote] = []
    current_congress = congress_of(ctx.now.year)[0]
    for congress in range(current_congress, FIRST_SENATE_CONGRESS - 1, -1):
        for session in (2, 1):
            known = have.get(("senate", congress, session), set())
            if congress < current_congress - 1 and known and len(known) == max(known):
                numbers = sorted(known)  # a past Congress fetched in full: its votes don't change
            else:
                numbers = senate_rolls(ctx, congress, session)
                time.sleep(PAUSE_S["senate"])
            wanted += [("senate", congress, session, n, SENATE.format(c=congress, s=session, n=n)) for n in reversed(numbers)]
    return wanted


def _chamber(ctx: Any, chamber: str, have: dict[tuple[str, int, int], set[int]], deadline: float) -> tuple[dict[str, bytes], int]:
    """One chamber's new votes (and its REFRESH_RECENT newest, again), newest first, until PER_RUN votes, the
    deadline, or a 403/429. Returns (raw files, votes still to fetch)."""
    started = time.monotonic()
    try:
        wanted = (_house_votes if chamber == "house" else _senate_votes)(ctx, have)
    except httpx.HTTPStatusError as err:
        _throttled(err, chamber, set())
        return {}, 0
    todo = [w for i, w in enumerate(wanted) if i < REFRESH_RECENT or w[3] not in have.get(w[:3], set())]
    print(f"{chamber}: {len(wanted)} votes listed, {len(todo)} to fetch (found in {time.monotonic() - started:.0f}s)", flush=True)
    raw: dict[str, bytes] = {}
    for _, congress, session, roll, url in todo:
        if len(raw) >= PER_RUN[chamber] or time.monotonic() >= deadline:
            break
        try:
            body = _get(ctx, url)
        except httpx.HTTPStatusError as err:
            _throttled(err, chamber, set(), len(raw))
            break
        if body is None:
            raise RuntimeError(f"{chamber} vote {congress}-{session} #{roll} is listed but missing: {url}")
        raw[f"vote/{chamber}/{congress}/{session}/{roll}"] = body
        if len(raw) % 250 == 0:
            print(f"{chamber}: {len(raw)} fetched, {time.monotonic() - started:.0f}s", flush=True)
        time.sleep(PAUSE_S[chamber])
    print(f"{chamber}: {len(raw)} fetched in {time.monotonic() - started:.0f}s; {len(todo) - len(raw)} still to fetch", flush=True)
    return raw, len(todo) - len(raw)


def collect(ctx: Any) -> dict[str, bytes]:
    listing = previous_listing(ctx)
    have: dict[tuple[str, int, int], set[int]] = {}
    for path in listing:
        chamber, congress, session, roll = path.removesuffix(".json").split("/")
        have.setdefault((chamber, int(congress), int(session)), set()).add(int(roll))

    # The chambers are different servers, so they're fetched side by side, each at its own pace. Both stop at
    # the same deadline, which leaves the job time to build and publish what was fetched.
    deadline = time.monotonic() + FETCH_BUDGET_S
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda chamber: _chamber(ctx, chamber, have, deadline), ("house", "senate")))
    raw: dict[str, bytes] = {}
    for files, _ in results:
        raw.update(files)
    raw["legislators/current"] = ctx.get(LEGISLATORS["current"])
    raw["legislators/historical"] = ctx.get(LEGISLATORS["historical"])
    raw[LISTING] = json.dumps({"files": listing, "missing": sum(missing for _, missing in results)}, sort_keys=True).encode()
    return raw


def normalize(name: str, body: bytes) -> bytes:
    """Change detection: only new or corrected votes count; the legislators files and the listing ride along."""
    return b"" if name == LISTING or name.startswith("legislators/") else body


# --- members ------------------------------------------------------------------------------------------------


class Members:
    """Bioguide IDs from congress-legislators: by LIS ID (Senate), and by surname, state and House term."""

    def __init__(self, current: bytes, historical: bytes):
        self.by_lis: dict[str, str] = {}
        self.house_terms: dict[tuple[str, str], list[tuple[date, date, str, set[str]]]] = {}
        for person in json.loads(historical) + json.loads(current):
            ids = person.get("id", {})
            bioguide = ids.get("bioguide")
            if not bioguide:
                continue
            if ids.get("lis"):
                self.by_lis[ids["lis"]] = bioguide
            name = person.get("name", {})
            last = fold(name.get("last", ""))
            firsts = {fold(name.get(k, "")) for k in ("first", "nickname", "middle")} - {""}
            for term in person.get("terms", []):
                if term.get("type") == "rep":
                    key = (last, term["state"])
                    self.house_terms.setdefault(key, []).append((date.fromisoformat(term["start"]), date.fromisoformat(term["end"]), bioguide, firsts))

    def senator(self, lis_id: str) -> str | None:
        return self.by_lis.get(lis_id)

    def representatives(self, surname: str, state: str, on: date, first: str | None = None) -> set[str]:
        """Bioguide IDs of members with that surname in that state's House delegation that day; a first name,
        when the Clerk prints one to tell two members apart, must match too."""
        fits = [(b, f) for start, end, b, f in self.house_terms.get((fold(surname), state), []) if start <= on <= end]
        if first:
            fits = [(b, f) for b, f in fits if fold(first) in f]
        return {b for b, _ in fits}


def fold(name: str) -> str:
    """'Ros-Lehtinen' and 'ros lehtinen' alike: lower case, letters only."""
    import unicodedata

    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", plain.lower())


def house_name(text: str) -> tuple[str, str | None]:
    """The Clerk's display name -> (surname, first name if printed): 'Smith (NJ)' -> ('Smith', None),
    'Smith, Denny (OR)' -> ('Smith', 'Denny'), 'Brown (OH), Sherrod' -> ('Brown', 'Sherrod')."""
    plain = re.sub(r"\([^)]*\)", "", text)  # drop "(OR)"
    surname, _, first = plain.partition(",")
    return surname.strip(), (first.strip() or None)


# --- parsing one vote --------------------------------------------------------------------------------------


def position(vote: str) -> str:
    return POSITIONS.get(vote.strip().lower(), "other")


def _text(el: ET.Element | None, path: str) -> str | None:
    value = el.findtext(path) if el is not None else None
    value = value.strip() if value else None
    return value or None


def parse_house(body: bytes, members: Members, url: str) -> dict[str, Any]:
    root = ET.fromstring(body)
    meta = root.find("vote-metadata")
    if meta is None:
        raise ValueError(f"{url}: no vote-metadata")
    held = datetime.strptime(_text(meta, "action-date"), "%d-%b-%Y").date()
    session = {"1st": 1, "2nd": 2, "3rd": 3}[_text(meta, "session")]
    votes, candidates = [], []
    for rv in root.iter("recorded-vote"):
        leg = rv.find("legislator")
        if leg is None:
            raise ValueError(f"{url}: a recorded vote without a legislator")
        name = (leg.text or "").strip()
        state = leg.get("state") or ""
        surname, first = house_name(name)
        cast = (rv.findtext("vote") or "").strip()
        given = leg.get("name-id")
        candidates.append({given} if given else members.representatives(surname, state, held, first))
        votes.append({"member_id": given, "name": name, "party": leg.get("party"), "state": state, "vote": cast, "position": position(cast)})
    # Unique fits first; then, by elimination, a member whose other possible IDs are all taken by members of
    # the same vote ('Smith (OR)' once 'Smith, Denny (OR)' is matched).
    taken: set[str] = set()
    for _ in range(3):
        for v, fits in zip(votes, candidates):
            if v["member_id"] is None and len(fits - taken) == 1:
                v["member_id"] = next(iter(fits - taken))
            if v["member_id"]:
                taken.add(v["member_id"])
    return {
        "chamber": "house",
        "congress": int(_text(meta, "congress")),
        "session": session,
        "roll": int(_text(meta, "rollcall-num")),
        "date": held.isoformat(),
        "question": _text(meta, "vote-question"),
        "type": _text(meta, "vote-type"),
        "result": _text(meta, "vote-result"),
        "measure": _text(meta, "legis-num"),
        "description": _text(meta, "vote-desc"),
        "source_url": url,
        "votes": votes,
    }


def parse_senate(body: bytes, members: Members, url: str) -> dict[str, Any]:
    root = ET.fromstring(body)
    stamp = re.sub(r"\s+", " ", _text(root, "vote_date") or "")
    held = datetime.strptime(stamp.split(",")[0] + "," + stamp.split(",")[1], "%B %d, %Y").date()
    votes = []
    for m in root.iter("member"):
        lis = _text(m, "lis_member_id")
        cast = _text(m, "vote_cast") or ""
        votes.append({
            "member_id": members.senator(lis) if lis else None,
            "name": _text(m, "member_full"),
            "party": _text(m, "party"),
            "state": _text(m, "state"),
            "vote": cast,
            "position": position(cast),
        })
    doc = root.find("document")
    return {
        "chamber": "senate",
        "congress": int(_text(root, "congress")),
        "session": int(_text(root, "session")),
        "roll": int(_text(root, "vote_number")),
        "date": held.isoformat(),
        "question": _text(root, "vote_question_text") or _text(root, "question"),
        "type": _text(root, "question"),
        "result": _text(root, "vote_result_text") or _text(root, "vote_result"),
        "measure": _text(doc, "document_name"),
        "description": _text(root, "vote_title") or _text(doc, "document_title"),
        "majority_requirement": _text(root, "majority_requirement"),
        "source_url": url,
        "votes": votes,
    }


def totals(record: dict[str, Any]) -> dict[str, int]:
    out = {"yea": 0, "nay": 0, "present": 0, "not-voting": 0, "other": 0}
    for v in record["votes"]:
        out[v["position"]] += 1
    return out


# --- building ---------------------------------------------------------------------------------------------


def dumps(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _previous(raw: dict[str, bytes], ctx: Any) -> dict[str, dict[str, Any]]:
    listing = json.loads(raw[LISTING])["files"] if LISTING in raw else {}
    if listing and ctx is None:
        raise RuntimeError("roll-calls: the published votes can only be read inside a datasites run")
    with ThreadPoolExecutor(max_workers=16) as pool:
        bodies = dict(zip(listing, pool.map(ctx.read_blob, listing.values()))) if listing else {}
    missing = [p for p, b in bodies.items() if b is None]
    if missing:
        raise RuntimeError(f"roll-calls: {len(missing)} published vote files can't be read, e.g. {missing[0]}")
    return {path: json.loads(body) for path, body in bodies.items()}


def build(raw: dict[str, bytes], ctx: Any = None) -> tuple[dict[str, bytes], dict[str, Any]]:
    members = Members(raw["legislators/current"], raw["legislators/historical"])
    records = _previous(raw, ctx)
    for name in sorted(n for n in raw if n.startswith("vote/")):
        _, chamber, congress, session, roll = name.split("/")
        if chamber == "house":
            record = parse_house(raw[name], members, house_url(int(congress), int(session), int(roll)))
        else:
            record = parse_senate(raw[name], members, SENATE.format(c=congress, s=session, n=int(roll)))
        if (record["congress"], record["session"], record["roll"]) != (int(congress), int(session), int(roll)):
            raise ValueError(f"{name}: the file is vote {record['congress']}-{record['session']} #{record['roll']}")
        record["totals"] = totals(record)
        records[vote_path(chamber, int(congress), int(session), int(roll))] = record
    if not records:
        raise ValueError("roll-calls: no votes")

    files: dict[str, bytes] = {path: dumps(r) for path, r in records.items()}
    by_congress: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for path, r in records.items():
        by_congress.setdefault((r["chamber"], r["congress"]), []).append({**r, "path": path})
    coverage: dict[str, dict[str, Any]] = {"house": {}, "senate": {}}
    rows = 0
    for (chamber, congress), votes in sorted(by_congress.items()):
        votes.sort(key=lambda r: (r["session"], r["roll"]))
        files[f"{chamber}/{congress}/index.json"] = dumps({
            "chamber": chamber,
            "congress": congress,
            "votes": [{k: r.get(k) for k in ("session", "roll", "date", "question", "type", "result", "measure", "description", "totals", "path")} for r in votes],
        })
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        for r in votes:
            for v in r["votes"]:
                writer.writerow([chamber, congress, r["session"], r["roll"], r["date"], v["member_id"] or "", v["name"], v["party"], v["state"], v["vote"], v["position"]])
                rows += 1
        files[f"{chamber}/{congress}/member-votes.csv"] = buf.getvalue().encode()
        coverage[chamber][str(congress)] = {
            "votes": len(votes),
            "first": votes[0]["date"],
            "last": votes[-1]["date"],
            "members_without_id": sum(1 for r in votes for v in r["votes"] if not v["member_id"]),
        }
    missing = json.loads(raw[LISTING])["missing"] if LISTING in raw else 0
    files["index.json"] = dumps({"about": ABOUT, "chambers": coverage, "history_complete": missing == 0, "votes_still_to_fetch": missing})
    return files, {"rows": rows, "votes": len(records)}
