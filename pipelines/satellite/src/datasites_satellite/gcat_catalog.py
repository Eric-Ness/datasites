"""satellitedata/satellite-catalog: one record per artificial space object, from GCAT.

The objects are those of GCAT's primary catalogs (satcat, satcat100k, satcat270k, auxcat, tmpcat and
ftocat), keyed by GCAT's JCAT id. Each catalog row is an object's first phase; ecat holds its later
phases (dockings, renamings, reentry after release, ...). The record combines:

- identity, launch date and mass from the first phase;
- status code, owner and state from the latest phase (first phase plus ecat, alias 'PA' phases skipped);
- name, status text, decay date, active flag and the latest orbit from currentcat, GCAT's own derived
  summary of each object's current state (it also follows objects that left for deep space);
- for objects currentcat does not cover (ftocat, a few others), the latest phase's own values.

Status groups map GCAT's documented phase status codes
(https://planet4589.org/space/gcat/web/intro/phases.html); an undocumented code stops the run. Objects
whose Earth-catalog history ends with a departure for deep space (DSO, DSA) are placed by currentcat's
status text, and by their host's fate when they travelled attached to or inside another object.
Orbit classes come only from GCAT's OpOrbit categories (https://planet4589.org/space/gcat/web/intro/orbits.html).
"""

from __future__ import annotations

import re

import polars as pl

from .gcat import URLS, lookup, missing, normalize, number, org_name, org_names, read_tsv, updated_at  # noqa: F401  (normalize is part of the module interface)
from .vague import iso_day

MAIN_CATALOGS = {  # catalog file -> the JCAT prefix its objects carry
    "satcat": "S",
    "satcat100k": "S",
    "satcat270k": "S",
    "auxcat": "A",
    "tmpcat": "T",
    "ftocat": "F",
}
RESOURCES = {name: URLS[name] for name in (*MAIN_CATALOGS, "ecat", "currentcat", "orgs")}

OBJECT_TYPES = {  # SatType byte 1 (https://planet4589.org/space/gcat/web/intro/type.html)
    "P": "payload",
    "C": "component",
    "R": "rocket-stage",
    "D": "debris",
    "S": "suborbital-payload",
    "Z": "spurious",
}

# Every status code GCAT documents, by what it means for where the object is now. The status describes
# the event that ends a phase; for the latest phase that is the object's current state.
STATUS_GROUPS = {
    # still in Earth orbit: free flying, attached to another object, or ending a phase in orbit
    "O": "in-orbit", "AO": "in-orbit", "AO IN": "in-orbit",
    "UDK": "in-orbit", "REL": "in-orbit", "N": "in-orbit", "NA": "in-orbit", "LEASE": "in-orbit",
    "DK": "in-orbit", "GRP": "in-orbit", "ATT": "in-orbit", "TFR": "in-orbit", "TFR E": "in-orbit",
    "TFR IN": "in-orbit", "EVA DP": "in-orbit", "EVA RP": "in-orbit",
    "OX": "lost",  # in orbit (probably) but no recent tracking data
    # back on Earth: natural decay, active deorbit, landing, on its own or attached to a parent
    "R": "reentered", "D": "reentered", "L": "reentered", "LF": "reentered",
    "AR": "reentered", "AR IN": "reentered", "AL": "reentered", "AL IN": "reentered",
    "S": "suborbital", "AS": "suborbital",  # targeted suborbital entry: never completed an orbit
    "F": "failed-to-orbit", "AF": "failed-to-orbit",
    "E": "broken-up", "C": "broken-up",  # exploded, or collided with no later phase
    "ERR": "spurious",  # no real object (tracking or cataloging error)
    # left Earth's sphere of influence, or events that only happen beyond it
    "DSO": "deep-space", "DSA": "deep-space", "DSA IN": "deep-space",
    "EO": "deep-space", "EAO": "deep-space", "EN": "deep-space", "OI": "deep-space", "OE": "deep-space",
    "LO": "deep-space", "LOA": "deep-space", "TO": "deep-space", "TOA": "deep-space", "DEP": "deep-space",
    "TX": "deep-space",
}
DEEP_SPACE_CODES = {"DSO", "DSA", "DSA IN"}
REFLOWN = "REFLT"  # zero-length phase: the object flew again under a new catalog number
BACK_ON_EARTH = re.compile(r"^((Reentered|Deorbited into) Earth atmosphere|(Landed|Crashed) on Earth)")
# 'Landed inside S03626    /D00217 Apollo 8', 'Reentered attached to A00046 Agena', 'Docked with S25544 Zarya'
HOSTED = re.compile(r"^(?P<verb>Landed|Reentered|Deorbited|Crashed|Attached|Docked)(?: attached to| inside| to| with) (?P<host>[A-Z]\d+)\b")

ORBIT_CLASSES = {  # GCAT OpOrbit category -> orbit class
    "LLEO/E": "LEO", "LLEO/I": "LEO", "LLEO/P": "LEO", "LLEO/S": "LEO", "LLEO/R": "LEO",
    "LEO/E": "LEO", "LEO/I": "LEO", "LEO/P": "LEO", "LEO/S": "LEO", "LEO/R": "LEO",
    "MEO": "MEO",
    "HEO": "HEO", "HEO/M": "HEO", "GTO": "HEO",
    "GEO/S": "GEO", "GEO/I": "GEO", "GEO/T": "GEO", "GEO/D": "GEO", "GEO/SI": "GEO", "GEO/ID": "GEO", "GEO/NS": "GEO",
    "VHEO": "VHEO", "DSO": "VHEO",
    "CLO": "cislunar",
    "EEO": "escape", "HCO": "escape", "PCO": "escape", "SSE": "escape",
    "ATM": "suborbital", "SO": "suborbital", "TA": "suborbital",
}

PHASE_FIELDS = ["Name", "Status", "Primary", "DDate", "Owner", "State",
                "ODate", "Perigee", "PF", "Apogee", "AF", "Inc", "IF", "OpOrbit"]
CURRENT_FIELDS = ["Active", "Name", "ExpandedStatus", "DDate", "ODate", "Period",
                  "Perigee", "PF", "Apogee", "AF", "Inc", "IF", "OpOrbit"]

COLUMNS = [
    "jcat", "norad_cat_id", "satcat_note", "piece", "launch_tag", "catalog", "name", "original_name",
    "object_type", "gcat_type", "identity_uncertain", "status_group", "status_code", "status", "active",
    "owner", "owner_name", "state", "state_name",
    "launch_date", "launch_date_text", "decay_date", "decay_date_text", "mass_kg", "mass_estimated",
    "orbit_source", "orbit_epoch", "orbit_epoch_text", "period_min", "perigee_km", "apogee_km",
    "inclination_deg", "orbit_estimated", "orbit_category", "orbit_class",
]


def _main(raw: dict[str, bytes]) -> pl.DataFrame:
    frames = []
    for name, prefix in MAIN_CATALOGS.items():
        frame = read_tsv(raw[name], name)
        frame = frame.filter(missing("Type").is_not_null())  # deleted entries: every field is '-'
        wrong = frame.filter(~pl.col("JCAT").str.starts_with(prefix))
        if len(wrong):
            raise ValueError(f"{name}: JCAT {wrong['JCAT'][0]!r} does not carry the {prefix} prefix of this catalog")
        frames.append(frame.with_columns(pl.lit(name).alias("catalog")))
    main = pl.concat(frames, how="vertical")
    dupes = main.filter(pl.col("JCAT").is_duplicated())
    if len(dupes):
        raise ValueError(f"JCAT {dupes['JCAT'][0]!r} appears in more than one catalog")
    return main


def _latest_phases(raw: dict[str, bytes]) -> pl.DataFrame:
    """Per JCAT, the last ecat phase that is not an alias ('PA') record, plus the code before it (for REFLT)."""
    ecat = read_tsv(raw["ecat"], "ecat").with_row_index("_order")
    ecat = ecat.filter(~pl.col("Type").str.starts_with("PA"))
    return ecat.group_by("JCAT").agg(
        *[pl.col(f).sort_by("_order").last().alias(f"e_{f}") for f in PHASE_FIELDS],
        pl.col("Status").sort_by("_order").alias("e_statuses"),
    )


def _status_group(catalog: str, coarse: str, code: str, previous: str | None, expanded: str | None) -> str:
    if coarse == "Z":
        return "spurious"
    if catalog == "ftocat":
        return "failed-to-orbit"
    base = code[:-1] if code.endswith("?") else code  # 'R?': reentry believed but not confirmed
    if base == REFLOWN:
        if previous is None:
            raise ValueError("REFLT status with no earlier phase")
        return _status_group(catalog, coarse, previous, None, expanded)
    if base not in STATUS_GROUPS:
        raise ValueError(f"undocumented GCAT status code {code!r}")
    group = STATUS_GROUPS[base]
    if base in DEEP_SPACE_CODES and expanded:
        # The object left Earth's catalog; currentcat follows it through the deep-space catalogs.
        if expanded.startswith("In Earth orbit"):
            return "in-orbit"
        if BACK_ON_EARTH.match(expanded):
            return "reentered"
    return group


def _follow_hosts(table: pl.DataFrame) -> pl.DataFrame:
    """Objects that went to deep space attached to or inside another object end where their host ends.

    currentcat says e.g. 'Landed inside S03626 /D00217 Apollo 8' for film canisters that came home in the
    Apollo 8 capsule, or 'Attached to A09093 /D00992 Falcon Heavy-001 Stage 2' for a payload still on its
    stage in solar orbit. The host's own group decides: back on Earth, still in Earth orbit, or deep space.
    """
    deep = table.filter(
        (pl.col("status_group") == "deep-space")
        & pl.col("l_Status").is_in(sorted(DEEP_SPACE_CODES))
        & pl.col("c_ExpandedStatus").is_not_null()
    )
    links = []
    for jcat, expanded in deep.select("JCAT", "c_ExpandedStatus").iter_rows():
        if m := HOSTED.match(expanded):
            links.append((jcat, m.group("verb"), m.group("host")))
    if not links:
        return table
    groups = dict(table.select("JCAT", "status_group").iter_rows())
    for _ in range(10):  # chains are short (a canister inside a capsule attached to a stage)
        changed = False
        for jcat, verb, host in links:
            host_group = groups.get(host)
            new = None
            if verb in ("Attached", "Docked") and host_group in ("in-orbit", "reentered"):
                new = host_group
            elif verb != "Attached" and verb != "Docked" and host_group == "reentered":
                new = "reentered"
            if new and groups[jcat] != new:
                groups[jcat], changed = new, True
        if not changed:
            break
    resolved = {jcat: groups[jcat] for jcat, _, _ in links}
    return table.with_columns(
        pl.col("JCAT").replace_strict(resolved, default=pl.col("status_group"), return_dtype=pl.Utf8).alias("status_group")
    )


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    main = _main(raw)
    current = read_tsv(raw["currentcat"], "currentcat").select(
        "JCAT", *[pl.col(f).alias(f"c_{f}") for f in CURRENT_FIELDS]
    ).with_columns(pl.lit(True).alias("c_present"))
    table = main.join(_latest_phases(raw), on="JCAT", how="left").join(current, on="JCAT", how="left")
    table = table.with_columns(pl.col("c_present").fill_null(False), pl.col("e_Status").is_not_null().alias("e_present"))

    # The latest phase: ecat's last phase when there is one, else the catalog row itself.
    table = table.with_columns(
        [pl.when(pl.col("e_present")).then(pl.col(f"e_{f}")).otherwise(pl.col(f)).alias(f"l_{f}") for f in PHASE_FIELDS]
    )
    if table["l_Status"].is_in(["-", ""]).any() or table["l_Status"].null_count():
        bad = table.filter(pl.col("l_Status").is_in(["-", ""]) | pl.col("l_Status").is_null())["JCAT"][0]
        raise ValueError(f"{bad}: latest phase has no status code")

    # Status group, over the (few thousand) distinct combinations rather than row by row.
    table = table.with_columns(
        pl.col("Type").str.slice(0, 1).alias("_coarse"),
        pl.col("e_statuses").list.get(-2, null_on_oob=True).alias("_previous_e"),
    ).with_columns(
        pl.when(pl.col("_previous_e").is_not_null()).then(pl.col("_previous_e")).otherwise(pl.col("Status")).alias("_previous")
    )
    keys = ["catalog", "_coarse", "l_Status", "_previous", "c_ExpandedStatus"]
    combos = table.select(keys).unique()
    combos = combos.with_columns(
        pl.Series(
            "status_group",
            [_status_group(cat, coarse, code, prev, exp) for cat, coarse, code, prev, exp in combos.iter_rows()],
            dtype=pl.Utf8,
        )
    )
    table = table.join(combos, on=keys, how="left", nulls_equal=True)
    table = _follow_hosts(table)

    unknown_types = set(table["_coarse"].unique().to_list()) - set(OBJECT_TYPES)
    if unknown_types:
        raise ValueError(f"undocumented GCAT SatType byte 1 values: {sorted(unknown_types)}")

    # Orbit: currentcat's latest orbit when it has one, else the latest phase's canonical orbit.
    # An orbit counts only if GCAT dates or classifies it: tmpcat entries carry 0/0/0 placeholders with
    # no epoch and no category until their orbit is known.
    has_latest = pl.col("c_present") & (missing("c_OpOrbit").is_not_null() | missing("c_ODate").is_not_null())
    has_canonical = missing("l_OpOrbit").is_not_null() | missing("l_ODate").is_not_null()
    pick = lambda c, l: pl.when(has_latest).then(pl.col(c)).when(has_canonical).then(pl.col(l)).otherwise(None)  # noqa: E731
    table = table.with_columns(
        pl.when(has_latest).then(pl.lit("latest")).when(has_canonical).then(pl.lit("canonical")).otherwise(None).alias("orbit_source"),
        pick("c_ODate", "l_ODate").alias("_odate"),
        pl.when(has_latest).then(pl.col("c_Period")).otherwise(None).alias("_period"),
        pick("c_Perigee", "l_Perigee").alias("_perigee"),
        pick("c_Apogee", "l_Apogee").alias("_apogee"),
        pick("c_Inc", "l_Inc").alias("_inc"),
        pick("c_OpOrbit", "l_OpOrbit").alias("_oporbit"),
        pl.when(has_latest)
        .then(pl.concat_str([pl.col("c_PF"), pl.col("c_AF"), pl.col("c_IF")], ignore_nulls=True))
        .when(has_canonical)
        .then(pl.concat_str([pl.col("l_PF"), pl.col("l_AF"), pl.col("l_IF")], ignore_nulls=True))
        .otherwise(None)
        .alias("_oflags"),
    )
    categories = set(table.select(missing("_oporbit")).to_series().drop_nulls().unique().to_list())
    unknown_orbits = categories - set(ORBIT_CLASSES)
    if unknown_orbits:
        raise ValueError(f"undocumented GCAT orbit categories: {sorted(unknown_orbits)}")

    # Decay: the date the object came down or impacted, from currentcat when it covers the object.
    ended = pl.col("status_group").is_in(["reentered", "suborbital", "failed-to-orbit", "deep-space"])
    table = table.with_columns(
        pl.when(~ended)
        .then(None)
        .when(pl.col("c_present"))
        .then(missing("c_DDate"))
        .when(pl.col("status_group") == "deep-space")
        .then(None)  # the Earth catalog's end date for these is the departure into deep space, not an end of flight
        .otherwise(missing("l_DDate"))
        .alias("decay_date_text"),
    )

    names = org_names(raw["orgs"])
    day = lambda col: lookup(pl.col(col), iso_day)  # noqa: E731
    satcat = pl.col("Satcat")
    is_number = satcat.str.contains(r"^\d+$")
    mass = number("Mass")
    out = table.select(
        pl.col("JCAT").alias("jcat"),
        pl.when(is_number).then(satcat).otherwise(None).cast(pl.Int64).alias("norad_cat_id"),
        pl.when(is_number).then(None).otherwise(missing("Satcat")).alias("satcat_note"),
        missing("Piece").alias("piece"),
        missing("Launch_Tag").alias("launch_tag"),
        pl.col("catalog"),
        pl.coalesce(missing("c_Name"), missing("l_Name"), pl.col("Name")).alias("name"),
        pl.col("Name").alias("original_name"),
        pl.col("_coarse").replace_strict(OBJECT_TYPES, return_dtype=pl.Utf8).alias("object_type"),
        pl.col("Type").alias("gcat_type"),
        (pl.col("Type").str.slice(8, 1) == "?").fill_null(False).alias("identity_uncertain"),
        pl.col("status_group"),
        pl.col("l_Status").alias("status_code"),
        pl.when(pl.col("c_present")).then(missing("c_ExpandedStatus")).otherwise(None).alias("status"),
        pl.when(pl.col("c_present")).then(pl.col("c_Active") == "A").otherwise(None).alias("active"),
        missing("l_Owner").alias("owner"),
        missing("l_State").alias("state"),
        missing("LDate").alias("launch_date_text"),
        pl.col("decay_date_text"),
        pl.when(mass > 0).then(mass).otherwise(None).alias("mass_kg"),
        pl.when(mass > 0).then((pl.col("MassFlag") == "?").fill_null(False)).otherwise(None).alias("mass_estimated"),
        pl.col("orbit_source"),
        missing("_odate").alias("orbit_epoch_text"),
        number("_period").alias("period_min"),
        number("_perigee").alias("perigee_km"),
        number("_apogee").alias("apogee_km"),
        number("_inc").alias("inclination_deg"),
        pl.when(pl.col("orbit_source").is_not_null()).then(pl.col("_oflags").fill_null("").str.contains("?", literal=True)).otherwise(None).alias("orbit_estimated"),
        missing("_oporbit").alias("orbit_category"),
    )
    out = out.with_columns(
        day("launch_date_text").alias("launch_date"),
        day("decay_date_text").alias("decay_date"),
        day("orbit_epoch_text").alias("orbit_epoch"),
        pl.col("orbit_category").replace_strict(ORBIT_CLASSES, default=None, return_dtype=pl.Utf8).alias("orbit_class"),
        lookup(pl.col("owner"), lambda c: org_name(c, names)).alias("owner_name"),
        lookup(pl.col("state"), lambda c: org_name(c, names)).alias("state_name"),
    )
    stamps = [s for s in (updated_at(raw[name]) for name in (*MAIN_CATALOGS, "ecat", "currentcat")) if s]
    if stamps:  # a day after GCAT regenerated its files cannot have happened yet: keep only the text
        cutoff = max(stamps)
        out = out.with_columns(
            pl.when(pl.col(c) > cutoff).then(None).otherwise(pl.col(c)).alias(c)
            for c in ("launch_date", "decay_date", "orbit_epoch")
        )
    return out.select(COLUMNS)
