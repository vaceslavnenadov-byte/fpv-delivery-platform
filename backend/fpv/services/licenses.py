"""Квалификация операторов: лицензии на управление типами БПЛА из справочника «Типы БПЛА».

* Оператор загружает лицензию (номер, когда и кем выдана, скан — PNG/JPEG/WebP или PDF до 15 МБ);
  заявка получает статус «На согласовании» (pending).
* Администратор или рекрутёр: «Согласовать» (approved), «Исправить» (fix — оператор правит заявку и
  отправляет снова), «Отклонить» (rejected — «Отклонена/Аннулирована»). Статус можно сменить и после
  согласования.
* Заказы на дрон, у которого есть тип из справочника, видят и принимают только операторы с
  подтверждённой лицензией на этот тип. Дроны без типа (заведены вручную) допуска не требуют.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock, runtime
from ..errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..models import Drone, DroneModel, OperatorLicense, User
from . import audit, events, notifications
from .identity import Principal

STATUS_LABELS = {"pending": "На согласовании", "fix": "Исправить", "approved": "Подтверждена",
                 "rejected": "Отклонена/Аннулирована"}
SCAN_MAX = 15 * 1024 * 1024
_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "application/pdf": ".pdf"}
MODEL_FIELDS = ("model", "manufacturer", "link_type", "construction", "engine_type")


def sniff_scan(data: bytes) -> str:
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValidationFailed("Скан лицензии — изображение (PNG, JPEG, WebP) или PDF", code="unsupported_attachment")


def _full_name(u: Optional[User]) -> str:
    if u is None:
        return "—"
    name = " ".join(x for x in (u.last_name, u.first_name, u.middle_name) if x)
    return name or u.display_name


def serialize(db: Session, lic: OperatorLicense) -> Dict[str, Any]:
    u = db.get(User, lic.user_id)
    reviewer = db.get(User, lic.reviewed_by) if lic.reviewed_by else None
    return {
        "id": lic.id, "user_id": str(lic.user_id), "full_name": _full_name(u),
        "short_name": " ".join(x for x in ((u.last_name, u.first_name) if u else ()) if x) or (u.display_name if u else "—"),
        "username": u.domain_username if u else None,
        "drone_model_id": str(lic.drone_model_id) if lic.drone_model_id else None,
        **{f: (lic.model or {}).get(f) or "" for f in MODEL_FIELDS},
        "license_number": lic.license_number, "issued_on": lic.issued_on.isoformat(), "issued_by": lic.issued_by,
        # у новой версии скана новый адрес — браузер не покажет прежний из кэша
        "scan_type": lic.scan_type, "scan_name": lic.scan_name,
        "scan_url": f"/api/v1/licenses/{lic.id}/scan?v={(lic.scan_sha256 or '')[:12]}",
        "status": lic.status, "status_label": STATUS_LABELS[lic.status], "review_comment": lic.review_comment,
        "reviewed_by": reviewer.display_name if reviewer else None,
        "reviewed_at": lic.reviewed_at.isoformat() if lic.reviewed_at else None,
        "created_at": lic.created_at.isoformat() if lic.created_at else None,
        "updated_at": lic.updated_at.isoformat() if lic.updated_at else None,
    }


def _store_scan(data: bytes) -> Tuple[str, str, str]:
    if not data:
        raise ValidationFailed("Приложите скан лицензии", code="scan_required")
    if len(data) > SCAN_MAX:
        raise ValidationFailed("Скан больше 15 МБ", code="attachment_too_large")
    kind = sniff_scan(data[:16])
    key = f"licenses/{uuid.uuid4().hex}{_EXT[kind]}"
    fd, tmp = tempfile.mkstemp(prefix="fpv-license-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        runtime.store().put_file(key, Path(tmp), kind)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return key, kind, hashlib.sha256(data).hexdigest()


def _fields(data: Dict[str, Any]) -> Dict[str, Any]:
    number = (data.get("license_number") or "").strip()
    issued_by = (data.get("issued_by") or "").strip()
    if not number:
        raise ValidationFailed("Укажите номер лицензии")
    if len(issued_by) < 2:
        raise ValidationFailed("Укажите, кем выдана лицензия")
    try:
        issued_on = dt.date.fromisoformat(str(data.get("issued_on") or ""))
    except ValueError as exc:
        raise ValidationFailed("Укажите дату выдачи лицензии") from exc
    if issued_on > clock.now().date():
        raise ValidationFailed("Дата выдачи не может быть в будущем")
    return {"license_number": number[:100], "issued_by": issued_by[:300], "issued_on": issued_on}


def _reviewers(db: Session) -> List[uuid.UUID]:
    return list({*notifications.users_with_role(db, "admin"), *notifications.users_with_role(db, "recruiter")})


def submit(db: Session, p: Principal, data: Dict[str, Any], scan: bytes, filename: str = "") -> OperatorLicense:
    """Новая заявка оператора на допуск к типу БПЛА."""
    p.require("operator.license")
    try:
        model_id = uuid.UUID(str(data.get("drone_model_id")))
    except ValueError as exc:
        raise ValidationFailed("Выберите тип БПЛА из списка") from exc
    m = db.get(DroneModel, model_id)
    if m is None:
        raise ValidationFailed("Выберите тип БПЛА из списка", code="drone_model_not_found")
    if db.execute(select(OperatorLicense.id).where(OperatorLicense.user_id == p.user_id,
                                                   OperatorLicense.drone_model_id == m.id,
                                                   OperatorLicense.status.in_(("pending", "fix", "approved")))).first():
        raise Conflict("Заявка на этот тип БПЛА уже есть — откройте её в списке", code="license_exists")
    fields = _fields(data)
    key, kind, sha = _store_scan(scan)
    lic = OperatorLicense(user_id=p.user_id, drone_model_id=m.id, model={f: getattr(m, f) for f in MODEL_FIELDS},
                          scan_key=key, scan_type=kind, scan_name=_safe_name(filename), scan_sha256=sha,
                          status="pending", created_at=clock.now(), updated_at=clock.now(), **fields)
    db.add(lic)
    db.flush()
    audit.record(db, p.actor, "operator.license_submitted", entity_type="user", entity_id=p.user_id,
                 details={"license_id": lic.id, "model": lic.model.get("model"), "number": lic.license_number})
    notifications.create(db, _reviewers(db), "operator.license",
                         f"Подтверждение квалификации: {p.display_name}",
                         f"{lic.model.get('manufacturer')} {lic.model.get('model')}, лицензия {lic.license_number}")
    return lic


def _safe_name(name: str) -> str:
    return "".join(ch for ch in (name or "")[:120] if ch.isprintable() and ch not in '/\\"')


def resubmit(db: Session, p: Principal, license_id: int, data: Dict[str, Any], scan: bytes,
             filename: str = "") -> OperatorLicense:
    """Исправленная заявка (после «Исправить») — снова на согласование. Скан можно не менять."""
    p.require("operator.license")
    lic = _get(db, license_id, for_update=True)
    if lic.user_id != p.user_id:
        raise NotFound("Заявка не найдена")
    if lic.status != "fix":
        raise Conflict("Изменить можно только заявку со статусом «Исправить»", code="invalid_transition")
    for k, v in _fields(data).items():
        setattr(lic, k, v)
    if scan:
        lic.scan_key, lic.scan_type, lic.scan_sha256 = _store_scan(scan)
        lic.scan_name = _safe_name(filename)
    lic.status = "pending"
    lic.updated_at = clock.now()
    audit.record(db, p.actor, "operator.license_resubmitted", entity_type="user", entity_id=p.user_id,
                 details={"license_id": lic.id, "number": lic.license_number, "new_scan": bool(scan)})
    notifications.create(db, _reviewers(db), "operator.license",
                         f"Исправленная заявка на квалификацию: {p.display_name}", lic.license_number)
    return lic


def _get(db: Session, license_id: int, *, for_update: bool = False) -> OperatorLicense:
    stmt = select(OperatorLicense).where(OperatorLicense.id == license_id)
    if for_update:
        stmt = stmt.with_for_update()
    lic = db.execute(stmt).scalar_one_or_none()
    if lic is None:
        raise NotFound("Заявка не найдена")
    return lic


def visible(db: Session, p: Principal, license_id: int) -> OperatorLicense:
    lic = _get(db, license_id)
    if lic.user_id == p.user_id or p.has("license.review"):
        return lic
    raise NotFound("Заявка не найдена")


def mine(db: Session, p: Principal) -> List[OperatorLicense]:
    p.require("operator.license")
    return list(db.execute(select(OperatorLicense).where(OperatorLicense.user_id == p.user_id)
                           .order_by(OperatorLicense.created_at.desc())).scalars())


def all_requests(db: Session, p: Principal, status: Optional[str] = None) -> List[OperatorLicense]:
    p.require("license.review")
    stmt = select(OperatorLicense)
    if status:
        stmt = stmt.where(OperatorLicense.status == status)
    return list(db.execute(stmt.order_by(OperatorLicense.updated_at.desc()).limit(1000)).scalars())


def decide(db: Session, p: Principal, license_id: int, status: str, comment: str = "") -> OperatorLicense:
    """Решение по заявке: approved | fix | rejected (в том числе смена после согласования)."""
    p.require("license.review")
    if status not in ("approved", "fix", "rejected"):
        raise ValidationFailed("Неизвестное решение")
    lic = _get(db, license_id, for_update=True)
    if lic.user_id == p.user_id:
        raise Forbidden("Свою квалификацию подтверждает другой сотрудник")
    comment = (comment or "").strip()
    if status == "fix" and len(comment) < 3:
        raise ValidationFailed("Напишите, что исправить (не менее 3 символов)")
    before = lic.status
    lic.status, lic.review_comment = status, comment or None
    lic.reviewed_by, lic.reviewed_at, lic.updated_at = p.user_id, clock.now(), clock.now()
    audit.record(db, p.actor, "operator.license_decided", entity_type="user", entity_id=lic.user_id,
                 details={"license_id": lic.id, "before": before, "after": status, "comment": comment})
    title = {"approved": "Лицензия подтверждена", "fix": "Лицензию нужно исправить",
             "rejected": "Лицензия отклонена/аннулирована"}[status]
    notifications.create(db, [lic.user_id], "operator.license",
                         f"{title}: {lic.model.get('manufacturer')} {lic.model.get('model')}", comment,
                         severity="info" if status == "approved" else "warning")
    events.notify(db, "operator.license", {"license_id": lic.id}, users=[lic.user_id], roles=["admin", "recruiter"])
    return lic


def scan(db: Session, p: Principal, license_id: int) -> Tuple[Iterator[bytes], OperatorLicense]:
    lic = visible(db, p, license_id)
    return runtime.store().open(lic.scan_key), lic


# --- допуск к заказам -----------------------------------------------------------------------------

def approved_models(db: Session, user_id: uuid.UUID) -> Set[uuid.UUID]:
    return {m for (m,) in db.execute(select(OperatorLicense.drone_model_id).where(
        OperatorLicense.user_id == user_id, OperatorLicense.status == "approved",
        OperatorLicense.drone_model_id.is_not(None))).all()}


def admitted_condition(db: Session, user_id: uuid.UUID):
    """Условие на заказы: дрон без типа из справочника или тип, на который у оператора есть лицензия."""
    models = approved_models(db, user_id)
    drone_ok = select(Drone.id).where(Drone.drone_model_id.is_(None) if not models
                                      else (Drone.drone_model_id.is_(None) | Drone.drone_model_id.in_(models)))
    from ..models import Order
    return Order.drone_id.in_(drone_ok)


def check_admitted(db: Session, user_id: uuid.UUID, drone: Drone) -> None:
    if drone.drone_model_id is None:
        return
    if drone.drone_model_id not in approved_models(db, user_id):
        m = db.get(DroneModel, drone.drone_model_id)
        name = f"{m.manufacturer} {m.model}" if m else "этот тип БПЛА"
        raise Forbidden(f"Нет подтверждённой лицензии на {name} — загрузите её в разделе «Квалификация»",
                        code="license_required")
