"""Зоны обслуживания и назначение в них техников.

* Зон может быть сколько угодно; каждая — прямоугольник (юг, запад, север, восток, WGS 84).
* В зону назначается любое число техников; один техник может работать в нескольких зонах.
  Зона без назначенных техников открыта всем техникам.
* База парка должна лежать в зоне, доступной технику парка; парк привязывается к ней
  (``fleets.zone_id`` — самая маленькая из подходящих зон).
* Все точки маршрута заказа (А, Б, промежуточные, В) должны лежать в зоне парка дрона.

Изменение зон не трогает оформленные заказы: проверка выполняется при создании парка, переносе
базы и проверке условий нового заказа. Если после изменения базы каких-то парков окажутся вне зоны,
сохранение требует подтверждения.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, List, Optional, Set

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .. import clock
from ..config import Settings
from ..errors import Conflict, NotFound, ValidationFailed
from ..models import Fleet, PlatformSetting, ServiceZone, ServiceZoneTechnician, User
from . import audit, events, notifications
from .identity import Principal

SEED_KEY = "zones_seeded"
MAX_SPAN_DEG = 20.0          # защита от опечатки: зона больше ~2000 км по стороне — почти наверняка ошибка


# ---------------------------------------------------------------------------
# Чтение
# ---------------------------------------------------------------------------

def ensure_seeded(db: Session, settings: Settings) -> None:
    """Первая зона «Основная зона» из FPV_SERVICE_AREA — один раз, при первом обращении."""
    if db.get(PlatformSetting, SEED_KEY) is not None:
        return
    res = db.execute(pg_insert(PlatformSetting).values(key=SEED_KEY, value=True)
                     .on_conflict_do_nothing(index_elements=["key"]))
    if not res.rowcount:
        return
    if db.execute(select(ServiceZone.id).limit(1)).first() is None:
        s, w, n, e = settings.service_area
        zone = ServiceZone(name="Основная зона", south=s, west=w, north=n, east=e, active=True,
                           created_at=clock.now(), updated_at=clock.now())
        db.add(zone)
        db.flush()
        _rebind_fleets(db)


def contains(z: ServiceZone, lat: float, lon: float) -> bool:
    return z.south <= lat <= z.north and z.west <= lon <= z.east


def _area(z: ServiceZone) -> float:
    return (z.north - z.south) * (z.east - z.west)


def active_zones(db: Session, settings: Settings) -> List[ServiceZone]:
    ensure_seeded(db, settings)
    return list(db.execute(select(ServiceZone).where(ServiceZone.active.is_(True))
                           .order_by(ServiceZone.name)).scalars())


def _assignments(db: Session, zone_ids: Optional[Iterable[uuid.UUID]] = None) -> Dict[uuid.UUID, Set[uuid.UUID]]:
    stmt = select(ServiceZoneTechnician.zone_id, ServiceZoneTechnician.technician_id)
    if zone_ids is not None:
        stmt = stmt.where(ServiceZoneTechnician.zone_id.in_(list(zone_ids)))
    out: Dict[uuid.UUID, Set[uuid.UUID]] = {}
    for zid, tid in db.execute(stmt):
        out.setdefault(zid, set()).add(tid)
    return out


def zones_for_technician(db: Session, settings: Settings, technician_id: uuid.UUID) -> List[ServiceZone]:
    """Зоны, где технику можно держать базу парка: назначен или зона открыта всем."""
    zones = active_zones(db, settings)
    assigned = _assignments(db, [z.id for z in zones])
    return [z for z in zones if not assigned.get(z.id) or technician_id in assigned[z.id]]


def smallest_containing(zones: Iterable[ServiceZone], lat: float, lon: float) -> Optional[ServiceZone]:
    found = [z for z in zones if contains(z, lat, lon)]
    return min(found, key=_area) if found else None


def check_fleet_base(db: Session, settings: Settings, technician_id: uuid.UUID, lat: float, lon: float) -> ServiceZone:
    allowed = zones_for_technician(db, settings, technician_id)
    zone = smallest_containing(allowed, lat, lon)
    if zone is None:
        if not allowed:
            raise ValidationFailed("Вам не назначена ни одна зона обслуживания — обратитесь к администратору "
                                   "(«Зоны обслуживания»)", code="no_zone_assigned")
        names = ", ".join(f"«{z.name}»" for z in allowed)
        raise ValidationFailed(f"База парка должна быть в вашей зоне обслуживания: {names}. "
                               "Зоны и назначение техников меняет администратор: «Зоны обслуживания»",
                               code="base_outside_area")
    return zone


def order_zone(db: Session, settings: Settings, fleet: Optional[Fleet]) -> Optional[ServiceZone]:
    """Зона, в которой должны лежать точки маршрута заказа на дрон этого парка."""
    zones = active_zones(db, settings)
    if fleet is None:
        return None
    if fleet.zone_id:
        z = next((z for z in zones if z.id == fleet.zone_id), None)
        if z is not None and contains(z, fleet.base_lat, fleet.base_lon):
            return z
    return smallest_containing(zones, fleet.base_lat, fleet.base_lon)


def any_zone_contains_all(db: Session, settings: Settings, points: List[tuple]) -> Optional[ServiceZone]:
    """Для заказов без парка (дроны, зарегистрированные до появления парков)."""
    for z in sorted(active_zones(db, settings), key=_area):
        if all(contains(z, lat, lon) for lat, lon in points):
            return z
    return None


def center(db: Session, settings: Settings) -> tuple:
    zones = active_zones(db, settings)
    if zones:
        z = zones[0]
        return (z.south + z.north) / 2, (z.west + z.east) / 2
    s, w, n, e = settings.service_area
    return (s + n) / 2, (w + e) / 2


# ---------------------------------------------------------------------------
# Представление
# ---------------------------------------------------------------------------

def _user(db: Session, uid: Optional[uuid.UUID]) -> Optional[Dict[str, Any]]:
    from ..api import serialize
    return serialize.user_brief(db, uid)


def serialize(db: Session, z: ServiceZone, *, full: bool = False,
              assigned: Optional[Set[uuid.UUID]] = None) -> Dict[str, Any]:
    data = {"id": str(z.id), "name": z.name, "south": z.south, "west": z.west, "north": z.north, "east": z.east,
            "active": z.active}
    if full:
        assigned = assigned if assigned is not None else _assignments(db, [z.id]).get(z.id, set())
        fleets = list(db.execute(select(Fleet).where(Fleet.zone_id == z.id).order_by(Fleet.name)).scalars())
        data.update({
            "open_to_all": not assigned,
            "technicians": sorted([_user(db, t) for t in assigned if _user(db, t)], key=lambda u: u["display_name"]),
            "fleets": [{"id": str(f.id), "name": f.name, "active": f.active, "technician": _user(db, f.technician_id),
                        "base": {"lat": f.base_lat, "lon": f.base_lon, "address": f.base_address}} for f in fleets],
            "updated_at": z.updated_at.isoformat() if z.updated_at else None,
            "updated_by": _user(db, z.updated_by),
        })
    return data


def visible_zones(db: Session, settings: Settings, p: Principal) -> List[Dict[str, Any]]:
    """Администратору — все зоны с техниками и парками; технику — его зоны; остальным — активные зоны."""
    p.require("map.read")
    if p.has("platform.settings"):
        ensure_seeded(db, settings)
        zones = list(db.execute(select(ServiceZone).order_by(ServiceZone.name)).scalars())
        assigned = _assignments(db, [z.id for z in zones])
        return [serialize(db, z, full=True, assigned=assigned.get(z.id, set())) for z in zones]
    if p.has("fleet.manage"):
        zones = zones_for_technician(db, settings, p.user_id)
        assigned = _assignments(db, [z.id for z in zones])
        return [{**serialize(db, z), "open_to_all": not assigned.get(z.id)} for z in zones]
    return [serialize(db, z) for z in active_zones(db, settings)]


# ---------------------------------------------------------------------------
# Изменение (администратор)
# ---------------------------------------------------------------------------

def _validate(v: Dict[str, Any]) -> Dict[str, float]:
    s, w, n, e = float(v["south"]), float(v["west"]), float(v["north"]), float(v["east"])
    if not (-85 <= s < n <= 85):
        raise ValidationFailed("Южная граница должна быть меньше северной (широта от −85 до 85)")
    if not (-180 <= w < e <= 180):
        raise ValidationFailed("Западная граница должна быть меньше восточной (долгота от −180 до 180)")
    if n - s > MAX_SPAN_DEG or e - w > MAX_SPAN_DEG:
        raise ValidationFailed(f"Слишком большая зона (больше {MAX_SPAN_DEG:g}° по стороне) — проверьте границы")
    return {"south": round(s, 6), "west": round(w, 6), "north": round(n, 6), "east": round(e, 6)}


def _get(db: Session, zone_id: uuid.UUID, *, for_update: bool = False) -> ServiceZone:
    stmt = select(ServiceZone).where(ServiceZone.id == zone_id)
    if for_update:
        stmt = stmt.with_for_update()
    z = db.execute(stmt).scalar_one_or_none()
    if z is None:
        raise NotFound("Зона не найдена")
    return z


def _name_free(db: Session, name: str, except_id: Optional[uuid.UUID] = None) -> str:
    name = (name or "").strip()
    if not name:
        raise ValidationFailed("Укажите название зоны")
    other = db.execute(select(ServiceZone.id).where(ServiceZone.name == name)).scalar_one_or_none()
    if other is not None and other != except_id:
        raise Conflict(f"Зона «{name}» уже есть", code="zone_exists")
    return name


def _rebind_fleets(db: Session) -> None:
    """Привязка парков к зонам после изменения зон: прежняя зона, если база в ней, иначе самая
    маленькая подходящая активная зона (с учётом назначения техника)."""
    zones = list(db.execute(select(ServiceZone).where(ServiceZone.active.is_(True))).scalars())
    assigned = _assignments(db, [z.id for z in zones])
    for f in db.execute(select(Fleet)).scalars():
        current = next((z for z in zones if z.id == f.zone_id), None)
        if current is not None and contains(current, f.base_lat, f.base_lon):
            continue
        allowed = [z for z in zones if not assigned.get(z.id) or f.technician_id in assigned[z.id]]
        new = smallest_containing(allowed, f.base_lat, f.base_lon)
        f.zone_id = new.id if new else None
    db.flush()


def _fleets_left_outside(db: Session, zone: ServiceZone, bounds: Dict[str, float], active: bool) -> List[Dict[str, Any]]:
    fleets = db.execute(select(Fleet).where(Fleet.zone_id == zone.id, Fleet.active.is_(True))).scalars()
    out = []
    for f in fleets:
        inside = bounds["south"] <= f.base_lat <= bounds["north"] and bounds["west"] <= f.base_lon <= bounds["east"]
        if not active or not inside:
            out.append({"id": str(f.id), "name": f.name, "address": f.base_address})
    return out


def _changed(db: Session, p: Principal, action: str, zone: ServiceZone, details: Dict[str, Any]) -> None:
    audit.record(db, p.actor, action, entity_type="service_zone", entity_id=zone.id, details=details)
    events.publish(db, "platform.zones_changed", {"zone_id": str(zone.id)}, roles=["admin", "technician", "customer"])


def create(db: Session, settings: Settings, p: Principal, data: Dict[str, Any]) -> ServiceZone:
    p.require("platform.settings")
    ensure_seeded(db, settings)
    b = _validate(data)
    z = ServiceZone(name=_name_free(db, data.get("name", "")), **b, active=True, created_by=p.user_id,
                    updated_by=p.user_id, created_at=clock.now(), updated_at=clock.now())
    db.add(z)
    db.flush()
    _rebind_fleets(db)
    _changed(db, p, "service_zone.created", z, {"name": z.name, **b})
    return z


def update(db: Session, settings: Settings, p: Principal, zone_id: uuid.UUID, data: Dict[str, Any],
           *, confirm: bool = False) -> ServiceZone:
    p.require("platform.settings")
    z = _get(db, zone_id, for_update=True)
    before = {"name": z.name, "south": z.south, "west": z.west, "north": z.north, "east": z.east, "active": z.active}
    bounds = _validate({k: data.get(k, getattr(z, k)) for k in ("south", "west", "north", "east")})
    active = data["active"] if data.get("active") is not None else z.active
    outside = _fleets_left_outside(db, z, bounds, active)
    if outside and not confirm:
        raise Conflict("Базы этих парков окажутся вне зоны — заказы на их дроны не пройдут проверку, пока "
                       "техники не перенесут базы или парки не попадут в другую зону",
                       code="fleets_outside", details={"fleets": outside})
    if data.get("name") is not None:
        z.name = _name_free(db, data["name"], z.id)
    for k, v in bounds.items():
        setattr(z, k, v)
    z.active = active
    z.updated_by, z.updated_at = p.user_id, clock.now()
    db.flush()
    _rebind_fleets(db)
    _changed(db, p, "service_zone.updated", z, {"before": before, "after": {
        "name": z.name, **bounds, "active": z.active}, "fleets_outside": [f["name"] for f in outside]})
    return z


def delete(db: Session, settings: Settings, p: Principal, zone_id: uuid.UUID, *, confirm: bool = False) -> None:
    p.require("platform.settings")
    z = _get(db, zone_id, for_update=True)
    outside = _fleets_left_outside(db, z, {"south": 0, "west": 0, "north": 0, "east": 0}, False)
    if outside and not confirm:
        raise Conflict("В зоне есть парки — после удаления заказы на их дроны не пройдут проверку, пока парки "
                       "не попадут в другую зону", code="fleets_outside", details={"fleets": outside})
    _changed(db, p, "service_zone.deleted", z, {"name": z.name, "fleets": [f["name"] for f in outside]})
    db.execute(sa_delete(ServiceZoneTechnician).where(ServiceZoneTechnician.zone_id == z.id))
    db.delete(z)
    db.flush()
    _rebind_fleets(db)


def assign(db: Session, p: Principal, zone_id: uuid.UUID, technician_id: uuid.UUID, on: bool) -> ServiceZone:
    p.require("platform.settings")
    z = _get(db, zone_id)
    from .identity import effective_roles
    user = db.get(User, technician_id)
    if user is None or (on and "technician" not in effective_roles(db, technician_id)):
        raise ValidationFailed("Назначить в зону можно только пользователя с ролью «Техник»")
    if on:
        db.execute(pg_insert(ServiceZoneTechnician).values(zone_id=z.id, technician_id=technician_id,
                                                          assigned_by=p.user_id, assigned_at=clock.now())
                   .on_conflict_do_nothing())
        notifications.create(db, [technician_id], "zone.assigned", f"Вы назначены в зону обслуживания «{z.name}»")
    else:
        db.execute(sa_delete(ServiceZoneTechnician).where(ServiceZoneTechnician.zone_id == z.id,
                                                          ServiceZoneTechnician.technician_id == technician_id))
        notifications.create(db, [technician_id], "zone.assigned", f"Вы исключены из зоны обслуживания «{z.name}»")
    db.flush()
    _changed(db, p, "service_zone.technician_assigned" if on else "service_zone.technician_removed", z,
             {"zone": z.name, "technician": user.display_name if user else str(technician_id)})
    return z


def technicians(db: Session) -> List[Dict[str, Any]]:
    from .notifications import users_with_role
    ids = users_with_role(db, "technician")
    users = db.execute(select(User).where(User.id.in_(ids)).order_by(User.display_name)).scalars()
    return [{"id": str(u.id), "display_name": u.display_name, "username": u.domain_username} for u in users]
