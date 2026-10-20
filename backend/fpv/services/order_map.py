"""Карта заказов (координатор, администратор): заказы в работе, маршруты, точки доставки и последние
координаты дронов по телеметрии. Только просмотр — платформа полётом не управляет."""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..models import Drone, Order, TelemetryRecord, User
from . import catalogs
from .identity import Principal
from .notifications import STATUS_LABELS
from .orders import PRIORITY_FIRST, cargo_items

# Заказы на карте: от согласования до подтверждения доставки.
MAP_STATUSES = ("awaiting_coordinator", "awaiting_operator", "accepted", "awaiting_readiness", "ready",
                "in_progress", "awaiting_delivery_confirmation")
# Дрон «на управлении оператора» — заказ принят оператором и не завершён.
OPERATED = ("accepted", "awaiting_readiness", "ready", "in_progress")
TRAIL_POINTS = 120
POSITION_MAX_AGE = dt.timedelta(minutes=30)


def person(db: Session, user_id) -> Optional[Dict[str, Any]]:
    """Имя и фамилия (если заполнены в анкете), иначе отображаемое имя."""
    if user_id is None:
        return None
    u = db.get(User, user_id)
    if u is None:
        return None
    name = " ".join(x for x in (u.first_name, u.last_name) if x) or u.display_name
    return {"id": str(u.id), "name": name}


def _position(db: Session, order: Order) -> Optional[Dict[str, Any]]:
    stmt = select(TelemetryRecord).where(TelemetryRecord.drone_id == order.drone_id,
                                         TelemetryRecord.lat.is_not(None), TelemetryRecord.lon.is_not(None))
    if order.status == "in_progress":
        stmt = stmt.where(TelemetryRecord.order_id == order.id)
    t = db.execute(stmt.order_by(TelemetryRecord.recorded_at.desc()).limit(1)).scalar_one_or_none()
    if t is None or clock.now() - t.recorded_at > POSITION_MAX_AGE:
        return None
    return {"lat": t.lat, "lon": t.lon, "alt_m": t.alt_m, "speed_mps": t.ground_speed_mps,
            "heading_deg": t.heading_deg, "battery_pct": t.battery_pct, "phase": t.flight_phase,
            "recorded_at": t.recorded_at.isoformat()}


def _trail(db: Session, order: Order) -> List[List[float]]:
    if order.status != "in_progress":
        return []
    rows = db.execute(select(TelemetryRecord.lat, TelemetryRecord.lon).where(
        TelemetryRecord.order_id == order.id, TelemetryRecord.lat.is_not(None))
        .order_by(TelemetryRecord.recorded_at.desc()).limit(TRAIL_POINTS)).all()
    return [[lat, lon] for lat, lon in reversed(rows)]


def snapshot(db: Session, p: Principal) -> Dict[str, Any]:
    p.require("order.map")
    orders = list(db.execute(select(Order).where(Order.status.in_(MAP_STATUSES))
                             .order_by(PRIORITY_FIRST, Order.created_at)).unique().scalars())
    names = catalogs.cargo_names(db)
    items = []
    for o in orders:
        r = o.route
        d = db.get(Drone, o.drone_id)
        operated = o.status in OPERATED
        items.append({
            "id": str(o.id), "number": o.number, "status": o.status, "status_label": STATUS_LABELS.get(o.status),
            "priority_level": o.priority_level, "repeat": o.repeat_of is not None,
            "operator": person(db, o.operator_id), "customer": person(db, o.customer_id),
            # заказ ждёт оператора — кому система его предложила (ждёт подтверждения)
            "offered_operator": person(db, o.offered_operator_id) if o.status == "awaiting_operator" else None,
            "coordinator": person(db, o.coordinator_id),
            "drone": {"id": str(d.id), "model": d.model, "manufacturer": d.manufacturer,
                      "registration_number": d.registration_number} if d else None,
            "cargo": [{"name": names.get(i.cargo_type, i.cargo_type), "description": i.description,
                       "weight_g": i.weight_g} for i in cargo_items(db, o)],
            "cargo_weight_g": o.cargo_weight_g,
            "consignee": {"name": (o.consignee or {}).get("name") or o.recipient_name,
                          "location": (o.consignee or {}).get("location"),
                          "kind": (o.consignee or {}).get("kind")},
            "route": {
                "base": {"lat": r.base_lat, "lon": r.base_lon, "address": r.base_address} if r.base_lat is not None else None,
                "origin": {"lat": r.origin_lat, "lon": r.origin_lon, "address": r.origin_address},
                "stops": r.stops or [],
                "destination": {"lat": r.dest_lat, "lon": r.dest_lon, "address": r.dest_address},
                "pickup_at_base": bool(r.pickup_at_base),
            } if r else None,
            "operated": operated,
            "position": _position(db, o) if operated else None,
            "trail": _trail(db, o),
        })
    return {"items": items, "generated_at": clock.now().isoformat()}
