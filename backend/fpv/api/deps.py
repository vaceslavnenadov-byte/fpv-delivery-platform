"""Общие зависимости API: транзакция БД, аутентификация, CSRF, лимиты, идемпотентность."""
from __future__ import annotations

import hashlib
import ipaddress
import json
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

from fastapi import Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import db as dbmod
from .. import runtime
from ..errors import Conflict, Forbidden, NotAuthenticated, ValidationFailed
from ..models import Drone, IdempotencyKey
from ..security.rate_limit import SlidingWindowLimiter
from ..security.tokens import tokens_equal
from ..services import drones as drone_svc
from ..services.identity import Principal, authenticate_session

SESSION_COOKIE = "fpv_session"
CSRF_COOKIE = "fpv_csrf"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class Limiters:
    login: Optional[SlidingWindowLimiter] = None
    mutations: Optional[SlidingWindowLimiter] = None
    drone: Optional[SlidingWindowLimiter] = None


limiters = Limiters()


def init_limiters(login_per_min: int, mutation_per_min: int) -> None:
    limiters.login = SlidingWindowLimiter(login_per_min)
    limiters.mutations = SlidingWindowLimiter(mutation_per_min)
    limiters.drone = SlidingWindowLimiter(max(600, mutation_per_min * 5))


def get_db() -> Iterator[Session]:
    db = dbmod.new_session()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def client_ip(request: Request) -> Optional[str]:
    """IP клиента. X-Forwarded-For учитывается только от доверенного обратного прокси."""
    peer = request.client.host if request.client else None
    trusted = runtime.settings().trusted_proxies
    if peer and peer in trusted:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            candidate = fwd.split(",")[-1].strip()
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                pass
    try:
        if peer:
            ipaddress.ip_address(peer)
            return peer
    except ValueError:
        return None
    return None


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    token = request.cookies.get(SESSION_COOKIE, "")
    ip = client_ip(request)
    principal, rec = authenticate_session(db, runtime.settings(), runtime.directory(), token, ip)
    if request.method not in SAFE_METHODS:
        csrf = request.headers.get("x-csrf-token", "")
        if not csrf or not tokens_equal(csrf, rec.csrf_hash):
            raise Forbidden("Отсутствует или неверен CSRF-токен", code="csrf_failed")
        if limiters.mutations:
            limiters.mutations.hit(f"user:{principal.user_id}")
    db.commit()     # фиксируем last_seen/повторную проверку каталога
    return principal


def require(permission: str) -> Callable[[Principal], Principal]:
    """Зависимость: проверка конкретного разрешения из таблицы прав."""
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        p.require(permission)
        return p
    return dep


def current_drone(request: Request, db: Session = Depends(get_db),
                  authorization: str = Header(default="")) -> Drone:
    """Аутентификация сервисного модуля дрона (контур телеметрии): Authorization: Drone <ключ>."""
    scheme, _, key = authorization.partition(" ")
    if scheme.lower() != "drone" or not key:
        raise NotAuthenticated("Требуется ключ устройства", code="drone_auth_required")
    drone = drone_svc.authenticate_drone(db, key.strip())
    if limiters.drone:
        limiters.drone.hit(f"drone:{drone.id}")
    return drone


# ---------------------------------------------------------------------------
# Идемпотентность критических операций
# ---------------------------------------------------------------------------

def _request_hash(body: Any) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def idempotent(db: Session, p: Principal, key: Optional[str], route: str, body: Any,
               action: Callable[[], Tuple[int, Dict[str, Any]]]) -> JSONResponse:
    """Выполняет действие один раз для пары (пользователь, Idempotency-Key).

    Повторный запрос с тем же ключом (например, после обрыва связи) получает
    сохранённый ответ и не создаёт дубликатов. Ключ и результат фиксируются в
    одной транзакции с бизнес-изменением.
    """
    if not key:
        status, payload = action()
        db.commit()
        return JSONResponse(status_code=status, content=payload)
    if not (8 <= len(key) <= 128):
        raise ValidationFailed("Недопустимый Idempotency-Key")
    rhash = _request_hash({"route": route, "body": body})
    existing = db.execute(select(IdempotencyKey).where(IdempotencyKey.user_id == p.user_id,
                                                       IdempotencyKey.key == key)).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != rhash:
            raise Conflict("Idempotency-Key уже использован для другого запроса", code="idempotency_mismatch")
        if existing.response_status is None:
            raise Conflict("Запрос с этим ключом ещё выполняется", code="idempotency_in_progress")
        return JSONResponse(status_code=existing.response_status, content=existing.response_body,
                            headers={"Idempotent-Replay": "true"})
    rec = IdempotencyKey(user_id=p.user_id, key=key, route=route, request_hash=rhash)
    db.add(rec)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict("Запрос с этим ключом уже выполняется", code="idempotency_in_progress")
    status, payload = action()
    rec.response_status = status
    rec.response_body = json.loads(json.dumps(payload, default=str))
    db.commit()
    return JSONResponse(status_code=status, content=rec.response_body)
