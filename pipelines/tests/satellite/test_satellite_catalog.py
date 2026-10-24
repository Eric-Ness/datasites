import json

import polars as pl
import pytest

from datasites_core.contracts import load_contract
from datasites_core.layout import blob_key, version_key
from datasites_core.publish import run_dataset
from datasites_core.store import get_json
from datasites_satellite import gcat_catalog
from datasites_satellite.gcat import URLS
from satellite_helpers import NOW, FakeGCAT, fixture, gcat_raw, relaxed, rows_by

REF = "satellitedata/satellite-catalog"


def catalog(raw=None) -> dict[str, dict]:
    return rows_by(gcat_catalog.transform(raw or gcat_raw(gcat_catalog)), "jcat")


def replaced(name: str, old: bytes, new: bytes) -> dict[str, bytes]:
    raw = gcat_raw(gcat_catalog)
    assert old in raw[name], f"{old!r} not in the {name} fixture"
    raw[name] = raw[name].replace(old, new)
    return raw


def test_columns_match_the_contract():
    assert gcat_catalog.transform(gcat_raw(gcat_catalog)).columns == load_contract(REF).columns


def test_iss_record():
    assert catalog()["S25544"] == {
        "jcat": "S25544",
        "norad_cat_id": 25544,
        "satcat_note": None,
        "piece": "1998-067A",
        "launch_tag": "1998-067",
        "catalog": "satcat",
        "name": "Zarya",
        "original_name": "Zarya",
        "object_type": "payload",
        "gcat_type": "PPTH-MU",
        "identity_uncertain": False,
        "status_group": "in-orbit",
        "status_code": "O",  # satcat says GRP (grappled by Endeavour); the latest ecat phase says O
        "status": "In Earth orbit",
        "active": False,  # GCAT counts the Zarya module itself as an inactive payload
        "owner": "JSC",
        "owner_name": "NASA Johnson Space Flight Center",
        "state": "US",
        "state_name": "United States of America",
        "launch_date": "1998-11-20",
        "launch_date_text": "1998 Nov 20",
        "decay_date": None,
        "decay_date_text": None,
        "mass_kg": 20281.0,
        "mass_estimated": False,
        "orbit_source": "latest",
        "orbit_epoch": "2026-10-22",
        "orbit_epoch_text": "2026 Oct 22",
        "period_min": 92.963,
        "perigee_km": 414.0,
        "apogee_km": 424.0,
        "inclination_deg": 51.63,
        "orbit_estimated": False,
        "orbit_category": "LLEO/I",
        "orbit_class": "LEO",
    }


def test_six_digit_catalog_numbers():
    rows = catalog()
    assert rows["S100000"]["norad_cat_id"] == 100000 and rows["S100000"]["catalog"] == "satcat100k"
    assert rows["S100001"]["norad_cat_id"] == 100001 and rows["S100001"]["owner"] == "SPXS"
    assert rows["S270000"]["norad_cat_id"] == 270000 and rows["S270000"]["catalog"] == "satcat270k"
    table = gcat_catalog.transform(gcat_raw(gcat_catalog))
    assert table["norad_cat_id"].dtype == pl.Int64 and table["norad_cat_id"].max() == 270000


def test_numbers_gcat_cannot_match_are_null_not_guessed():
    rows = catalog()
    # S100129 holds SATCAT slot 100129, but GCAT does not know which Dongpo satellite it is.
    dongpo = rows["S100129"]
    assert (dongpo["norad_cat_id"], dongpo["satcat_note"], dongpo["identity_uncertain"]) == (None, "NNA", True)
    assert (rows["S40899"]["norad_cat_id"], rows["S40899"]["identity_uncertain"]) == (None, True)
    assert rows["A00001"]["satcat_note"] == "NNA" and rows["F00001"]["satcat_note"] == "NSO"


def test_multi_piece_objects_share_a_catalog_number_but_not_a_key():
    rows = catalog()
    pieces = {jcat: rows[jcat]["norad_cat_id"] for jcat in ("S00032", "A00046", "A00047")}
    assert pieces == {"S00032": 32, "A00046": 32, "A00047": 32}
    assert rows["S00032"]["status"] == "Reentered attached to A00046 Agena A 1055"
    assert [rows[j]["object_type"] for j in ("S00032", "A00046", "A00047")] == ["payload", "rocket-stage", "payload"]


def test_renamed_objects_carry_the_new_name_and_the_launch_name():
    greb = catalog()["S00046"]  # renamed in an ecat 'N' phase
    assert (greb["name"], greb["original_name"]) == ("Solar Radiation 1", "GREB")
    pioneer = catalog()["S00113"]  # renamed in the deep-space catalog; currentcat carries it
    assert (pioneer["name"], pioneer["original_name"]) == ("Pioneer 4", "Pioneer IV")


def test_lease_alias_phases_do_not_replace_the_owner():
    palapa = catalog()["S23779"]  # Palapa C1 -> HGS-3, leased as Anatolia 1 and Paksat 1 ('PA' alias phases)
    assert (palapa["name"], palapa["owner"], palapa["status_code"]) == ("HGS-3", "HGS", "O")
    assert palapa["original_name"] == "Palapa C1"


@pytest.mark.parametrize(
    "jcat, code, group",
    [
        ("S00005", "O", "in-orbit"),
        ("S25544", "O", "in-orbit"),
        ("T00001", "AO", "in-orbit"),  # attached to its dispenser in orbit
        ("S00052", "OX", "lost"),
        ("S00001", "R", "reentered"),
        ("S00432", "R?", "reentered"),  # unconfirmed reentry: code kept with its '?'
        ("A00047", "AR", "reentered"),
        ("S12399", "REFLT", "reentered"),  # STS-1 Columbia: landed, then flew again as S12953
        ("A00024", "S", "suborbital"),
        ("F00001", "S", "failed-to-orbit"),  # every ftocat object, whatever the code says about its fall
        ("S00919", "E", "broken-up"),
        ("S04494", "ERR", "spurious"),
        ("S00113", "DSO", "deep-space"),
        ("S00114", "DSO", "deep-space"),  # impacted the Moon
        ("S00674", "DSO", "in-orbit"),  # Vela 1A: left the Earth catalog's range, still in (very high) Earth orbit
        ("S04039", "DSA", "reentered"),  # Apollo 11 Columbia: went to the Moon, landed in the Pacific
    ],
)
def test_status_mapping(jcat, code, group):
    row = catalog()[jcat]
    assert (row["status_code"], row["status_group"]) == (code, group)


def test_objects_that_travelled_inside_or_attached_to_a_host_end_where_it_ends():
    rows = catalog()
    # film and gear that went to the Moon inside Apollo 8's capsule came home with it
    assert (rows["A01498"]["status"], rows["A01498"]["status_group"]) == ("Landed inside S03626    /D00217 Apollo 8", "reentered")
    assert rows["S03626"]["status_group"] == "reentered"
    # a chain: A01787 rode on A01786, which rode on Apollo 13's Aquarius, which reentered over the Pacific
    assert [rows[j]["status_group"] for j in ("A01777", "A01786", "A01787")] == ["reentered"] * 3
    assert rows["A01787"]["decay_date_text"] == rows["A01786"]["decay_date_text"]
    # a component still attached to Vela 1A, which is in (very high) Earth orbit
    assert (rows["A00401"]["status_code"], rows["A00401"]["status_group"]) == ("DSA", "in-orbit")


def test_every_documented_status_code_is_mapped():
    # https://planet4589.org/space/gcat/web/intro/phases.html, the status value table
    documented = ("O|AO|AO IN|UDK|REL|DEP|TO|TOA|OX|E|N|NA|LEASE|S|R|D|L|LF|AS|AR|AR IN|AL|AL IN|TX|F|AF|DSO|DSA|"
                  "DSA IN|LO|LOA|DK|GRP|ATT|TFR|TFR E|TFR IN|C|EVA DP|EVA RP|EO|EAO|EN|OI|OE|ERR").split("|")
    assert set(documented) - set(gcat_catalog.STATUS_GROUPS) == set()
    assert set(gcat_catalog.STATUS_GROUPS.values()) == {
        "in-orbit", "lost", "reentered", "suborbital", "failed-to-orbit", "broken-up", "spurious", "deep-space"}


def test_undocumented_status_code_stops_the_run():
    raw = replaced("satcat", b"\tR\t-\tOKB1\tSU\tOKB1\tBlok-A", b"\tQQ\t-\tOKB1\tSU\tOKB1\tBlok-A")
    with pytest.raises(ValueError, match="undocumented GCAT status code 'QQ'"):
        gcat_catalog.transform(raw)


def test_orbit_classes_come_from_gcat_categories():
    rows = catalog()
    assert {j: (rows[j]["orbit_category"], rows[j]["orbit_class"]) for j in ("S25544", "S00005", "S23779", "S00674", "S04039", "S00113", "F00001")} == {
        "S25544": ("LLEO/I", "LEO"),
        "S00005": ("MEO", "MEO"),
        "S23779": ("GEO/ID", "GEO"),  # inclined drift: an old geostationary satellite no longer station-kept
        "S00674": ("DSO", "VHEO"),  # GCAT's 'Deep Space Orbit' category is still Earth orbit, beyond 145,688 km
        "S04039": ("CLO", "cislunar"),
        "S00113": ("EEO", "escape"),
        "F00001": ("ATM", "suborbital"),
    }
    # https://planet4589.org/space/gcat/web/intro/orbits.html, the category table
    documented = ("ATM SO TA LLEO/E LLEO/I LLEO/P LLEO/S LLEO/R LEO/E LEO/I LEO/P LEO/S LEO/R MEO HEO HEO/M GTO "
                  "GEO/S GEO/I GEO/T GEO/D GEO/SI GEO/ID GEO/NS VHEO DSO CLO EEO HCO PCO SSE").split()
    assert set(documented) == set(gcat_catalog.ORBIT_CLASSES)
    assert {gcat_catalog.ORBIT_CLASSES[c] for c in documented if c.startswith(("LLEO", "LEO"))} == {"LEO"}
    assert {gcat_catalog.ORBIT_CLASSES[c] for c in documented if c.startswith("GEO")} == {"GEO"}


def test_undocumented_orbit_category_stops_the_run():
    raw = replaced("currentcat", b"\t 51.63 \t \tLLEO/I", b"\t 51.63 \t \tXEO/Q")
    with pytest.raises(ValueError, match="undocumented GCAT orbit categories"):
        gcat_catalog.transform(raw)


def test_orbits_latest_where_gcat_has_one_else_canonical_else_none():
    rows = catalog()
    assert rows["S00005"]["orbit_source"] == "latest" and rows["S00005"]["period_min"] == 132.587
    assert rows["S00001"]["orbit_source"] == "canonical" and rows["S00001"]["period_min"] is None
    assert (rows["S00001"]["perigee_km"], rows["S00001"]["apogee_km"]) == (214.0, 938.0)
    # tmpcat holds 0/0/0 placeholders with no epoch and no category until an orbit is known
    assert {k: rows["T00001"][k] for k in ("orbit_source", "perigee_km", "orbit_class")} == {"orbit_source": None, "perigee_km": None, "orbit_class": None}
    # escape trajectories: GCAT writes a negative (hyperbolic) apogee, or Inf
    assert rows["S00113"]["apogee_km"] == -393400.0


def test_vague_dates_are_kept_as_text_and_never_rounded():
    rows = catalog()
    assert (rows["S00001"]["decay_date"], rows["S00001"]["decay_date_text"]) == ("1957-12-01", "1957 Dec  1 1000?")
    assert (rows["S00002"]["decay_date"], rows["S00002"]["decay_date_text"]) == (None, "1958 Jan  4?")
    assert (rows["S40899"]["decay_date"], rows["S40899"]["decay_date_text"]) == (None, "2025 Mar 31?")
    assert (rows["S00682"]["decay_date"], rows["S00682"]["decay_date_text"]) == (None, "1965 May 23 0000?")
    assert (rows["S00432"]["decay_date"], rows["S00432"]["decay_date_text"]) == (None, "1965?")
    assert (rows["A00001"]["decay_date"], rows["A00001"]["decay_date_text"]) == (None, "1957 Nov?")


def test_decay_dates_only_for_objects_that_came_down():
    rows = catalog()
    assert rows["S25544"]["decay_date"] is None and rows["S00052"]["decay_date"] is None  # in orbit; lost
    assert rows["S00113"]["decay_date"] is None  # still flying, in solar orbit
    assert rows["S00114"]["decay_date"] == "1959-09-13"  # Luna 2 hit the Moon
    assert rows["S04039"]["decay_date"] == "1969-07-24"  # Apollo 11 splashdown, from currentcat
    assert rows["S12399"]["decay_date"] == "1981-04-14"  # STS-1 landing
    assert rows["F00001"]["decay_date"] == "1957-12-06"  # Vanguard TV-3 fell back at launch


def test_launch_dates_are_the_real_launch():
    deployed = catalog()["S45916"]  # released from the ISS: USSF gives it a 1998-067 piece designation
    assert (deployed["piece"], deployed["launch_tag"], deployed["launch_date"]) == ("1998-067RP", "2020-011", "2020-02-15")


def test_owner_names_and_joint_and_uncertain_owners():
    rows = catalog()
    assert rows["S00032"]["owner_name"] == "US Air Force Ballistic Missile Division / Central Intelligence Agency, USA"
    assert (rows["S00589"]["owner"], rows["S00589"]["owner_name"]) == ("AFSSD?", "US Air Force Space Systems Division")
    assert rows["S00001"]["state_name"] == "Union of Soviet Socialist Republics"


def test_deleted_entries_are_skipped():
    rows = catalog()
    assert "A00182" not in rows  # every field '-'
    assert len([j for j in rows if j.startswith("S27")]) == 1


def test_mass_zero_means_unknown():
    rows = catalog()
    assert rows["S270000"]["mass_kg"] is None and rows["S270000"]["mass_estimated"] is None  # debris: GCAT gives 0
    assert (rows["S100000"]["mass_kg"], rows["S100000"]["mass_estimated"]) == (15.0, True)


def test_truncated_record_is_rejected():
    raw = gcat_raw(gcat_catalog)
    raw["satcat100k"] = raw["satcat100k"].rsplit(b"\t", 3)[0]  # a download cut off mid-record
    with pytest.raises(ValueError, match="fields, header has"):
        gcat_catalog.transform(raw)


def test_object_in_two_catalogs_is_rejected():
    raw = gcat_raw(gcat_catalog)
    s100000 = next(line for line in raw["satcat100k"].split(b"\n") if line.startswith(b"S100000"))
    raw["satcat"] = raw["satcat"].rstrip(b"\n") + b"\n" + s100000 + b"\n"
    with pytest.raises(ValueError, match="more than one catalog"):
        gcat_catalog.transform(raw)


def test_normalize_drops_only_the_updated_line():
    body = fixture("gcat_satcat100k.tsv")
    normalized = gcat_catalog.normalize("satcat100k", body)
    assert b"# Updated" in body and b"# Updated" not in normalized
    assert normalized.split(b"\n")[0].startswith(b"#JCAT") and normalized.count(b"\n") == body.count(b"\n") - 1


def test_end_to_end_publish_and_unchanged_rerun(store):
    contract = relaxed(REF)
    upstream = FakeGCAT()
    first = run_dataset(contract, store, fetcher=upstream, now=NOW)
    assert first.outcome == "published", first.report and first.report.errors
    doc = get_json(store, version_key(contract.site, contract.id, first.version))[0]
    assert sorted(p for p in doc["files"] if p.startswith("table/")) == sorted(
        f"table/{g}.{ext}" for g in ("in-orbit", "lost", "reentered", "suborbital", "deep-space", "failed-to-orbit",
                                     "broken-up", "spurious", "all") for ext in ("csv", "json"))
    in_orbit = json.loads(store.get(blob_key(doc["files"]["table/in-orbit.json"]["blob"]))[0])
    assert in_orbit["meta"]["attribution"] == "data from GCAT (J. McDowell, planet4589.org/space/gcat)"
    assert "S25544" in {r["jcat"] for r in in_orbit["rows"]}
    assert doc["coverage"] == {"start": "1957-10-04", "end": "2026-10-01"}
    assert run_dataset(contract, store, fetcher=upstream, now=NOW).outcome == "unchanged"
    assert all(etag is not None for _, etag in upstream.requests[len(gcat_catalog.RESOURCES):])  # conditional GETs


def test_only_the_updated_line_changing_is_unchanged(store):
    contract = relaxed(REF)
    upstream = FakeGCAT()
    assert run_dataset(contract, store, fetcher=upstream, now=NOW).outcome == "published"
    url = URLS["satcat"]
    upstream.bodies[url] = upstream.bodies[url].replace(b"# Updated 2026 Oct 24 0114:13", b"# Updated 2026 Oct 25 0114:09")
    assert run_dataset(contract, store, fetcher=upstream, now=NOW).outcome == "unchanged"


def test_validation_blocks_a_catalog_that_lost_most_of_its_objects(store):
    contract = relaxed(REF, rows_min_ratio=0.99)
    upstream = FakeGCAT()
    good = run_dataset(contract, store, fetcher=upstream, now=NOW)
    assert good.outcome == "published"
    # A satcat regenerated with only its header and update line (an interrupted export upstream)
    url = URLS["satcat"]
    upstream.bodies[url] = b"\n".join(upstream.bodies[url].split(b"\n")[:2]) + b"\n"
    blocked = run_dataset(contract, store, fetcher=upstream, now=NOW)
    assert blocked.outcome == "blocked"
    assert any("row count fell" in e for e in blocked.report.errors)
    manifest = get_json(store, f"sites/{contract.site}/datasets/{contract.id}/manifest.json")[0]
    assert manifest["current"] == good.version  # the good version keeps serving


def test_validation_blocks_impossible_inclinations(store):
    contract = relaxed(REF)
    upstream = FakeGCAT()
    url = URLS["currentcat"]
    assert b"\t 51.63 \t \tLLEO/I" in upstream.bodies[url]
    upstream.bodies[url] = upstream.bodies[url].replace(b"\t 51.63 \t \tLLEO/I", b"\t251.63 \t \tLLEO/I")
    blocked = run_dataset(contract, store, fetcher=upstream, now=NOW)
    assert blocked.outcome == "blocked"
    assert any(e.startswith("inclination_deg: 1 values outside [0.0, 180.0]") for e in blocked.report.errors)
