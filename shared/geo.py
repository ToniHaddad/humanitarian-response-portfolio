"""Distances on the invented planar grid (kilometres). No real coordinates anywhere."""

from __future__ import annotations

import math
from collections.abc import Sequence

Point = tuple[float, float]


def distance_km(a: Point, b: Point) -> float:
    """Straight-line distance on the grid, multiplied by 1.3 as a simple road-winding factor."""
    return round(math.hypot(a[0] - b[0], a[1] - b[1]) * 1.3, 2)


def route_km(points: Sequence[Point], return_to_start: bool = True) -> float:
    """Total length of a route visiting points in order (optionally back to the first point)."""
    if len(points) < 2:
        return 0.0
    legs = [distance_km(points[i], points[i + 1]) for i in range(len(points) - 1)]
    if return_to_start:
        legs.append(distance_km(points[-1], points[0]))
    return round(sum(legs), 2)
