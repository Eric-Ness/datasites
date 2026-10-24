from datasites_carbon.factors import common
from datasites_core.contracts import site_contracts

CORNERSTONE = "EPA methodology, Cornerstone release (not EPA-official)"


def contracts():
    return {c.id: c for c in site_contracts("carbondata")}


def test_both_datasets_are_declared():
    found = contracts()
    assert set(found) == {"emission-factors", "national-fossil-co2"}
    assert found["emission-factors"].kind == "table" and found["national-fossil-co2"].kind == "series"
    assert {c.check_every for c in found.values()} == {"P7D"}


def test_licensing_is_explicit():
    factors = contracts()["emission-factors"].source
    assert factors.license == "CC-BY-4.0 AND OGL-UK-3.0" and factors.commercial_ok is True
    # each Cornerstone release (Hub, eGRID2024, USEEIO) carries the label, with its DOI
    assert factors.attribution.count(CORNERSTONE) == 3
    for doi in ("10.5281/zenodo.20403776", "10.5281/zenodo.18968658", "10.5281/zenodo.17202747"):
        assert doi in factors.attribution
    assert "Contains public sector information licensed under the Open Government Licence v3.0." in factors.attribution
    assert "Ember" in factors.attribution
    gcb = contracts()["national-fossil-co2"].source
    assert gcb.license == "CC-BY-4.0" and gcb.commercial_ok is True and "10.5281/zenodo.5569234" in gcb.attribution


def test_factor_table_schema():
    contract = contracts()["emission-factors"]
    assert contract.columns == common.COLUMNS
    assert contract.key == ("factor_id",) and contract.partition == "source"
    assert contract.time_column == "valid_year"
    assert [c.name for c in contract.schema if c.nullable] == ["source_ref", "notes"]
