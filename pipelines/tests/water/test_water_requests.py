"""The USGS request layer: URL-sized id batches, and retries that honor Retry-After exactly."""

import httpx
import pytest

from datasites_water import usgs
from water_helpers import FakeCtx


def test_id_batches_respect_the_url_budget():
    ids = ["USGS-01646500"] * 500 + ["USGS-385106106571000"] * 300
    batches = usgs.id_batches(ids)
    assert [site for batch in batches for site in batch] == ids
    assert all(len(",".join(batch)) <= usgs.ID_BATCH_CHARS for batch in batches)
    assert len(batches) == 3


def _answers(monkeypatch, responses):
    """Make usgs.request answer with these (status, headers) in turn; returns the recorded waits."""
    waits, queue = [], list(responses)
    monkeypatch.setattr(usgs.time, "sleep", waits.append)

    def fake_request(url, *, params=None, headers=None, retries=3, timeout=120):
        assert retries == 0  # the engine's own retry loop is bypassed
        status, extra = queue.pop(0)
        res = httpx.Response(status, headers=extra, content=b"x,y,id\n", request=httpx.Request("GET", url))
        res.raise_for_status()
        return res

    monkeypatch.setattr(usgs, "request", fake_request)
    return waits


def test_rate_limited_requests_wait_exactly_as_long_as_asked(monkeypatch):
    waits = _answers(monkeypatch, [(429, {"Retry-After": "806"}), (429, {"Retry-After": "40"}), (200, {})])
    ctx = FakeCtx()
    assert usgs.get_csv(ctx, "daily", {}) == b"x,y,id\n"
    assert waits == [806, 40]  # no tripling between rate-limited answers
    assert ctx.requests == 3


def test_server_errors_back_off_and_a_long_retry_after_is_capped(monkeypatch):
    waits = _answers(monkeypatch, [(503, {}), (502, {}), (429, {"Retry-After": "7200"}), (200, {})])
    usgs.get_csv(FakeCtx(), "daily", {})
    assert waits == [5.0, 15.0, usgs.MAX_WAIT]


def test_retries_give_up_and_client_errors_are_not_retried(monkeypatch):
    waits = _answers(monkeypatch, [(429, {"Retry-After": "1"})] * usgs.ATTEMPTS)
    with pytest.raises(httpx.HTTPStatusError):
        usgs.get_csv(FakeCtx(), "daily", {})
    assert len(waits) == usgs.ATTEMPTS - 1
    waits = _answers(monkeypatch, [(400, {})])
    with pytest.raises(httpx.HTTPStatusError):
        usgs.get_csv(FakeCtx(), "daily", {})
    assert waits == []
