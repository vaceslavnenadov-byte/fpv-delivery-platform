"""Пользователи: список и карточка, создание учётных записей приложения, анкета, статусы.

Права (см. docs/permissions.md):

* рекрутер (``user.recruit``) — список и карточки, новый пользователь с логином и паролем,
  группа и роли (кроме администратора и рекрутера);
* администратор — всё то же, а также роли администратора и рекрутера (кто и когда выдал —
  в карточке), смена логина и пароля учётных записей приложения (``user.credentials``, кто и когда
  менял — в карточке), бан/разбан/приостановка (``user.status``), анкета (``user.profile.edit``).

Учётные записи бывают доменные (вход по паролю домена) и созданные в приложении (``auth_source =
'local'``: пароль хранится как PBKDF2-SHA256 со случайной солью).
"""
from __future__ import annotations

import os
import re
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..models import AuditLog, Operator, OrgUnit, Role, User, UserRole
from . import audit, notifications, org_units, recruitment
from .directory import pbkdf2_hash
from .identity import Principal, revoke_user_sessions

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
STATUS_LABEL = {"active": "активна", "suspended": "приостановлена", "banned": "забанена",
                "disabled": "отключена", "deleted": "удалена"}
PROFILE_FIELDS = ("last_name", "first_name", "middle_name", "position_title", "profile_notes", "phone", "email",
                  "nickname", "location")


def _can_list(p: Principal) -> None:
    if not (p.has("user.recruit") or p.has("user.manage")):
        p.require("user.recruit")


def check_password(password: str) -> None:
    if len(password or "") < 10 or not re.search(r"[A-Za-zА-Яа-я]", password) or not re.search(r"\d", password):
        raise ValidationFailed("Пароль — не короче 10 символов, с буквами и цифрами", code="weak_password")


def hash_password(password: str) -> str:
    return pbkdf2_hash(password, salt=os.urandom(16), iterations=310_000)


def _full_name(d: Dict[str, Any]) -> str:
    return " ".join(x.strip() for x in (d.get("last_name"), d.get("first_name"), d.get("middle_name")) if x and x.strip())


def _brief(db: Session, uid: Optional[uuid.UUID]) -> Optional[Dict[str, Any]]:
    from ..api import serialize
    return serialize.user_brief(db, uid)


def _roles(db: Session, ids: List[uuid.UUID]) -> Dict[uuid.UUID, List[Dict[str, Any]]]:
    out: Dict[uuid.UUID, List[Dict[str, Any]]] = {}
    rows = db.execute(select(UserRole.user_id, Role.code, UserRole.source, UserRole.granted_by, UserRole.granted_at)
                      .join(Role, Role.id == UserRole.role_id).where(UserRole.user_id.in_(ids)))
    for uid, code, source, by, at in rows:
        out.setdefault(uid, []).append({"role": code, "source": source, "granted_by": by, "granted_at": at})
    return out


def list_people(db: Session, p: Principal, *, q: Optional[str] = None, status: Optional[str] = None,
                org_unit: Optional[uuid.UUID] = None, role: Optional[str] = None) -> List[Dict[str, Any]]:
    _can_list(p)
    stmt = select(User).where(User.status != "deleted")
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(User.domain_username.ilike(like), User.display_name.ilike(like),
                              User.email.ilike(like), User.position_title.ilike(like)))
    if status:
        stmt = stmt.where(User.status == status)
    if org_unit:
        stmt = stmt.where(User.org_unit_id.in_(org_units.descendants(db, org_unit)))
    users = list(db.execute(stmt.order_by(User.display_name).limit(1000)).scalars())
    roles = _roles(db, [u.id for u in users])
    if role:
        users = [u for u in users if any(r["role"] == role for r in roles.get(u.id, []))]
    ps = org_units.paths(db)
    return [{
        "id": str(u.id), "username": u.domain_username, "display_name": u.display_name, "auth_source": u.auth_source,
        "status": u.status, "status_label": STATUS_LABEL.get(u.status, u.status), "position_title": u.position_title,
        "org_unit": {"id": str(u.org_unit_id), "path": ps.get(u.org_unit_id, "")} if u.org_unit_id else None,
        "roles": [{"role": r["role"], "source": r["source"]} for r in roles.get(u.id, [])],
        "profile_status": u.profile_status, "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
    } for u in users]


def _user(db: Session, user_id: uuid.UUID) -> User:
    u = db.get(User, user_id)
    if u is None or u.status == "deleted":
        raise NotFound("Пользователь не найден")
    return u


def card(db: Session, p: Principal, user_id: uuid.UUID) -> Dict[str, Any]:
    _can_list(p)
    u = _user(db, user_id)
    ps = org_units.paths(db)
    op = db.get(Operator, u.id)
    roles = _roles(db, [u.id]).get(u.id, [])
    return {
        "id": str(u.id), "username": u.domain_username, "auth_source": u.auth_source, "display_name": u.display_name,
        "status": u.status, "status_label": STATUS_LABEL.get(u.status, u.status), "status_reason": u.status_reason,
        "status_changed_at": u.status_changed_at.isoformat() if u.status_changed_at else None,
        "status_changed_by": _brief(db, u.status_changed_by),
        "directory_enabled": u.directory_enabled,
        "profile": {f: getattr(u, f) for f in PROFILE_FIELDS},
        "org_unit": {"id": str(u.org_unit_id), "path": ps.get(u.org_unit_id, "")} if u.org_unit_id else None,
        "roles": [{"role": r["role"], "source": r["source"], "granted_by": _brief(db, r["granted_by"]),
                   "granted_at": r["granted_at"].isoformat() if r["granted_at"] else None} for r in roles],
        "password_changed_at": u.password_changed_at.isoformat() if u.password_changed_at else None,
        "password_changed_by": _brief(db, u.password_changed_by),
        "registered_by": _brief(db, u.registered_by),
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "profile_status": u.profile_status, "profile_completed": u.profile_completed,
        "profile_verified": u.registered_by is None or u.profile_verified_at is not None,
        "profile_verified_at": u.profile_verified_at.isoformat() if u.profile_verified_at else None,
        "profile_review_notes": u.profile_review_notes,
        "operator": {"status": op.status, "license_number": op.license_number} if op else None,
    }


HISTORY_LABELS = {
    "user.provisioned": "Учётная запись создана при первом входе (домен)",
    "user.created_local": "Создана учётная запись приложения",
    "user.registered": "Добавлен из домена",
    "user.profile_updated": "Пользователь изменил свой профиль",
    "user.profile_edited": "Анкета изменена",
    "user.profile_reviewed": "Проверка профиля",
    "user.credentials_changed": "Изменены логин или пароль",
    "user.role_granted": "Выдана роль",
    "user.role_revoked": "Снята роль",
    "user.roles_synced": "Роли обновлены по группам домена",
    "user.suspended": "Учётная запись приостановлена",
    "user.banned": "Учётная запись забанена",
    "user.unbanned": "Учётная запись возобновлена",
    "operator.applied": "Заявка на допуск оператора",
    "operator.decision": "Решение по допуску оператора",
    "operator.license_submitted": "Заявка на квалификацию (лицензия)",
    "operator.license_resubmitted": "Исправленная заявка на квалификацию",
    "operator.license_decided": "Решение по квалификации",
    "operator.suspended": "Допуск оператора приостановлен",
}


def history(db: Session, p: Principal, user_id: uuid.UUID) -> List[Dict[str, Any]]:
    """История изменения профиля: все действия с учётной записью от момента создания (из журнала аудита)."""
    if not (p.has("user.recruit") or p.has("user.profile.edit")):
        p.require("user.recruit")
    u = _user(db, user_id)
    rows = db.execute(select(AuditLog).where(AuditLog.entity_type == "user", AuditLog.entity_id == str(u.id),
                                             or_(AuditLog.action.like("user.%"), AuditLog.action.like("operator.%")))
                      .order_by(AuditLog.id)).scalars()
    out = []
    for r in rows:
        details = dict(r.details or {})
        for secret in ("password", "password_hash"):
            details.pop(secret, None)
        out.append({"id": r.id, "at": r.occurred_at.isoformat(), "action": r.action,
                    "label": HISTORY_LABELS.get(r.action, r.action), "actor": r.actor_label,
                    "outcome": r.outcome, "details": details})
    return out


def report_identity(db: Session, user_id: uuid.UUID) -> Dict[str, Any]:
    """Сведения профиля для шапки отчётов и раздела «Профиль»."""
    u = _user(db, user_id)
    groups = org_units.chain(db, u.org_unit_id)
    return {"display_name": u.display_name, "first_name": u.first_name, "last_name": u.last_name,
            "middle_name": u.middle_name, "nickname": u.nickname, "position_title": u.position_title,
            "location": u.location, "group": groups[0] if groups else None, "subgroups": groups[1:]}


def _check_org_unit(db: Session, unit_id: Optional[uuid.UUID]) -> Optional[uuid.UUID]:
    if unit_id is not None and db.get(OrgUnit, unit_id) is None:
        raise ValidationFailed("Группа не найдена")
    return unit_id


def create_local(db: Session, p: Principal, data: Dict[str, Any]) -> User:
    """Новая учётная запись приложения с логином и паролем (рекрутер или администратор)."""
    p.require("user.recruit")
    username = (data.get("username") or "").strip().lower()
    if not USERNAME_RE.match(username):
        raise ValidationFailed("Логин — 3–64 символа: латинские буквы, цифры, точка, дефис, подчёркивание; "
                               "начинается с буквы или цифры", code="bad_username")
    if db.execute(select(User.id).where(func.lower(User.domain_username) == username)).first():
        raise Conflict("Такой логин уже занят", code="username_taken")
    check_password(data.get("password") or "")
    name = _full_name(data)
    if len(name) < 2:
        raise ValidationFailed("Укажите фамилию и имя")
    now = clock.now()
    u = User(domain_object_id=f"local:{uuid.uuid4()}", domain_username=username, display_name=name,
             email=(data.get("email") or None), phone=(data.get("phone") or None), status="active",
             directory_enabled=True, auth_source="local", password_hash=hash_password(data["password"]),
             password_changed_at=now, password_changed_by=p.user_id, registered_by=p.user_id,
             # анкету заполнил добавивший; к обязанностям — после проверки профиля
             profile_completed=True, profile_status="pending",
             org_unit_id=_check_org_unit(db, data.get("org_unit_id")),
             created_at=now, updated_at=now,
             **{f: (data.get(f) or None) for f in ("last_name", "first_name", "middle_name", "position_title", "profile_notes")})
    db.add(u)
    db.flush()
    audit.record(db, p.actor, "user.created_local", entity_type="user", entity_id=u.id,
                 details={"username": username, "roles": data.get("roles") or [],
                          "org_unit_id": str(u.org_unit_id) if u.org_unit_id else None})
    for role in data.get("roles") or []:
        recruitment.set_role(db, p, u.id, role, True)
    return u


def update_profile(db: Session, p: Principal, user_id: uuid.UUID, data: Dict[str, Any]) -> User:
    """Анкета — администратор; группа — рекрутер или администратор."""
    u = _user(db, user_id)
    changes: Dict[str, Any] = {}
    if "org_unit_id" in data:
        if not (p.has("user.recruit") or p.has("user.profile.edit")):
            p.require("user.recruit")
        u.org_unit_id = _check_org_unit(db, data["org_unit_id"])
        changes["org_unit_id"] = str(u.org_unit_id) if u.org_unit_id else None
    fields = {k: v for k, v in data.items() if k in PROFILE_FIELDS}
    if fields:
        p.require("user.profile.edit")
        for k, v in fields.items():
            setattr(u, k, (v or "").strip() or None)
        changes.update(fields)
        name = _full_name({k: getattr(u, k) for k in ("last_name", "first_name", "middle_name")})
        if u.auth_source == "local" and len(name) >= 2:
            u.display_name = name
        u.profile_completed = True
    if not changes:
        raise ValidationFailed("Нечего сохранять")
    u.updated_at = clock.now()
    db.flush()
    audit.record(db, p.actor, "user.profile_edited", entity_type="user", entity_id=u.id, details=changes)
    return u


def set_credentials(db: Session, p: Principal, user_id: uuid.UUID, *, username: Optional[str] = None,
                    password: Optional[str] = None) -> User:
    """Смена логина и/или пароля учётной записи приложения (администратор); фиксируется кто и когда."""
    p.require("user.credentials")
    u = _user(db, user_id)
    if u.auth_source != "local":
        raise Conflict("Это доменная учётная запись — логин и пароль меняются в домене", code="domain_account")
    details: Dict[str, Any] = {}
    if username is not None and username.strip().lower() != u.domain_username:
        new = username.strip().lower()
        if not USERNAME_RE.match(new):
            raise ValidationFailed("Логин — 3–64 символа: латинские буквы, цифры, точка, дефис, подчёркивание",
                                   code="bad_username")
        if db.execute(select(User.id).where(func.lower(User.domain_username) == new, User.id != u.id)).first():
            raise Conflict("Такой логин уже занят", code="username_taken")
        details["username"] = {"before": u.domain_username, "after": new}
        u.domain_username = new
    if password:
        check_password(password)
        u.password_hash = hash_password(password)
        u.password_changed_at, u.password_changed_by = clock.now(), p.user_id
        details["password_changed"] = True
    if not details:
        raise ValidationFailed("Укажите новый логин или пароль")
    revoke_user_sessions(db, u.id, "credentials_changed")
    u.updated_at = clock.now()
    db.flush()
    audit.record(db, p.actor, "user.credentials_changed", entity_type="user", entity_id=u.id, details=details)
    notifications.create(db, [u.id], "user.account", "Изменены данные для входа в учётную запись",
                         "Логин или пароль изменил администратор.")
    return u


def set_status(db: Session, p: Principal, user_id: uuid.UUID, status: str, reason: str) -> User:
    """Бан, разбан, приостановка. Бан — вход запрещён; приостановка — вход есть, обязанности недоступны."""
    p.require("user.status")
    if status not in ("active", "suspended", "banned"):
        raise ValidationFailed("Статус: active, suspended или banned")
    u = _user(db, user_id)
    if u.id == p.user_id:
        raise Forbidden("Нельзя менять статус собственной учётной записи")
    reason = (reason or "").strip()
    if status != "active" and len(reason) < 3:
        raise ValidationFailed("Укажите причину (не менее 3 символов)")
    before = u.status
    u.status, u.status_reason = status, reason or None
    u.status_changed_at, u.status_changed_by, u.updated_at = clock.now(), p.user_id, clock.now()
    if status == "banned":
        revoke_user_sessions(db, u.id, "user_banned")
    db.flush()
    audit.record(db, p.actor, {"active": "user.unbanned", "suspended": "user.suspended", "banned": "user.banned"}[status],
                 entity_type="user", entity_id=u.id, details={"before": before, "after": status, "reason": reason})
    if status != "banned":
        notifications.create(db, [u.id], "user.account",
                             "Учётная запись приостановлена" if status == "suspended" else "Учётная запись снова активна",
                             reason, severity="warning" if status == "suspended" else "info")
    return u
