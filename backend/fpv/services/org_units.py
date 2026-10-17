"""Внутренние группы пользователей: дерево любой глубины (компания → отдел → кабинет → …).

Группы носят чисто информационный характер — для сортировки и поиска пользователей; на права
доступа не влияют. Ведёт дерево администратор (``org.manage``); назначать пользователя в группу
могут рекрутер и администратор.
"""
from __future__ import annotations

import uuid
from collections import Counter
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Conflict, NotFound, ValidationFailed
from ..models import OrgUnit, User
from . import audit
from .identity import Principal


def _all(db: Session) -> List[OrgUnit]:
    return list(db.execute(select(OrgUnit).order_by(OrgUnit.name)).scalars())


def paths(db: Session) -> Dict[uuid.UUID, str]:
    """Полный путь каждой группы: «Компания / Отдел / Кабинет»."""
    units = {u.id: u for u in _all(db)}
    out: Dict[uuid.UUID, str] = {}

    def path(uid: uuid.UUID, guard: int = 0) -> str:
        if uid in out:
            return out[uid]
        u = units[uid]
        p = u.name if u.parent_id is None or guard > 50 else f"{path(u.parent_id, guard + 1)} / {u.name}"
        out[uid] = p
        return p
    for uid in units:
        path(uid)
    return out


def descendants(db: Session, unit_id: uuid.UUID) -> Set[uuid.UUID]:
    children: Dict[Optional[uuid.UUID], List[uuid.UUID]] = {}
    for u in _all(db):
        children.setdefault(u.parent_id, []).append(u.id)
    out, stack = {unit_id}, [unit_id]
    while stack:
        for c in children.get(stack.pop(), []):
            if c not in out:
                out.add(c)
                stack.append(c)
    return out


def list_units(db: Session, p: Principal) -> List[Dict[str, Any]]:
    if not (p.has("user.recruit") or p.has("org.manage")):
        p.require("org.manage")
    counts = Counter(dict(db.execute(select(User.org_unit_id, func.count()).where(
        User.org_unit_id.is_not(None), User.status != "deleted").group_by(User.org_unit_id)).all()))
    ps = paths(db)
    return [{"id": str(u.id), "parent_id": str(u.parent_id) if u.parent_id else None, "name": u.name,
             "path": ps[u.id], "members": counts.get(u.id, 0)} for u in _all(db)]


def _get(db: Session, unit_id: uuid.UUID) -> OrgUnit:
    u = db.get(OrgUnit, unit_id)
    if u is None:
        raise NotFound("Группа не найдена")
    return u


def _check_name(db: Session, name: str, parent_id: Optional[uuid.UUID], except_id: Optional[uuid.UUID] = None) -> str:
    name = (name or "").strip()
    if not name or len(name) > 160:
        raise ValidationFailed("Название группы — от 1 до 160 символов")
    stmt = select(OrgUnit.id).where(func.lower(OrgUnit.name) == name.lower(),
                                    OrgUnit.parent_id.is_(None) if parent_id is None else OrgUnit.parent_id == parent_id)
    other = db.execute(stmt).scalar_one_or_none()
    if other is not None and other != except_id:
        raise Conflict(f"Группа «{name}» здесь уже есть", code="org_unit_exists")
    return name


def create(db: Session, p: Principal, name: str, parent_id: Optional[uuid.UUID]) -> OrgUnit:
    p.require("org.manage")
    if parent_id is not None:
        _get(db, parent_id)
    u = OrgUnit(name=_check_name(db, name, parent_id), parent_id=parent_id, created_by=p.user_id,
                created_at=clock.now(), updated_at=clock.now())
    db.add(u)
    db.flush()
    audit.record(db, p.actor, "org_unit.created", entity_type="org_unit", entity_id=u.id,
                 details={"name": u.name, "parent_id": str(parent_id) if parent_id else None})
    return u


def update(db: Session, p: Principal, unit_id: uuid.UUID, data: Dict[str, Any]) -> OrgUnit:
    p.require("org.manage")
    u = _get(db, unit_id)
    before = {"name": u.name, "parent_id": str(u.parent_id) if u.parent_id else None}
    parent_id = u.parent_id
    if "parent_id" in data:
        parent_id = data["parent_id"]
        if parent_id is not None:
            _get(db, parent_id)
            if parent_id in descendants(db, u.id):
                raise ValidationFailed("Нельзя перенести группу внутрь неё самой")
    name = _check_name(db, data.get("name", u.name), parent_id, u.id)
    u.name, u.parent_id, u.updated_at = name, parent_id, clock.now()
    db.flush()
    audit.record(db, p.actor, "org_unit.updated", entity_type="org_unit", entity_id=u.id,
                 details={"before": before, "after": {"name": u.name, "parent_id": str(parent_id) if parent_id else None}})
    return u


def delete(db: Session, p: Principal, unit_id: uuid.UUID) -> None:
    p.require("org.manage")
    u = _get(db, unit_id)
    if db.execute(select(OrgUnit.id).where(OrgUnit.parent_id == u.id).limit(1)).first():
        raise Conflict("Сначала удалите или перенесите подгруппы", code="org_unit_not_empty")
    if db.execute(select(User.id).where(User.org_unit_id == u.id, User.status != "deleted").limit(1)).first():
        raise Conflict("В группе есть пользователи — переведите их в другую группу", code="org_unit_not_empty")
    audit.record(db, p.actor, "org_unit.deleted", entity_type="org_unit", entity_id=u.id, details={"name": u.name})
    db.delete(u)
    db.flush()
