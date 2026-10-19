"""Справочники администратора: типы грузов, типы (конфигурации) БПЛА и грузополучатели.

* Типы грузов — из них заказчик выбирает «Тип груза»; масса каждого груза не больше максимальной
  для его типа. Удалённый тип, который уже встречался в заказах, скрывается из выбора, но его
  название остаётся в истории заказов.
* Типы БПЛА — техник выбирает конфигурацию при заведении дрона, параметры подставляются сами;
  дрон, заведённый вручную, в справочник не попадает.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Conflict, NotFound, ValidationFailed
from ..models import CargoType, Consignee, DroneModel, Order, OrderCargoItem
from . import audit
from .identity import Principal

DRONE_MODEL_FIELDS = ("model", "manufacturer", "link_type", "construction", "engine_type", "max_range_m", "max_payload_g")


# --- типы грузов ----------------------------------------------------------------------------------

def cargo_types(db: Session, *, include_deleted: bool = False) -> List[CargoType]:
    stmt = select(CargoType).order_by(CargoType.name)
    if not include_deleted:
        stmt = stmt.where(CargoType.deleted_at.is_(None))
    return list(db.execute(stmt).scalars())


def cargo_by_code(db: Session, code: str) -> Optional[CargoType]:
    return db.execute(select(CargoType).where(CargoType.code == code)).scalar_one_or_none()


def cargo_names(db: Session) -> Dict[str, str]:
    return {c.code: c.name for c in cargo_types(db, include_deleted=True)}


def serialize_cargo(c: CargoType) -> Dict[str, Any]:
    return {"id": str(c.id), "code": c.code, "name": c.name, "category": c.category, "max_weight_g": c.max_weight_g,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None}


def _cargo_name_free(db: Session, name: str, except_id: Optional[uuid.UUID] = None) -> str:
    name = (name or "").strip()
    if not name:
        raise ValidationFailed("Укажите название груза")
    other = db.execute(select(CargoType.id).where(func.lower(CargoType.name) == name.lower(),
                                                  CargoType.deleted_at.is_(None))).scalar_one_or_none()
    if other is not None and other != except_id:
        raise Conflict(f"Тип груза «{name}» уже есть", code="cargo_type_exists")
    return name


def create_cargo(db: Session, p: Principal, data: Dict[str, Any]) -> CargoType:
    p.require("catalog.manage")
    c = CargoType(code=f"c_{uuid.uuid4().hex[:12]}", name=_cargo_name_free(db, data["name"]),
                  category=(data.get("category") or "").strip(), max_weight_g=data["max_weight_g"],
                  created_by=p.user_id, created_at=clock.now(), updated_at=clock.now())
    db.add(c)
    db.flush()
    db.info.pop("cargo_names", None)
    audit.record(db, p.actor, "catalog.cargo_created", entity_type="cargo_type", entity_id=c.id,
                 details=serialize_cargo(c))
    return c


def update_cargo(db: Session, p: Principal, cargo_id: uuid.UUID, data: Dict[str, Any]) -> CargoType:
    p.require("catalog.manage")
    c = db.get(CargoType, cargo_id)
    if c is None or c.deleted_at is not None:
        raise NotFound("Тип груза не найден")
    before = serialize_cargo(c)
    if data.get("name") is not None:
        c.name = _cargo_name_free(db, data["name"], c.id)
    if data.get("category") is not None:
        c.category = data["category"].strip()
    if data.get("max_weight_g") is not None:
        c.max_weight_g = data["max_weight_g"]
    c.updated_at = clock.now()
    db.flush()
    db.info.pop("cargo_names", None)
    audit.record(db, p.actor, "catalog.cargo_updated", entity_type="cargo_type", entity_id=c.id,
                 details={"before": before, "after": serialize_cargo(c)})
    return c


def delete_cargo(db: Session, p: Principal, cargo_id: uuid.UUID) -> None:
    p.require("catalog.manage")
    c = db.get(CargoType, cargo_id)
    if c is None or c.deleted_at is not None:
        raise NotFound("Тип груза не найден")
    used = db.execute(select(Order.id).where(Order.cargo_type == c.code).limit(1)).first() or \
        db.execute(select(OrderCargoItem.id).where(OrderCargoItem.cargo_type == c.code).limit(1)).first()
    audit.record(db, p.actor, "catalog.cargo_deleted", entity_type="cargo_type", entity_id=c.id,
                 details={"name": c.name, "kept_for_history": bool(used)})
    if used:
        c.deleted_at = clock.now()          # в оформленных заказах название сохраняется
    else:
        db.delete(c)
    db.flush()


# --- типы БПЛА ------------------------------------------------------------------------------------

def drone_models(db: Session) -> List[DroneModel]:
    return list(db.execute(select(DroneModel).order_by(DroneModel.manufacturer, DroneModel.model)).scalars())


def serialize_model(m: DroneModel) -> Dict[str, Any]:
    return {"id": str(m.id), **{f: getattr(m, f) for f in DRONE_MODEL_FIELDS},
            "updated_at": m.updated_at.isoformat() if m.updated_at else None}


def _model_free(db: Session, manufacturer: str, model: str, except_id: Optional[uuid.UUID] = None) -> None:
    other = db.execute(select(DroneModel.id).where(func.lower(DroneModel.manufacturer) == manufacturer.lower(),
                                                   func.lower(DroneModel.model) == model.lower())).scalar_one_or_none()
    if other is not None and other != except_id:
        raise Conflict(f"Тип БПЛА «{manufacturer} {model}» уже есть", code="drone_model_exists")


def create_model(db: Session, p: Principal, data: Dict[str, Any]) -> DroneModel:
    p.require("catalog.manage")
    vals = {f: (data[f].strip() if isinstance(data.get(f), str) else data.get(f)) for f in DRONE_MODEL_FIELDS}
    vals = {k: ("" if v is None and k in ("link_type", "construction", "engine_type") else v) for k, v in vals.items()}
    _model_free(db, vals["manufacturer"], vals["model"])
    m = DroneModel(**vals, created_by=p.user_id, created_at=clock.now(), updated_at=clock.now())
    db.add(m)
    db.flush()
    audit.record(db, p.actor, "catalog.drone_model_created", entity_type="drone_model", entity_id=m.id,
                 details=serialize_model(m))
    return m


def update_model(db: Session, p: Principal, model_id: uuid.UUID, data: Dict[str, Any]) -> DroneModel:
    p.require("catalog.manage")
    m = db.get(DroneModel, model_id)
    if m is None:
        raise NotFound("Тип БПЛА не найден")
    before = serialize_model(m)
    for f in DRONE_MODEL_FIELDS:
        if data.get(f) is not None:
            setattr(m, f, data[f].strip() if isinstance(data[f], str) else data[f])
    _model_free(db, m.manufacturer, m.model, m.id)
    m.updated_at = clock.now()
    db.flush()
    audit.record(db, p.actor, "catalog.drone_model_updated", entity_type="drone_model", entity_id=m.id,
                 details={"before": before, "after": serialize_model(m)})
    return m


def delete_model(db: Session, p: Principal, model_id: uuid.UUID) -> None:
    p.require("catalog.manage")
    m = db.get(DroneModel, model_id)
    if m is None:
        raise NotFound("Тип БПЛА не найден")
    audit.record(db, p.actor, "catalog.drone_model_deleted", entity_type="drone_model", entity_id=m.id,
                 details={"model": m.model, "manufacturer": m.manufacturer})
    db.delete(m)          # у заведённых дронов параметры остаются (ссылка обнуляется)
    db.flush()


# --- грузополучатели ------------------------------------------------------------------------------

PRIORITIES = {"low": "Низкий приоритет", "normal": "Обычный приоритет", "high": "Высокий приоритет",
              "highest": "Наивысший приоритет"}
CONSIGNEE_FIELDS = ("name", "kind", "recommended_cargo", "location", "priority", "comment")


def consignees(db: Session) -> List[Consignee]:
    return list(db.execute(select(Consignee).where(Consignee.deleted_at.is_(None))
                           .order_by(Consignee.name)).scalars())


def get_consignee(db: Session, consignee_id: uuid.UUID) -> Consignee:
    c = db.get(Consignee, consignee_id)
    if c is None or c.deleted_at is not None:
        raise NotFound("Грузополучатель не найден")
    return c


def serialize_consignee(db: Session, c: Consignee) -> Dict[str, Any]:
    cargo = cargo_by_code(db, c.recommended_cargo) if c.recommended_cargo else None
    return {"id": str(c.id), **{f: getattr(c, f) for f in CONSIGNEE_FIELDS},
            "priority_label": PRIORITIES.get(c.priority, c.priority),
            "recommended_cargo_name": cargo.name if cargo else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None}


def snapshot(db: Session, c: Consignee) -> Dict[str, Any]:
    """Сведения о грузополучателе, сохраняемые в заказе (справочник может измениться позже)."""
    d = serialize_consignee(db, c)
    d.pop("updated_at", None)
    return d


def _consignee_values(db: Session, data: Dict[str, Any], current: Optional[Consignee] = None) -> Dict[str, Any]:
    vals = {f: data.get(f) for f in CONSIGNEE_FIELDS if f in data and data.get(f) is not None}
    for f in ("name", "kind", "location", "comment"):
        if f in vals:
            vals[f] = vals[f].strip()
    if "recommended_cargo" in data:
        code = (data.get("recommended_cargo") or "").strip() or None
        if code is not None:
            c = cargo_by_code(db, code)
            if c is None or c.deleted_at is not None:
                raise ValidationFailed("Рекомендованный груз — выберите из списка «Типы грузов»")
        vals["recommended_cargo"] = code
    name = vals.get("name", current.name if current else "")
    if not name:
        raise ValidationFailed("Укажите название грузополучателя")
    other = db.execute(select(Consignee.id).where(func.lower(Consignee.name) == name.lower(),
                                                  Consignee.deleted_at.is_(None))).scalar_one_or_none()
    if other is not None and (current is None or other != current.id):
        raise Conflict(f"Грузополучатель «{name}» уже есть", code="consignee_exists")
    return vals


def create_consignee(db: Session, p: Principal, data: Dict[str, Any]) -> Consignee:
    p.require("catalog.manage")
    c = Consignee(**_consignee_values(db, data), created_by=p.user_id, created_at=clock.now(), updated_at=clock.now())
    db.add(c)
    db.flush()
    audit.record(db, p.actor, "catalog.consignee_created", entity_type="consignee", entity_id=c.id,
                 details=snapshot(db, c))
    return c


def update_consignee(db: Session, p: Principal, consignee_id: uuid.UUID, data: Dict[str, Any]) -> Consignee:
    p.require("catalog.manage")
    c = get_consignee(db, consignee_id)
    before = snapshot(db, c)
    for k, v in _consignee_values(db, data, c).items():
        setattr(c, k, v)
    c.updated_at = clock.now()
    db.flush()
    audit.record(db, p.actor, "catalog.consignee_updated", entity_type="consignee", entity_id=c.id,
                 details={"before": before, "after": snapshot(db, c)})
    return c


def delete_consignee(db: Session, p: Principal, consignee_id: uuid.UUID) -> None:
    p.require("catalog.manage")
    c = get_consignee(db, consignee_id)
    used = db.execute(select(Order.id).where(Order.consignee_id == c.id).limit(1)).first()
    audit.record(db, p.actor, "catalog.consignee_deleted", entity_type="consignee", entity_id=c.id,
                 details={"name": c.name, "kept_for_history": bool(used)})
    if used:
        c.deleted_at = clock.now()          # в заказах сведения о грузополучателе сохранены
    else:
        db.delete(c)
    db.flush()
