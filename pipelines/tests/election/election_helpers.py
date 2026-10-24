"""Shared helpers for the electiondata tests (pytest finds this module next to the tests; no conftest needed)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    """A fixture's content without its leading '#' header comment (which is ours, not the source's)."""
    lines = (FIXTURES / name).read_bytes().split(b"\n")
    while lines and lines[0].startswith(b"#"):
        lines.pop(0)
    return b"\n".join(lines)


def fixture_json(name: str) -> dict[str, Any]:
    return json.loads(fixture_bytes(name))


def crlf(body: bytes) -> bytes:
    """The live state files use CRLF line endings; the repository stores fixtures with LF."""
    return body.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def md_files(name: str, *, line_endings: str = "lf") -> dict[str, bytes]:
    """The '# file: XX_NNCountyResults.csv' sections of a Maryland fixture, keyed by county code NN."""
    files: dict[str, list[bytes]] = {}
    current = None
    for line in (FIXTURES / name).read_bytes().split(b"\n"):
        if line.startswith(b"# file: "):
            current = line.decode().split("_")[1][:2]
            files[current] = []
        elif line.startswith(b"#") or current is None:
            continue
        elif line:
            files[current].append(line)
    out = {nn: b"\n".join(lines) + b"\n" for nn, lines in files.items()}
    return {nn: crlf(body) for nn, body in out.items()} if line_endings == "crlf" else out


class FakeContext:
    """Stands in for datasites_core.fetch.CollectContext: serves fixture bodies by URL and records requests."""

    def __init__(self, contract: Any, bodies: dict[str, bytes]):
        self.contract = contract
        self.bodies = bodies
        self.requests: list[str] = []

    def get(self, url: str, *, params: dict[str, Any] | None = None, **_: Any) -> bytes:
        key = url if not params else f"{url}?{'&'.join(f'{k}={v}' for k, v in sorted(params.items()))}"
        self.requests.append(key)
        if key not in self.bodies:
            raise AssertionError(f"unexpected request {key}")
        return self.bodies[key]

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return json.loads(self.get(url, **kwargs))
