"""Stop order and vehicle split for Project 16 (pure functions, no database).

Technique: optimization with a nearest-neighbour heuristic (the documented fallback
from a full route optimizer, which the portfolio does not ship).

Stop order: start at the origin warehouse, repeatedly drive to the closest stop not
yet visited (distance from shared.geo.distance_km; ties go to the lower site ID),
then return to the warehouse. Stops in blocked areas are removed before ordering.

Vehicle split: first-fit decreasing. Vehicles are tried largest capacity first.
Stops are taken heaviest first; a stop goes whole onto the first vehicle with room
for all of it. A stop that fits on no single vehicle is split by load line, heaviest
line first, each onto the first vehicle with room; a line that fits nowhere is split
by quantity, and anything still left goes onto the vehicle with the most room left
(that vehicle then shows as overloaded).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from shared import geo

Point = tuple[float, float]


def nearest_neighbour_order(origin: Point, stops: Mapping[str, Point]) -> list[str]:
    """Visit order of the stops starting from origin, always going to the nearest unvisited stop."""
    remaining = dict(stops)
    here = origin
    order: list[str] = []
    while remaining:
        nxt = min(remaining, key=lambda s: (geo.distance_km(here, remaining[s]), s))
        order.append(nxt)
        here = remaining.pop(nxt)
    return order


def route_distance(origin: Point, order: Sequence[str], positions: Mapping[str, Point]) -> float:
    """Total km from the origin through the stops in order and back to the origin."""
    if not order:
        return 0.0
    return geo.route_km([origin, *[positions[s] for s in order]], return_to_start=True)


def is_overloaded(weight_kg: float, capacity_kg: float) -> bool:
    """Over the stated capacity is overloaded; exactly at capacity is not."""
    return round(float(weight_kg), 3) > round(float(capacity_kg), 3)


def split_loads(
    vehicles: Sequence[tuple[str, float]],
    lines: Sequence[Mapping[str, Any]],
    stop_order: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """First-fit decreasing split of load lines across vehicles.

    vehicles: (vehicle_id, capacity_kg). lines: dicts with site_id, item_id, quantity,
    unit_weight_kg. Returns lines with vehicle_id and weight_kg added (same site and item
    on the same vehicle are merged).
    """
    if not vehicles:
        raise ValueError("No vehicle to split the load across")
    fleet = sorted(vehicles, key=lambda v: (-float(v[1]), v[0]))
    room = {vid: float(cap) for vid, cap in fleet}
    rank = {s: i for i, s in enumerate(stop_order)}
    by_stop: dict[str, list[Mapping[str, Any]]] = {}
    for line in lines:
        by_stop.setdefault(str(line["site_id"]), []).append(line)

    def weight(line: Mapping[str, Any], qty: int | None = None) -> float:
        return float(line["unit_weight_kg"]) * int(line["quantity"] if qty is None else qty)

    placed: dict[tuple[str, str, str], dict[str, Any]] = {}

    def put(line: Mapping[str, Any], vid: str, qty: int) -> None:
        key = (str(line["site_id"]), str(line["item_id"]), vid)
        row = placed.setdefault(
            key,
            {"site_id": key[0], "item_id": key[1], "vehicle_id": vid, "quantity": 0, "unit_weight_kg": float(line["unit_weight_kg"])},
        )
        row["quantity"] += qty
        room[vid] -= float(line["unit_weight_kg"]) * qty

    stops = sorted(by_stop, key=lambda s: (-sum(weight(x) for x in by_stop[s]), rank.get(s, len(rank)), s))
    for site in stops:
        stop_lines = by_stop[site]
        total = sum(weight(x) for x in stop_lines)
        whole = next((vid for vid, _ in fleet if room[vid] >= total - 1e-9), None)
        if whole is not None:
            for line in stop_lines:
                put(line, whole, int(line["quantity"]))
            continue
        for line in sorted(stop_lines, key=lambda x: (-weight(x), str(x["item_id"]))):
            fits = next((vid for vid, _ in fleet if room[vid] >= weight(line) - 1e-9), None)
            if fits is not None:
                put(line, fits, int(line["quantity"]))
                continue
            left = int(line["quantity"])
            unit = float(line["unit_weight_kg"])
            for vid, _ in fleet:
                if left == 0:
                    break
                can = left if unit <= 0 else min(left, math.floor(room[vid] / unit + 1e-9))
                if can > 0:
                    put(line, vid, can)
                    left -= can
            if left > 0:
                put(line, max(fleet, key=lambda v: (room[v[0]], v[0]))[0], left)
    out = []
    for row in placed.values():
        row["weight_kg"] = round(row["quantity"] * row["unit_weight_kg"], 2)
        out.append(row)
    out.sort(key=lambda r: (rank.get(r["site_id"], len(rank)), r["site_id"], r["vehicle_id"], r["item_id"]))
    return out


def primary_vehicle(lines: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """site_id -> the vehicle carrying the largest weight for that stop."""
    weights: dict[str, dict[str, float]] = {}
    for line in lines:
        per = weights.setdefault(str(line["site_id"]), {})
        per[str(line["vehicle_id"])] = per.get(str(line["vehicle_id"]), 0.0) + float(line["weight_kg"])
    return {site: max(per, key=lambda v: (per[v], v)) for site, per in weights.items()}
