"""The wildfiredata contracts: modules, schedules and licensing."""

import importlib
from pathlib import Path

import yaml

from datasites_core import durations
from datasites_core.contracts import repo_root, site_contracts

EXPECTED = {"active-fires": "table", "fire-incidents": "table", "fire-perimeters": "files", "smoke": "files"}


def contracts():
    return {c.id: c for c in site_contracts("wildfiredata")}


def test_every_contract_names_a_working_module():
    found = contracts()
    assert {k: c.kind for k, c in found.items()} == EXPECTED
    for c in found.values():
        module = importlib.import_module(c.pipeline)
        assert sum(hasattr(module, n) for n in ("RESOURCES", "resources", "collect")) == 1, c.id
        assert callable(getattr(module, "build" if c.kind == "files" else "transform"))
        assert c.requires_env == ()  # every source is keyless; FIRMS's MAP_KEY API is not used


def test_hot_datasets_are_checked_every_3_hours_at_most():
    for c in contracts().values():
        assert durations.parse(c.check_every) >= durations.parse("PT3H"), c.id
    assert all(c.check_every == "PT3H" for c in contracts().values())


def test_licensing():
    found = contracts()
    for c in found.values():
        assert c.source.commercial_ok is True
    firms = found["active-fires"].source
    assert firms.attribution.startswith(
        "We acknowledge the use of imagery from the NASA LANCE FIRMS (https://earthdata.nasa.gov/firms), "
        "part of the NASA Earth Science Data and Information System (ESDIS)."
    )
    for doi in ("10.5067/FIRMS/VIIRS/VNP14IMGT_NRT.002", "10.5067/FIRMS/VIIRS/VJ114IMGT_NRT.002", "10.5067/FIRMS/VIIRS/VJ214IMGT_NRT.002"):
        assert doi in firms.attribution
    assert "MODIS" not in firms.name  # VIIRS only: MODIS ends in 2027
    assert found["smoke"].source.license == "CC0-1.0"
    for dataset in ("fire-incidents", "fire-perimeters"):
        assert found[dataset].source.license == "LicenseRef-US-Government-Work"
        assert "Wildland Fire Interagency Geospatial Services (WFIGS)" in found[dataset].source.attribution


def test_no_count_drop_checks_on_volatile_datasets():
    """Fires and incidents rightly fall by large fractions from one run to the next."""
    for c in contracts().values():
        assert c.checks.rows_min_ratio is None, c.id


def test_workflow_runs_every_3_hours_at_an_odd_minute():
    text = (repo_root() / ".github" / "workflows" / "ingest-wildfire.yml").read_text()
    wf = yaml.safe_load(text)
    triggers = wf.get("on", wf.get(True))  # YAML 1.1 reads a bare `on:` key as True
    cron = triggers["schedule"][0]["cron"]
    minute, hour, *_ = cron.split()
    assert hour == "*/3" and minute.isdigit() and int(minute) % 2 == 1
    assert "datasites run" in text and "wildfiredata" in text and "HC_INGEST_WILDFIRE" in text
    assert "vars.INGEST_ENABLED == 'true'" in text
    assert Path(repo_root() / "scripts" / "verify" / "wildfire.mjs").exists()
