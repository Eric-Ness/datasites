"""Polygon geometry for the boundary layers: shapefile rings to GeoJSON, RFC 7946 orientation, rounding.

Everything here is plain Python over lists of (lon, lat) tuples, so the output depends only on the input
bytes. The rules:
  - coordinates are rounded to `DECIMALS` places (5 = about 1.1 m at the equator), -0.0 becomes 0;
  - repeated consecutive positions are removed after rounding, every ring is closed, and rings that
    collapse below four positions or to zero area are dropped;
  - exterior rings are counterclockwise and holes clockwise (RFC 7946 section 3.1.6, the right-hand rule);
  - no edge may jump more than 180 degrees of longitude (a feature crossing the antimeridian must be split
    there, as both Census and Natural Earth already do), except the edge along a pole that closes a
    pole-wrapping ring such as Natural Earth's Antarctica.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

DECIMALS = 5

Point = tuple[float, float]
Ring = list[Point]


class GeometryError(ValueError):
    pass


def rnd(value: float) -> float:
    out = round(float(value), DECIMALS)
    return 0.0 if out == 0 else out


def signed_area(ring: Sequence[Sequence[float]]) -> float:
    """Shoelace area in square degrees; positive when the ring runs counterclockwise (lon east, lat north)."""
    total = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[i + 1][0], ring[i + 1][1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def clean_ring(points: Iterable[Sequence[float]]) -> Ring | None:
    """Rounds, removes repeated positions, closes. None if the ring collapses (fewer than 4 positions or no area)."""
    out: Ring = []
    for p in points:
        q = (rnd(p[0]), rnd(p[1]))
        if not out or q != out[-1]:
            out.append(q)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    if len(set(out)) < 3:
        return None
    out.append(out[0])
    if len(out) < 4 or signed_area(out) == 0:
        return None
    return out


def orient(ring: Ring, exterior: bool) -> Ring:
    ccw = signed_area(ring) > 0
    return ring if ccw == exterior else ring[::-1]


def _bbox(ring: Ring) -> tuple[float, float, float, float]:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def point_in_ring(x: float, y: float, ring: Ring) -> bool:
    """Even-odd ray casting. Points exactly on the boundary can go either way; callers vote over many vertices."""
    inside = False
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def check_antimeridian(ring: Ring, where: str) -> None:
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if abs(x2 - x1) > 180 and not (y1 == y2 and abs(y1) == 90):
            raise GeometryError(f"{where}: edge ({x1}, {y1}) -> ({x2}, {y2}) jumps across the antimeridian")


def polygons_from_shapefile_rings(rings: list[list[Sequence[float]]], where: str) -> list[list[Ring]]:
    """Groups a shapefile polygon record's rings into polygons.

    The shapefile convention is clockwise exteriors and counterclockwise holes. Each hole goes to the
    exterior that contains most of its vertices (ties: the smaller exterior); a "hole" no exterior
    contains is kept as an exterior of its own, which is how GDAL and pyshp read such files.
    """
    exteriors: list[Ring] = []
    holes: list[Ring] = []
    for raw in rings:
        ring = clean_ring(raw)
        if ring is None:
            continue
        check_antimeridian(ring, where)
        (exteriors if signed_area(ring) < 0 else holes).append(ring)
    boxes = [_bbox(r) for r in exteriors]
    areas = [abs(signed_area(r)) for r in exteriors]
    assigned: list[list[Ring]] = [[] for _ in exteriors]
    for hole in holes:
        hx0, hy0, hx1, hy1 = _bbox(hole)
        best, best_key = None, None
        for i, (x0, y0, x1, y1) in enumerate(boxes):
            if hx0 < x0 or hy0 < y0 or hx1 > x1 or hy1 > y1:
                continue
            votes = sum(point_in_ring(x, y, exteriors[i]) for x, y in hole[:-1])
            if votes == 0:
                continue
            key = (-votes, areas[i])
            if best_key is None or key < best_key:
                best, best_key = i, key
        if best is None:
            exteriors.append(hole)
            boxes.append(_bbox(hole))
            areas.append(abs(signed_area(hole)))
            assigned.append([])
        else:
            assigned[best].append(hole)
    return [[orient(ext, True), *(orient(h, False) for h in hs)] for ext, hs in zip(exteriors, assigned)]


def polygons_from_geojson(geometry: dict[str, Any], where: str) -> list[list[Ring]]:
    """GeoJSON Polygon or MultiPolygon (first ring exterior, the rest holes) to cleaned, oriented polygons."""
    kind = geometry.get("type")
    if kind == "Polygon":
        source = [geometry["coordinates"]]
    elif kind == "MultiPolygon":
        source = geometry["coordinates"]
    else:
        raise GeometryError(f"{where}: expected Polygon or MultiPolygon, got {kind}")
    polygons: list[list[Ring]] = []
    for poly in source:
        if not poly:
            continue
        exterior = clean_ring(poly[0])
        if exterior is None:
            continue  # the holes of a collapsed exterior go with it
        check_antimeridian(exterior, where)
        rings = [orient(exterior, True)]
        for raw in poly[1:]:
            hole = clean_ring(raw)
            if hole is not None:
                check_antimeridian(hole, where)
                rings.append(orient(hole, False))
        polygons.append(rings)
    return polygons


def geometry_json(polygons: list[list[Ring]]) -> str:
    """Compact GeoJSON geometry text: a Polygon for one part, a MultiPolygon for several."""

    def num(v: float) -> str:
        text = repr(v)
        return text[:-2] if text.endswith(".0") else text

    def ring(r: Ring) -> str:
        return "[" + ",".join(f"[{num(x)},{num(y)}]" for x, y in r) + "]"

    def poly(p: list[Ring]) -> str:
        return "[" + ",".join(ring(r) for r in p) + "]"

    if len(polygons) == 1:
        return '{"type":"Polygon","coordinates":' + poly(polygons[0]) + "}"
    return '{"type":"MultiPolygon","coordinates":[' + ",".join(poly(p) for p in polygons) + "]}"


def bbox_of(polygons: list[list[Ring]]) -> tuple[float, float, float, float]:
    xs = [x for p in polygons for x, _ in p[0]]
    ys = [y for p in polygons for _, y in p[0]]
    return min(xs), min(ys), max(xs), max(ys)
