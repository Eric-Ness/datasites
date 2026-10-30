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


@pytest.mark.parametrize(
    "every, slack",
    [
        (timedelta(hours=3), timedelta(hours=1)),  # 10% would be 18 minutes; at least an hour
        (timedelta(days=1), timedelta(hours=2.4)),  # 10% is already more than an hour
        (timedelta(hours=1), timedelta(minutes=30)),  # never more than half the interval
    ],
)
def test_due_slack(every, slack):
    assert cli.due_slack(every) == slack


def test_a_late_scheduled_run_does_not_skip_a_three_hourly_dataset(contract, store):
    """GitHub started two runs of ingest-wildfire 2h42m apart on 2026-10-26; the second skipped three datasets."""
    from dataclasses import replace

    three_hourly = replace(contract, check_every="PT3H")
    # each dataset is checked a minute or so into its run, so the real gap was a little under 2h42m
    put_json(store, state_key(contract.site, contract.id), {"checked_at": "2026-10-26T07:12:40Z"})
    assert not cli.not_due(three_hourly, store, datetime(2026, 10, 26, 9, 53, 10, tzinfo=timezone.utc))  # 2h40m30s: due
    assert cli.not_due(three_hourly, store, datetime(2026, 10, 26, 8, 40, tzinfo=timezone.utc))  # 1h29m later: not yet


def _failing_run(monkeypatch, contract, store, err):
    """cmd_run over one dataset whose run raises err, counting misses the way publish.run_dataset does."""
    from datasites_core.publish import load_state, save_state

    def run_dataset(c, s, **_):
        state = load_state(s, c)
        state["consecutive_failures"] += 1
        save_state(s, c, state)
        raise err

    monkeypatch.setattr(cli, "_refs", lambda target: [contract.ref])
    monkeypatch.setattr(cli, "load_contract", lambda ref: contract)
    monkeypatch.setattr(cli, "open_store", lambda target: store)
    monkeypatch.setattr(cli, "run_dataset", run_dataset)


def _connect_error():
    import httpx

    return httpx.ConnectError("[Errno 101] Network is unreachable", request=httpx.Request("GET", "https://firms.example/"))


def test_one_unreachable_source_does_not_alert(monkeypatch, contract, store, capsys):
    """FIRMS was unreachable from GitHub's runner for one run on 2026-10-30; the next run was fine."""
    _failing_run(monkeypatch, contract, store, _connect_error())
    assert cli.main(["run", contract.ref, "--alert-after", "2"]) == 0
    out = capsys.readouterr().out
    assert '"outcome": "upstream-down"' in out and "upstream down 1" in out
    assert cli.main(["run", contract.ref, "--alert-after", "2"]) == 1  # the second miss in a row alerts
    assert '"outcome": "error"' in capsys.readouterr().err


def test_without_alert_after_every_miss_alerts(monkeypatch, contract, store):
    _failing_run(monkeypatch, contract, store, _connect_error())
    assert cli.main(["run", contract.ref]) == 1


def test_a_wrapped_outage_counts_but_other_errors_alert_at_once(monkeypatch, contract, store):
    import httpx

    try:
        try:
            raise _connect_error()
        except httpx.ConnectError as inner:
            raise RuntimeError("FIRMS area CSV") from inner
    except RuntimeError as wrapped:
        assert cli.upstream_down(wrapped)
    request = httpx.Request("GET", "https://x/")
    assert cli.upstream_down(httpx.HTTPStatusError("503", request=request, response=httpx.Response(503, request=request)))
    assert not cli.upstream_down(httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request)))
    _failing_run(monkeypatch, contract, store, ValueError("bad column"))
    assert cli.main(["run", contract.ref, "--alert-after", "2"]) == 1
