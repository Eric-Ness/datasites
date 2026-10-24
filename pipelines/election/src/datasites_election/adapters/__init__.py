"""State adapters for official results files. One module per state; ADAPTERS maps USPS code -> adapter."""

from __future__ import annotations

from .base import PARSED_COLUMNS, SourceInfo, StateAdapter, to_county_rows
from .md import Maryland
from .wa import Washington

ADAPTERS: dict[str, StateAdapter] = {adapter.state_po: adapter for adapter in (Washington(), Maryland())}

__all__ = ["ADAPTERS", "PARSED_COLUMNS", "SourceInfo", "StateAdapter", "to_county_rows"]
