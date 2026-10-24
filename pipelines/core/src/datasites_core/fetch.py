"""HTTP fetching with conditional GETs, retries and a descriptive User-Agent."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Callable

import httpx

from . import __version__

RETRY_STATUSES = {429, 500, 502, 503, 504}


def user_agent() -> str:
    contact = os.environ.get("DATASITES_CONTACT", "hello@climatedata.dev")
    return f"datasites-pipelines/{__version__} (+mailto:{contact})"


@dataclass(frozen=True)
class Fetched:
    url: str
    status: int
    body: bytes | None  # None when the server answered 304 Not Modified
    etag: str | None
    last_modified: str | None

    @property
    def not_modified(self) -> bool:
        return self.status == 304


Fetcher = Callable[[str, "str | None", "str | None"], Fetched]


def fetch(url: str, etag: str | None = None, last_modified: str | None = None, *, retries: int = 3) -> Fetched:
    headers = {"User-Agent": user_agent()}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    delay = 2.0
    for attempt in range(retries + 1):
        try:
            res = httpx.get(url, headers=headers, timeout=60, follow_redirects=True)
        except httpx.TransportError:
            if attempt == retries:
                raise
        else:
            if res.status_code == 304:
                return Fetched(url, 304, None, etag, last_modified)
            if res.status_code not in RETRY_STATUSES or attempt == retries:
                res.raise_for_status()
                return Fetched(url, res.status_code, res.content, res.headers.get("etag"), res.headers.get("last-modified"))
            retry_after = res.headers.get("retry-after", "")
            if retry_after.isdigit():
                delay = max(delay, float(retry_after))
        time.sleep(delay)
        delay *= 3
    raise AssertionError("unreachable")


def http_date_to_iso(value: str | None) -> str | None:
    """'Fri, 09 Oct 2026 19:23:07 GMT' -> '2026-10-09T19:23:07Z'."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
