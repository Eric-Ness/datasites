"""Helpers shared by the economicdata pipelines: geography lookups and key-safe API requests."""

from __future__ import annotations

from functools import cache
from typing import Any

import httpx

from datasites_core import geo


@cache
def state_ids_by_fips() -> dict[str, str]:
    """'06' -> 'us-ca' for every US state, DC and territory in the shared registry."""
    return {loc["fips"]: loc_id for loc_id, loc in geo.load().items() if loc["level"] == "us-state"}


def keyed_get(ctx: Any, url: str, params: dict[str, Any], what: str) -> bytes:
    """ctx.get for APIs that take a secret in the query string.

    httpx puts the full URL, query string included, into its error messages, and those messages end
    up in the run log and in the dataset's state file. This re-raises without the query string so a
    key can never leak that way.
    """
    try:
        return ctx.get(url, params=params)
    except httpx.HTTPStatusError as err:
        raise RuntimeError(f"{what}: HTTP {err.response.status_code} from {url}") from None
    except httpx.HTTPError as err:
        raise RuntimeError(f"{what}: {type(err).__name__} requesting {url}") from None
