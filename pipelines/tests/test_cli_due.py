from datetime import datetime, timedelta, timezone

import pytest

from datasites_core import cli, durations
from datasites_core.layout import state_key
from datasites_core.store import put_json


@pytest.mark.parametrize(
    "iso, expected",
    [("PT3H", timedelta(hours=3)), ("P1D", timedelta(days=1)), ("P1W", timedelta(days=7)), ("P1M", timedelta(days=30)), ("P1DT12H", timedelta(days=1, hours=12))],
)
def test_durations_parse(iso, expected):
    assert durations.parse(iso) == expected


@pytest.mark.parametrize("iso", ["", "P", "PT", "1D", "P1DT"])
def test_durations_reject_invalid(iso):
    with pytest.raises(ValueError):
        durations.parse(iso)


def test_requires_env_reports_needs_credentials(tmp_path, monkeypatch, capsys):
    root = tmp_path / "repo"
    datasets = root / "contracts" / "sites" / "testsite" / "datasets"
    datasets.mkdir(parents=True)
    (datasets / "keyed.yaml").write_text(
        "id: keyed\nversion: 1\ntitle: Keyed\ndescription: d\npipeline: datasites_climate.noaa_gml_co2\n"
        "check_every: P1D\ncadence: P1D\nsla: P2D\nrequires_env: [DATASITES_TEST_KEY]\n"
        "source: {id: s, name: s, url: 'https://x', license: CC0-1.0, commercial_ok: true, attribution: a}\n"
        "schema: [{name: date, type: month}, {name: value, type: float}]\n"
        "series: {t: T}\nlocations: {world: World}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DATASITES_ROOT", str(root))
    monkeypatch.delenv("DATASITES_TEST_KEY", raising=False)
    assert cli.main(["run", "testsite", "--target", "local"]) == 0
    out = capsys.readouterr().out
    assert '"outcome": "needs-credentials"' in out and "DATASITES_TEST_KEY" in out
    assert "needs credentials 1" in out


def test_not_due_uses_check_every_with_tolerance(contract, store):
    now = datetime(2026, 10, 24, 14, 23, tzinfo=timezone.utc)
    assert not cli.not_due(contract, store, now)  # never checked
    put_json(store, state_key(contract.site, contract.id), {"checked_at": "2026-10-24T08:00:00Z"})
    assert cli.not_due(contract, store, now)  # 6h ago, check_every is P1D
    put_json(store, state_key(contract.site, contract.id), {"checked_at": "2026-10-23T14:23:05Z"})
    assert not cli.not_due(contract, store, now)  # a day minus a few seconds is still due
