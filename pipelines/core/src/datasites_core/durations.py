"""ISO 8601 durations from contracts (P1D, PT3H, P1M, P45D). Months and years are approximated as
30 and 365 days, matching apps/api/src/duration.ts, which is fine for freshness and scheduling."""

from __future__ import annotations

import re
from datetime import timedelta

PATTERN = re.compile(r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def parse(iso: str) -> timedelta:
    m = PATTERN.match(iso)
    if not m or iso in ("P", "PT") or iso.endswith("T"):
        raise ValueError(f"invalid ISO 8601 duration: {iso!r}")
    y, mo, w, d, h, mi, s = (int(part or 0) for part in m.groups())
    return timedelta(days=y * 365 + mo * 30 + w * 7 + d, hours=h, minutes=mi, seconds=s)
