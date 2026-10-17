"""Зона обслуживания платформы.

Прямоугольник (юг, запад, север, восток в градусах WGS 84), внутри которого должны находиться базы
парков и все точки маршрутов заказов. Администратор задаёт его в интерфейсе («Зона обслуживания»);
пока не задан — действует FPV_SERVICE_AREA из конфигурации сервера.

Изменение зоны не трогает уже оформленные заказы: проверка выполняется при создании парка, переносе
базы и проверке условий нового заказа. Базы парков, оказавшиеся вне новой зоны, нужно перенести —
до этого заказы на дроны такого парка не пройдут проверку, поэтому сохранение такой зоны требует
подтверждения.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import Settings
from ..errors import Conflict, NotFound, ValidationFailed
from ..models import Fleet, PlatformSetting
from . import audit, events, geo
from .identity import Principal

KEY = "service_area"
MAX_SPAN_DEG = 20.0          # защита от опечатки: зона больше ~2000 км по стороне — почти наверняка ошибка


def _from_bbox(b: List[float]) -> Dict[str, float]:
    return {"south": b[0], "west": b[1], "north": b[2], "east": b[3]}


def bbox(db: Session, settings: Settings) -> List[float]:
    """Действующая зона в виде [lat_min, lon_min, lat_max, lon_max]."""
    row = db.get(PlatformSetting, KEY)
    if row is not None:
        v = row.value
        return [v["south"], v["west"], v["north"], v["east"]]
    return list(settings.service_area)


def contains(db: Session, settings: Settings, lat: float, lon: float) -> bool:
    return geo.in_bbox(lat, lon, bbox(db, settings))


def describe(db: Session, settings: Settings) -> Dict[str, Any]:
    row = db.get(PlatformSetting, KEY)
    data = _from_bbox(bbox(db, settings))
    data.update({"source": "admin" if row else "config",
                 "config_default": _from_bbox(list(settings.service_area)),
                 "updated_at": row.updated_at.isoformat() if row and row.updated_at else None})
    if row and row.updated_by:
        from ..api import serialize
        data["updated_by"] = serialize.user_brief(db, row.updated_by)
    return data


def fleets_outside(db: Session, area: List[float]) -> List[Dict[str, Any]]:
    rows = db.execute(select(Fleet).where(Fleet.active.is_(True))).scalars()
    return [{"id": str(f.id), "name": f.name, "lat": f.base_lat, "lon": f.base_lon, "address": f.base_address}
            for f in rows if not geo.in_bbox(f.base_lat, f.base_lon, area)]


def _validate(v: Dict[str, float]) -> List[float]:
    s, w, n, e = v["south"], v["west"], v["north"], v["east"]
    if not (-85 <= s < n <= 85):
        raise ValidationFailed("Южная граница должна быть меньше северной (широта от −85 до 85)")
    if not (-180 <= w < e <= 180):
        raise ValidationFailed("Западная граница должна быть меньше восточной (долгота от −180 до 180)")
    if n - s > MAX_SPAN_DEG or e - w > MAX_SPAN_DEG:
        raise ValidationFailed(f"Слишком большая зона (больше {MAX_SPAN_DEG:g}° по стороне) — проверьте границы")
    return [round(s, 6), round(w, 6), round(n, 6), round(e, 6)]


def update(db: Session, settings: Settings, p: Principal, v: Dict[str, float], *, confirm: bool = False) -> Dict[str, Any]:
    p.require("platform.settings")
    area = _validate(v)
    outside = fleets_outside(db, area)
    if outside and not confirm:
        raise Conflict("Базы некоторых парков окажутся вне зоны обслуживания — заказы на их дроны не пройдут "
                       "проверку, пока техники не перенесут базы", code="fleets_outside", details={"fleets": outside})
    before = bbox(db, settings)
    row = db.get(PlatformSetting, KEY)
    value = _from_bbox(area)
    if row is None:
        row = PlatformSetting(key=KEY, value=value)
        db.add(row)
    else:
        row.value = value
    row.updated_by, row.updated_at = p.user_id, clock.now()
    db.flush()
    audit.record(db, p.actor, "platform.service_area_changed", entity_type="platform_setting", details={
        "before": _from_bbox(before), "after": value, "fleets_outside": [f["name"] for f in outside]})
    events.publish(db, "platform.settings_changed", {"key": KEY}, roles=["admin", "technician", "customer"])
    return describe(db, settings)


def reset(db: Session, settings: Settings, p: Principal) -> Dict[str, Any]:
    p.require("platform.settings")
    row = db.get(PlatformSetting, KEY)
    if row is None:
        raise NotFound("Зона уже берётся из настроек сервера")
    before = bbox(db, settings)
    db.delete(row)
    db.flush()
    audit.record(db, p.actor, "platform.service_area_changed", entity_type="platform_setting", details={
        "before": _from_bbox(before), "after": _from_bbox(list(settings.service_area)), "reset_to_config": True})
    events.publish(db, "platform.settings_changed", {"key": KEY}, roles=["admin", "technician", "customer"])
    return describe(db, settings)
