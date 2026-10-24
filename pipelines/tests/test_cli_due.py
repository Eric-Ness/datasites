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


def test_not_due_uses_check_every_with_tolerance(contract, store):
    now = datetime(2026, 10, 24, 14, 23, tzinfo=timezone.utc)
    assert not cli.not_due(contract, store, now)  # never checked
    put_json(store, state_key(contract.site, contract.id), {"checked_at": "2026-10-24T08:00:00Z"})
    assert cli.not_due(contract, store, now)  # 6h ago, check_every is P1D
    put_json(store, state_key(contract.site, contract.id), {"checked_at": "2026-10-23T14:23:05Z"})
    assert not cli.not_due(contract, store, now)  # a day minus a few seconds is still due
