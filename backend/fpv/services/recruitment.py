"""Рекрутер: добавление пользователей из каталога, выдача ролей, проверка профилей.

Учётные записи живут в доменном каталоге (Samba AD / FreeIPA); рекрутер не создаёт их там, а
добавляет существующую доменную учётную запись в систему заранее — до первого входа — и выдаёт
ей роли приложения. Без роли вход в приложение невозможен.

Рекрутер выдаёт и снимает роли из RECRUITABLE_ROLES (включая роль рекрутера). Роль администратора
выдаёт только администратор (разрешение user.manage); кто и когда её выдал, видно в карточке
пользователя (user_roles.granted_by/granted_at) и в журнале аудита. Роли из доменных групп меняются в каталоге, не здесь.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..models import Operator, Role, User, UserRole
from ..services.directory import DirectoryProvider, DirectoryUnavailable, normalize_username
from . import audit, identity, notifications
from .identity import Principal

RECRUITABLE_ROLES = ("customer", "operator", "coordinator", "technician", "recruiter")
PRIVILEGED_ROLES = ("admin",)


def _user(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None or user.status == "deleted":
        raise NotFound("Пользователь не найден")
    return user


def list_users(db: Session, p: Principal, q: Optional[str] = None,
               profile_status: Optional[str] = None) -> List[Dict[str, Any]]:
    p.require("user.recruit")
    stmt = select(User).where(User.status != "deleted")
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(User.domain_username.ilike(like), User.display_name.ilike(like)))
    if profile_status:
        # на проверке: заполненные профили и добавленные администратором/рекрутером учётные записи
        stmt = stmt.where(User.profile_status == profile_status,
                          or_(User.profile_completed.is_(True), User.registered_by.is_not(None)))
    users = list(db.execute(stmt.order_by(User.domain_username).limit(500)).scalars())
    ids = [u.id for u in users]
    roles: Dict[uuid.UUID, List[Dict[str, str]]] = {}
    for uid, code, source in db.execute(select(UserRole.user_id, Role.code, UserRole.source)
                                        .join(Role, Role.id == UserRole.role_id).where(UserRole.user_id.in_(ids))):
        roles.setdefault(uid, []).append({"role": code, "source": source})
    ops = {o.user_id: o for o in db.execute(select(Operator).where(Operator.user_id.in_(ids))).scalars()}
    names = {u.id: u.display_name for u in db.execute(select(User).where(User.id.in_(
        {x for u in users for x in (u.profile_reviewed_by, u.registered_by) if x}))).scalars()}
    from ..api import serialize
    return [{
        "id": str(u.id), "username": u.domain_username, "display_name": u.display_name, "email": u.email,
        "phone": u.phone, "status": u.status, "directory_enabled": u.directory_enabled,
        "roles": roles.get(u.id, []),
        "profile_completed": u.profile_completed, "profile_status": u.profile_status,
        "profile_review_notes": u.profile_review_notes, "profile_reviewed_at": serialize.ts(u.profile_reviewed_at),
        "profile_reviewed_by": names.get(u.profile_reviewed_by),
        "registered_by": names.get(u.registered_by),
        "profile_verified": u.registered_by is None or u.profile_verified_at is not None,
        "operator": {"status": ops[u.id].status, "license_number": ops[u.id].license_number,
                     "notes": ops[u.id].notes} if u.id in ops else None,
        "last_login_at": serialize.ts(u.last_login_at), "created_at": serialize.ts(u.created_at),
    } for u in users]


def register_user(db: Session, p: Principal, provider: DirectoryProvider, username: str,
                  roles: List[str]) -> User:
    """Добавить доменную учётную запись в систему (до первого входа) и сразу выдать роли."""
    p.require("user.recruit")
    username = normalize_username(username)
    if not username:
        raise ValidationFailed("Укажите имя доменной учётной записи")
    existing = db.execute(select(User).where(User.domain_username == username, User.status != "deleted")
                          ).scalar_one_or_none()
    if existing is not None and not roles:
        raise Conflict("Пользователь уже есть в системе — выдайте ему роли в списке", code="user_exists")
    try:
        found = provider.lookup(username)
    except DirectoryUnavailable as exc:
        raise Conflict("Каталог недоступен, попробуйте позже", code="directory_unavailable") from exc
    if found is None:
        raise NotFound("Учётная запись не найдена в доменном каталоге. Сначала её должен создать "
                       "администратор домена.")
    if not found.enabled:
        raise Conflict("Учётная запись заблокирована в каталоге", code="account_disabled")
    # Пользователь, уже пытавшийся войти без роли, есть в системе — его просто дополняем ролями.
    user = identity.sync_identity(db, found)
    if user.registered_by is None:
        user.registered_by = p.user_id
    db.flush()
    audit.record(db, p.actor, "user.registered", entity_type="user", entity_id=user.id,
                 details={"username": username, "roles": roles, "existing": existing is not None})
    for role in roles:
        set_role(db, p, user.id, role, True)
    return user


def set_role(db: Session, p: Principal, user_id: uuid.UUID, role_code: str, grant: bool) -> None:
    p.require("user.recruit")
    if role_code in PRIVILEGED_ROLES and not p.has("user.manage"):
        raise Forbidden("Роль администратора выдаёт только администратор")
    if role_code not in RECRUITABLE_ROLES + PRIVILEGED_ROLES:
        raise ValidationFailed("Неизвестная роль")
    user = _user(db, user_id)
    if user.id == p.user_id and not p.has("user.manage"):
        raise Forbidden("Нельзя менять собственные роли")
    role = db.execute(select(Role).where(Role.code == role_code)).scalar_one()
    if grant:
        if db.get(UserRole, (user.id, role.id, "manual")):
            return
        db.add(UserRole(user_id=user.id, role_id=role.id, source="manual", granted_by=p.user_id,
                        granted_at=clock.now()))
    else:
        res = db.execute(delete(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id,
                                                UserRole.source == "manual"))
        if not res.rowcount:
            raise Conflict("Роль выдана через доменную группу — её снимают в каталоге", code="domain_role")
        identity.revoke_user_sessions(db, user.id, "role_revoked")
    db.flush()
    audit.record(db, p.actor, "user.role_granted" if grant else "user.role_revoked", entity_type="user",
                 entity_id=user.id, details={"role": role_code, "source": "manual"})
    notifications.create(db, [user.id], "user.role",
                         f"{'Вам выдана' if grant else 'С вас снята'} роль «{role.name}»")


def review_profile(db: Session, p: Principal, user_id: uuid.UUID, approve: bool, notes: str) -> User:
    p.require("user.recruit")
    user = _user(db, user_id)
    if user.id == p.user_id:
        raise Forbidden("Собственный профиль подтверждает другой сотрудник")
    if not user.profile_completed and user.registered_by is None:
        raise Conflict("Пользователь ещё не заполнил профиль", code="profile_incomplete")
    notes = (notes or "").strip()
    if not approve and len(notes) < 5:
        raise ValidationFailed("Укажите, что исправить в профиле (не менее 5 символов)")
    before = user.profile_status
    user.profile_status = "verified" if approve else "rejected"
    user.profile_reviewed_by = p.user_id
    user.profile_reviewed_at = clock.now()
    user.profile_review_notes = notes or None
    if approve and user.profile_verified_at is None:
        user.profile_verified_at = clock.now()        # с этого момента доступны обязанности
    db.flush()
    audit.record(db, p.actor, "user.profile_reviewed", entity_type="user", entity_id=user.id,
                 details={"before": before, "after": user.profile_status, "notes": notes})
    notifications.create(db, [user.id], "user.profile",
                         "Профиль подтверждён" if approve else "Профиль возвращён на доработку", notes,
                         severity="info" if approve else "warning")
    return user


def recruiters(db: Session) -> List[uuid.UUID]:
    """Кому отправлять заявки на проверку: рекрутеры, а если их нет — администраторы."""
    return notifications.users_with_role(db, "recruiter") or notifications.users_with_role(db, "admin")
