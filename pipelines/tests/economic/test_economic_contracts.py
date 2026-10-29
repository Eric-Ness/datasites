import importlib
import json

from datasites_core import cli
from datasites_core.contracts import site_contracts

KEYLESS = {"laus", "qcew", "world-macro", "bea-county-income"}
KEYED = {"census-acs-income": "CENSUS_API_KEY"}


def test_every_contract_loads_and_its_module_has_a_transform():
    contracts = {c.id: c for c in site_contracts("economicdata")}
    assert set(contracts) == KEYLESS | set(KEYED)
    for c in contracts.values():
        module = importlib.import_module(c.pipeline)
        assert callable(module.transform)
        assert sum(hasattr(module, name) for name in ("RESOURCES", "resources", "collect")) == 1, c.id


def test_sources_and_credentials():
    contracts = {c.id: c for c in site_contracts("economicdata")}
    for dataset in KEYLESS:
        assert contracts[dataset].requires_env == ()
    for dataset, env in KEYED.items():
        assert contracts[dataset].requires_env == (env,)
        # Keyed sources must not be fetched with RESOURCES/resources(): their URL (with the key) would
        # be stored in the dataset's state file.
        assert hasattr(importlib.import_module(contracts[dataset].pipeline), "collect")
    for c in contracts.values():
        assert c.source.commercial_ok is True
    assert contracts["world-macro"].source.license == "CC-BY-4.0"
    assert contracts["world-macro"].source.attribution.startswith("The World Bank: World Development Indicators: ")
    assert "Not endorsed or certified by BEA" in contracts["bea-county-income"].source.attribution
    assert "not endorsed or certified by the Census Bureau" in contracts["census-acs-income"].source.attribution
    for dataset in ("laus", "qcew"):
        assert contracts[dataset].source.attribution.startswith("Source: U.S. Bureau of Labor Statistics")


def test_laus_description_flags_the_shutdown_gap():
    laus = next(c for c in site_contracts("economicdata") if c.id == "laus")
    assert "October 2025" in laus.description and "never interpolated" in laus.description


def test_keyed_datasets_report_needs_credentials(monkeypatch, capsys):
    for env in KEYED.values():
        monkeypatch.delenv(env, raising=False)
    for dataset, env in KEYED.items():
        assert cli.main(["run", f"economicdata/{dataset}", "--target", "local"]) == 0
        out = capsys.readouterr().out.splitlines()
        assert json.loads(out[0]) == {"dataset": f"economicdata/{dataset}", "outcome": "needs-credentials", "missing": [env]}
        assert out[1].startswith("RUN OK 1 datasets") and out[1].endswith("needs credentials 1)")
