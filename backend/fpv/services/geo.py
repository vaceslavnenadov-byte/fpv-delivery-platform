"""Геометрические расчёты для проверки маршрута."""
from __future__ import annotations

import math
from typing import List, Tuple

EARTH_R = 6371008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def _project(lat: float, lon: float, lat0: float) -> Tuple[float, float]:
    """Локальная равнопромежуточная проекция (достаточна для маршрутов до десятков км)."""
    x = math.radians(lon) * EARTH_R * math.cos(math.radians(lat0))
    y = math.radians(lat) * EARTH_R
    return x, y


def segment_point_distance_m(a: Tuple[float, float], b: Tuple[float, float], p: Tuple[float, float]) -> float:
    lat0 = (a[0] + b[0] + p[0]) / 3
    ax, ay = _project(a[0], a[1], lat0)
    bx, by = _project(b[0], b[1], lat0)
    px, py = _project(p[0], p[1], lat0)
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def in_bbox(lat: float, lon: float, bbox: List[float]) -> bool:
    lat_min, lon_min, lat_max, lon_max = bbox
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def interpolate(a: Tuple[float, float], b: Tuple[float, float], t: float) -> Tuple[float, float]:
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
