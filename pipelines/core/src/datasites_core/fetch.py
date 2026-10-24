"""HTTP fetching with conditional GETs, retries and a descriptive User-Agent."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Callable

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


def request(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    retries: int = 3,
    timeout: float = 120,
) -> httpx.Response:
    """GET with retries on transport errors, 429 and 5xx (honoring Retry-After). Raises on other errors."""
    all_headers = {"User-Agent": user_agent(), **(headers or {})}
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            res = httpx.get(url, params=params, headers=all_headers, timeout=timeout, follow_redirects=True)
        except httpx.TransportError:
            if attempt == retries:
                raise
        else:
            if res.status_code == 304:
                return res
            if res.status_code not in RETRY_STATUSES or attempt == retries:
                res.raise_for_status()
                return res
            retry_after = res.headers.get("retry-after", "")
            if retry_after.isdigit():
                delay = max(delay, float(retry_after))
        time.sleep(delay)
        delay *= 3
    raise AssertionError("unreachable")


def fetch(url: str, etag: str | None = None, last_modified: str | None = None, *, retries: int = 3) -> Fetched:
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
