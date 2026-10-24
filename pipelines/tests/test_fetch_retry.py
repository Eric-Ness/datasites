"""The engine's retry policy: Retry-After is honored exactly and never compounds into the backoff."""

from datetime import datetime, timezone

import httpx
import pytest

from datasites_core import fetch


def _answers(monkeypatch, responses):
    """Make httpx.get answer with these (status, headers) in turn; returns the recorded waits."""
    waits, queue = [], list(responses)
    monkeypatch.setattr(fetch.time, "sleep", waits.append)

    def fake_get(url, **_kwargs):
        status, extra = queue.pop(0)
        return httpx.Response(status, headers=extra, content=b"ok", request=httpx.Request("GET", url))

    monkeypatch.setattr(fetch.httpx, "get", fake_get)
    return waits


def test_retry_after_is_waited_exactly_and_does_not_escalate(monkeypatch):
    waits = _answers(monkeypatch, [(429, {"Retry-After": "806"}), (429, {"Retry-After": "40"}), (200, {})])
    assert fetch.request("https://api.example.test/x").content == b"ok"
    assert waits == [806, 40]  # was 806 then 2,418: the old loop tripled after every Retry-After


def test_backoff_without_retry_after_triples_and_is_capped(monkeypatch):
    waits = _answers(monkeypatch, [(503, {})] * 6 + [(200, {})])
    fetch.request("https://api.example.test/x", retries=6)
    assert waits == [2.0, 6.0, 18.0, 54.0, 162.0, fetch.BACKOFF_MAX]


def test_a_retry_after_between_server_errors_leaves_the_backoff_alone(monkeypatch):
    waits = _answers(monkeypatch, [(503, {}), (429, {"Retry-After": "30"}), (503, {}), (200, {})])
    fetch.request("https://api.example.test/x")
    assert waits == [2.0, 30, 18.0]


def test_a_long_retry_after_is_capped(monkeypatch):
    waits = _answers(monkeypatch, [(429, {"Retry-After": "7200"}), (200, {})])
    fetch.request("https://api.example.test/x")
    assert waits == [fetch.MAX_WAIT]


def test_gives_up_after_the_last_retry_and_never_retries_client_errors(monkeypatch):
    waits = _answers(monkeypatch, [(429, {"Retry-After": "1"})] * 4)
    with pytest.raises(httpx.HTTPStatusError):
        fetch.request("https://api.example.test/x", retries=3)
    assert len(waits) == 3
    waits = _answers(monkeypatch, [(404, {})])
    with pytest.raises(httpx.HTTPStatusError):
        fetch.request("https://api.example.test/x")
    assert waits == []


def test_retry_after_accepts_seconds_and_http_dates():
    now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
    assert fetch.retry_after_seconds("120", now) == 120
    assert fetch.retry_after_seconds("Sat, 24 Oct 2026 12:02:30 GMT", now) == 150
    assert fetch.retry_after_seconds("Sat, 24 Oct 2026 11:00:00 GMT", now) == 0  # already past
    assert fetch.retry_after_seconds("soon", now) is None
    assert fetch.retry_after_seconds(None, now) is None
