"""Authentication Service + User and Role Service.

* вход через доменную службу каталогов, создание/обновление профиля приложения;
* сопоставление доменных групп с ролями приложения (user_domain_groups / user_roles);
* серверные сессии (в БД хранится только хеш токена);
* периодическая повторная проверка учётной записи в каталоге и отзыв доступа;
* расчёт эффективных разрешений по таблице прав (permission_matrix);
* проверка доступа к устройствам (devices / device_access_policies).
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .. import clock
from ..config import Settings
from ..errors import Forbidden, NotAuthenticated, ServiceUnavailable
from ..models import (Device, DeviceAccessPolicy, DomainGroup, Operator, PermissionMatrix, Role,
                      SessionRecord, User, UserDomainGroup, UserRole)
from ..security.tokens import hash_token, new_token
from . import audit
from .directory import (AccountDisabled, DirectoryIdentity, DirectoryProvider, DirectoryUnavailable,
                        InvalidCredentials)


@dataclass
class Principal:
    """Аутентифицированный пользователь и его эффективные права для текущего запроса."""
    user_id: uuid.UUID
    username: str
    display_name: str
    roles: Set[str]
    permissions: Set[str]
    session_id: Optional[uuid.UUID] = None
    ip: Optional[str] = None
    profile_completed: bool = True
    operator_status: Optional[str] = None
    groups: List[str] = field(default_factory=list)

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise Forbidden(f"Недостаточно прав: требуется разрешение {permission}",
                            code="permission_denied", details={"permission": permission})

    @property
    def actor(self) -> audit.Actor:
        return audit.Actor(self.user_id, self.username, self.ip)


@dataclass
class LoginResult:
    principal: Principal
    session_token: str
    csrf_token: str
    expires_at: dt.datetime


# ---------------------------------------------------------------------------
# Разрешения
# ---------------------------------------------------------------------------

def effective_roles(db: Session, user_id: uuid.UUID) -> Set[str]:
    rows = db.execute(select(Role.code).join(UserRole, UserRole.role_id == Role.id)
                      .where(UserRole.user_id == user_id)).scalars()
    return set(rows)


def permissions_for_roles(db: Session, roles: Iterable[str]) -> Set[str]:
    roles = list(roles)
    if not roles:
        return set()
    rows = db.execute(select(PermissionMatrix.permission).join(Role, Role.id == PermissionMatrix.role_id)
                      .where(Role.code.in_(roles))).scalars()
    return set(rows)


def build_principal(db: Session, user: User, session_id: Optional[uuid.UUID] = None,
                    ip: Optional[str] = None) -> Principal:
    roles = effective_roles(db, user.id)
    perms = permissions_for_roles(db, roles)
    op = db.get(Operator, user.id)
    operator_status = op.status if op else None
    # Права оператора на работу с заказами действуют только после подтверждения администратором.
    if "operator" in roles and operator_status != "approved":
        perms -= {"order.queue.read", "order.accept", "order.execute", "order.report"}
    groups = list(db.execute(select(DomainGroup.name).join(
        UserDomainGroup, UserDomainGroup.domain_group_id == DomainGroup.id)
        .where(UserDomainGroup.user_id == user.id)).scalars())
    return Principal(user_id=user.id, username=user.domain_username, display_name=user.display_name,
                     roles=roles, permissions=perms, session_id=session_id, ip=ip,
                     profile_completed=user.profile_completed, operator_status=operator_status,
                     groups=sorted(groups))


# ---------------------------------------------------------------------------
# Синхронизация доменной учётной записи с профилем приложения
# ---------------------------------------------------------------------------

def sync_identity(db: Session, identity: DirectoryIdentity) -> User:
    """Создаёт или обновляет пользователя по доменному идентификатору, синхронизирует группы и роли."""
    now = clock.now()
    user = db.execute(select(User).where(User.domain_object_id == identity.object_id)).scalar_one_or_none()
    if user is None:
        # Имя занято другой (удалённой и пересозданной) учётной записью каталога — освобождаем его.
        stale = db.execute(select(User).where(User.domain_username == identity.username)).scalar_one_or_none()
        if stale is not None:
            stale.domain_username = f"{stale.domain_username}#replaced-{stale.id.hex[:8]}"
            stale.status = "disabled"
            stale.directory_enabled = False
            _revoke_user_sessions(db, stale.id, "domain_account_replaced")
            db.flush()
        user = User(domain_object_id=identity.object_id, domain_username=identity.username,
                    display_name=identity.display_name, email=identity.email, domain_upn=identity.upn,
                    status="active", directory_enabled=identity.enabled)
        db.add(user)
        db.flush()
        audit.record(db, audit.Actor.system("directory-sync"), "user.provisioned", entity_type="user",
                     entity_id=user.id, details={"username": identity.username})
    else:
        user.domain_username = identity.username
        user.display_name = identity.display_name
        user.domain_upn = identity.upn
        if identity.email:
            user.email = identity.email
        user.directory_enabled = identity.enabled
        if user.status == "disabled" and identity.enabled:
            # Повторная активация происходит только по решению администратора (status снимается вручную).
            pass
    user.last_directory_sync_at = now
    user.updated_at = now
    _sync_groups(db, user, identity.groups)
    return user


def _sync_groups(db: Session, user: User, group_dns: List[str]) -> None:
    lowered = {dn.lower() for dn in group_dns}
    known = list(db.execute(select(DomainGroup)).scalars())
    matched = [g for g in known if g.dn.lower() in lowered]
    current = set(db.execute(select(UserDomainGroup.domain_group_id)
                             .where(UserDomainGroup.user_id == user.id)).scalars())
    wanted = {g.id for g in matched}
    if current != wanted:
        db.execute(delete(UserDomainGroup).where(UserDomainGroup.user_id == user.id))
        for gid in wanted:
            db.add(UserDomainGroup(user_id=user.id, domain_group_id=gid))
    # Роли из доменных групп (source='domain') пересчитываются полностью; ручные роли не трогаем.
    wanted_roles = {g.role_id for g in matched if g.role_id is not None}
    have_roles = set(db.execute(select(UserRole.role_id).where(
        UserRole.user_id == user.id, UserRole.source == "domain")).scalars())
    if wanted_roles != have_roles:
        db.execute(delete(UserRole).where(UserRole.user_id == user.id, UserRole.source == "domain"))
        for rid in wanted_roles:
            db.add(UserRole(user_id=user.id, role_id=rid, source="domain"))
        role_codes = {r.id: r.code for r in db.execute(select(Role)).scalars()}
        audit.record(db, audit.Actor.system("directory-sync"), "user.roles_synced", entity_type="user",
                     entity_id=user.id, details={
                         "before": sorted(role_codes[r] for r in have_roles),
                         "after": sorted(role_codes[r] for r in wanted_roles)})
    db.flush()


# ---------------------------------------------------------------------------
# Вход, сессии, повторная проверка
# ---------------------------------------------------------------------------

def check_device_access(db: Session, user_id: uuid.UUID, device_code: str) -> Device:
    """Пользователь может работать с устройства только при наличии политики для его роли или группы."""
    device = db.execute(select(Device).where(Device.code == device_code)).scalar_one_or_none()
    if device is None or device.status != "active":
        raise Forbidden("Устройство не зарегистрировано или заблокировано", code="device_denied")
    role_ids = set(db.execute(select(UserRole.role_id).where(UserRole.user_id == user_id)).scalars())
    group_ids = set(db.execute(select(UserDomainGroup.domain_group_id)
                               .where(UserDomainGroup.user_id == user_id)).scalars())
    policies = db.execute(select(DeviceAccessPolicy).where(DeviceAccessPolicy.device_id == device.id)).scalars()
    for p in policies:
        if (p.role_id is not None and p.role_id in role_ids) or \
           (p.domain_group_id is not None and p.domain_group_id in group_ids):
            return device
    raise Forbidden("Доступ с этого устройства не разрешён таблицей прав", code="device_denied")


def login(db: Session, settings: Settings, provider: DirectoryProvider, username: str, password: str, *,
          ip: Optional[str], user_agent: Optional[str], device_code: Optional[str] = None) -> LoginResult:
    attempted = (username or "")[:64]
    try:
        identity = provider.authenticate(username, password)
    except DirectoryUnavailable:
        audit.record(db, audit.Actor(None, attempted or "anonymous", ip), "auth.login", outcome="failure",
                     details={"reason": "directory_unavailable"})
        db.commit()
        raise ServiceUnavailable("Служба каталогов недоступна. Вход временно невозможен.",
                                 code="directory_unavailable")
    except AccountDisabled:
        audit.record(db, audit.Actor(None, attempted, ip), "auth.login", outcome="denied",
                     details={"reason": "account_disabled"})
        user = db.execute(select(User).where(User.domain_username == attempted.lower())).scalar_one_or_none()
        if user is not None:
            user.directory_enabled = False
            _revoke_user_sessions(db, user.id, "domain_account_disabled")
        db.commit()
        raise NotAuthenticated("Учётная запись заблокирована в домене", code="account_disabled")
    except InvalidCredentials:
        audit.record(db, audit.Actor(None, attempted or "anonymous", ip), "auth.login", outcome="denied",
                     details={"reason": "invalid_credentials"})
        db.commit()
        raise NotAuthenticated("Неверное имя пользователя или пароль", code="invalid_credentials")

    user = sync_identity(db, identity)
    if user.status != "active":
        audit.record(db, audit.Actor(user.id, user.domain_username, ip), "auth.login", outcome="denied",
                     entity_type="user", entity_id=user.id, details={"reason": f"user_{user.status}"})
        db.commit()
        raise NotAuthenticated("Доступ к приложению отключён администратором", code="user_disabled")
    roles = effective_roles(db, user.id)
    if not roles:
        audit.record(db, audit.Actor(user.id, user.domain_username, ip), "auth.login", outcome="denied",
                     entity_type="user", entity_id=user.id, details={"reason": "no_application_roles"})
        db.commit()
        raise Forbidden("Учётной записи не назначена ни одна роль приложения. Обратитесь к администратору.",
                        code="no_roles")
    device_id = None
    if device_code:
        try:
            device_id = check_device_access(db, user.id, device_code).id
        except Forbidden:
            audit.record(db, audit.Actor(user.id, user.domain_username, ip), "auth.login", outcome="denied",
                         entity_type="device", entity_id=device_code, details={"reason": "device_denied"})
            db.commit()
            raise

    now = clock.now()
    token, csrf = new_token(), new_token()
    expires = now + dt.timedelta(seconds=settings.session_ttl_s)
    rec = SessionRecord(user_id=user.id, token_hash=hash_token(token), csrf_hash=hash_token(csrf),
                        device_id=device_id, ip_address=ip, user_agent=(user_agent or "")[:300],
                        expires_at=expires, last_seen_at=now, last_directory_check_at=now, created_at=now)
    db.add(rec)
    user.last_login_at = now
    db.flush()
    audit.record(db, audit.Actor(user.id, user.domain_username, ip), "auth.login", entity_type="user",
                 entity_id=user.id, details={"roles": sorted(roles), "device": device_code})
    principal = build_principal(db, user, rec.id, ip)
    return LoginResult(principal, token, csrf, expires)


def _revoke_user_sessions(db: Session, user_id: uuid.UUID, reason: str) -> int:
    res = db.execute(update(SessionRecord)
                     .where(SessionRecord.user_id == user_id, SessionRecord.revoked_at.is_(None))
                     .values(revoked_at=clock.now(), revoke_reason=reason))
    return res.rowcount or 0


def revoke_user_sessions(db: Session, user_id: uuid.UUID, reason: str) -> int:
    return _revoke_user_sessions(db, user_id, reason)


def logout(db: Session, principal: Principal) -> None:
    if principal.session_id:
        db.execute(update(SessionRecord).where(SessionRecord.id == principal.session_id)
                   .values(revoked_at=clock.now(), revoke_reason="logout"))
    audit.record(db, principal.actor, "auth.logout", entity_type="user", entity_id=principal.user_id)


def authenticate_session(db: Session, settings: Settings, provider: DirectoryProvider, token: str,
                         ip: Optional[str]) -> Tuple[Principal, SessionRecord]:
    """Проверка сессии при каждом защищённом запросе.

    Проверяются: срок действия, простой, отзыв, статус пользователя; раз в
    ``directory_revalidate_s`` — состояние учётной записи в каталоге. Если
    каталог недоступен, ранее выданная сессия продолжает действовать до
    следующей проверки (fail-open только для уже аутентифицированных, событие
    аудируется); новые входы при недоступном каталоге невозможны.
    """
    if not token:
        raise NotAuthenticated("Требуется вход в систему")
    rec = db.execute(select(SessionRecord).where(SessionRecord.token_hash == hash_token(token))).scalar_one_or_none()
    now = clock.now()
    if rec is None or rec.revoked_at is not None:
        raise NotAuthenticated("Сессия недействительна", code="session_invalid")
    if rec.expires_at <= now or (now - rec.last_seen_at).total_seconds() > settings.session_idle_s:
        rec.revoked_at = now
        rec.revoke_reason = "expired"
        db.commit()
        raise NotAuthenticated("Сессия истекла", code="session_expired")
    user = db.get(User, rec.user_id)
    if user is None or user.status != "active" or not user.directory_enabled:
        rec.revoked_at = now
        rec.revoke_reason = "user_inactive"
        db.commit()
        raise NotAuthenticated("Доступ отозван", code="access_revoked")
    if (now - rec.last_directory_check_at).total_seconds() >= settings.directory_revalidate_s:
        try:
            identity = provider.lookup(user.domain_username)
        except DirectoryUnavailable:
            audit.record(db, audit.Actor.system("directory-sync"), "auth.revalidate", outcome="failure",
                         entity_type="user", entity_id=user.id, details={"reason": "directory_unavailable"})
            identity = "unavailable"  # type: ignore[assignment]
        if identity is None or (identity != "unavailable" and (
                identity.object_id != user.domain_object_id or not identity.enabled)):
            user.directory_enabled = False
            n = _revoke_user_sessions(db, user.id, "domain_account_disabled")
            audit.record(db, audit.Actor.system("directory-sync"), "auth.access_revoked", entity_type="user",
                         entity_id=user.id, details={"sessions_revoked": n,
                                                     "reason": "deleted" if identity is None else "disabled"})
            db.commit()
            raise NotAuthenticated("Учётная запись заблокирована в домене. Доступ отозван.", code="access_revoked")
        if identity != "unavailable":
            sync_identity(db, identity)   # обновление групп и ролей
            rec.last_directory_check_at = now
    rec.last_seen_at = now
    db.flush()
    principal = build_principal(db, user, rec.id, ip)
    if not principal.roles:
        raise Forbidden("Учётной записи не назначена ни одна роль приложения", code="no_roles")
    return principal, rec


def sync_all_users(db: Session, provider: DirectoryProvider) -> Dict[str, int]:
    """Плановая синхронизация: блокировка в каталоге → отзыв доступа во всех сессиях."""
    stats = {"checked": 0, "disabled": 0, "updated": 0}
    users = list(db.execute(select(User).where(User.status == "active")).scalars())
    for user in users:
        stats["checked"] += 1
        identity = provider.lookup(user.domain_username)
        if identity is None or identity.object_id != user.domain_object_id or not identity.enabled:
            if user.directory_enabled:
                user.directory_enabled = False
                n = _revoke_user_sessions(db, user.id, "domain_account_disabled")
                audit.record(db, audit.Actor.system("directory-sync"), "auth.access_revoked",
                             entity_type="user", entity_id=user.id, details={"sessions_revoked": n})
                stats["disabled"] += 1
            continue
        sync_identity(db, identity)
        stats["updated"] += 1
    return stats
