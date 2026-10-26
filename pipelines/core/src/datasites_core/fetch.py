"""HTTP fetching with conditional GETs, retries and a descriptive User-Agent."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import urlsplit

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


MAX_WAIT = 20 * 60  # seconds; a longer Retry-After is capped (the next answer brings a fresh one)
BACKOFF_START = 2.0
BACKOFF_MAX = 300.0
RETRIES = 5  # waits of 2, 6, 18, 54 and 162 s: a source that drops out for up to ~4 minutes doesn't fail the run
CONNECT_TIMEOUT = 15.0  # a host that doesn't answer fails fast, so retries aren't spent on one hung connect


def retry_after_seconds(value: str | None, now: datetime | None = None) -> float | None:
    """Retry-After as seconds to wait: delay-seconds ("120") or an HTTP date. None when absent or unreadable."""
    value = (value or "").strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - (now or datetime.now(timezone.utc))).total_seconds())


def request(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    retries: int = RETRIES,
    timeout: float = 120,
    max_wait: float = MAX_WAIT,
) -> httpx.Response:
    """GET with retries on transport errors, 429 and 5xx. Raises on other errors.

    A Retry-After answer waits exactly as long as asked (capped at max_wait) and does not feed the backoff,
    so a ten-minute rate limit stays ten minutes instead of growing into hours. Without one, waits back off
    from 2 seconds, tripling up to 5 minutes. `timeout` bounds reading a response; connecting gets
    CONNECT_TIMEOUT, so an unreachable host costs seconds per attempt, not minutes.
    """
    all_headers = {"User-Agent": user_agent(), **(headers or {})}
    backoff = BACKOFF_START
    for attempt in range(retries + 1):
        try:
            res = httpx.get(
                url,
                params=params,
                headers=all_headers,
                timeout=httpx.Timeout(timeout, connect=min(CONNECT_TIMEOUT, timeout)),
                follow_redirects=True,
            )
        except httpx.TransportError as err:
            if attempt == retries:
                raise
            wait, reason = backoff, type(err).__name__
        else:
            if res.status_code == 304:
                return res
            if res.status_code not in RETRY_STATUSES or attempt == retries:
                res.raise_for_status()
                return res
            asked = retry_after_seconds(res.headers.get("retry-after"))
            wait = backoff if asked is None else asked
            reason = f"HTTP {res.status_code}"
        wait = min(wait, max_wait)
        print(f"{urlsplit(url).netloc}: {reason}, retrying in {wait:.0f}s", file=sys.stderr, flush=True)
        time.sleep(wait)
        backoff = min(backoff * 3, BACKOFF_MAX)
    raise AssertionError("unreachable")


def fetch(url: str, etag: str | None = None, last_modified: str | None = None, *, retries: int = RETRIES) -> Fetched:
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    res = request(url, headers=headers, retries=retries)
    if res.status_code == 304:
        return Fetched(url, 304, None, etag, last_modified)
    return Fetched(url, res.status_code, res.content, res.headers.get("etag"), res.headers.get("last-modified"))


class CollectContext:
    """Handed to a pipeline's collect(ctx) / resources(ctx), and to build(raw, ctx) if it takes two arguments.

    ctx.read_blob(hash) reads an immutable blob this dataset published earlier (for archives that grow
    across versions, such as fire-perimeter history). It is read-only: pipelines never write to the store.
    """

    def __init__(self, contract: Any, now: Any, store: Any = None):
        self.contract = contract
        self.now = now
        self.requests = 0
        self._store = store

    def read_blob(self, sha256: str) -> bytes | None:
        if self._store is None:
            return None
        from .layout import blob_key

        found = self._store.get(blob_key(sha256))
        return found[0] if found else None

    def previous_files(self) -> dict[str, dict[str, Any]]:
        """{path: entry} of this dataset's current published version (shards included), or {} if none."""
        if self._store is None:
            return {}
        from .layout import blob_key, manifest_key, version_key
        from .store import get_json

        manifest, _ = get_json(self._store, manifest_key(self.contract.site, self.contract.id))
        if not manifest:
            return {}
        doc, _ = get_json(self._store, version_key(self.contract.site, self.contract.id, manifest["current"]))
        if not doc:
            return {}
        files = dict(doc["files"])
        for shard in (doc.get("file_shards") or {}).values():
            found = self._store.get(blob_key(shard))
            if found:
                import json

                files.update(json.loads(found[0]))
        return files

    def get(self, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: float = 120) -> bytes:
        self.requests += 1
        return request(url, params=params, headers=headers, timeout=timeout).content

    def get_json(self, url: str, **kwargs: Any) -> Any:
        import json

        return json.loads(self.get(url, **kwargs))

    def env(self, name: str) -> str:
        """A required secret or setting from the environment, with a clear error when it's missing."""
        value = os.environ.get(name)
        if not value:
            raise RuntimeError(f"{self.contract.ref} needs the environment variable {name} (see docs/SETUP.md)")
        return value


def http_date_to_iso(value: str | None) -> str | None:
    """'Fri, 09 Oct 2026 19:23:07 GMT' -> '2026-10-09T19:23:07Z'."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
