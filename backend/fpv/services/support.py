"""Техподдержка: обращения пользователей администратору в виде переписки.

* Обращение создаёт любой пользователь (``support.use``) — причина, тема, сообщение; к сообщениям
  можно прикладывать фото (PNG, JPEG, WebP) и видео (MP4/MOV, WebM).
* Обращение получают администраторы (``support.manage``): видят все обращения, меняют статус
  (в работе, отклонено, выполнено), добавляют в обсуждение других пользователей.
* Участник видит обращения, где он автор или добавлен; статус виден всем участникам.

Вложения проверяются по содержимому (сигнатуре), хранятся в объектном хранилище и отдаются только
участникам переписки и администраторам.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .. import clock, runtime
from ..errors import Forbidden, NotFound, ValidationFailed
from ..models import (Order, SupportAttachment, SupportMessage, SupportParticipant, SupportTicket, User)
from . import audit, events, notifications
from .identity import Principal
from .maps import sniff_image

REASONS = {"order": "По заказу", "account": "По аккаунту", "group": "По группе", "service": "По работе сервиса",
           "suggestion": "Предложения", "other": "Другое"}
STATUSES = {"new": "Новое", "in_progress": "В работе", "rejected": "Отклонено", "done": "Выполнено"}
IMAGE_MAX = 15 * 1024 * 1024
VIDEO_MAX = 50 * 1024 * 1024
_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "video/mp4": ".mp4",
        "video/quicktime": ".mov", "video/webm": ".webm"}


def sniff_media(data: bytes) -> str:
    """Тип вложения по содержимому: изображение или видео; иное не принимается."""
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        return "video/quicktime" if brand == b"qt  " else "video/mp4"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"
    try:
        kind, _, _ = sniff_image(data)
        return kind
    except ValidationFailed:
        raise ValidationFailed("Можно прикладывать фото (PNG, JPEG, WebP) и видео (MP4, MOV, WebM)",
                               code="unsupported_attachment")


def _brief(db: Session, uid: Optional[uuid.UUID]) -> Optional[Dict[str, Any]]:
    from ..api import serialize
    return serialize.user_brief(db, uid)


def _ticket(db: Session, ticket_id: int, *, for_update: bool = False) -> SupportTicket:
    stmt = select(SupportTicket).where(SupportTicket.id == ticket_id)
    if for_update:
        stmt = stmt.with_for_update()
    t = db.execute(stmt).scalar_one_or_none()
    if t is None:
        raise NotFound("Обращение не найдено")
    return t


def _participants(db: Session, ticket_id: int) -> List[uuid.UUID]:
    return list(db.execute(select(SupportParticipant.user_id).where(SupportParticipant.ticket_id == ticket_id)).scalars())


def _visible(db: Session, p: Principal, ticket_id: int, *, for_update: bool = False) -> SupportTicket:
    p.require("support.use")
    t = _ticket(db, ticket_id, for_update=for_update)
    if not p.has("support.manage") and p.user_id not in _participants(db, t.id):
        raise NotFound("Обращение не найдено")
    return t


def _join(db: Session, ticket_id: int, user_id: uuid.UUID, added_by: Optional[uuid.UUID]) -> bool:
    res = db.execute(pg_insert(SupportParticipant).values(ticket_id=ticket_id, user_id=user_id, added_by=added_by,
                                                          added_at=clock.now()).on_conflict_do_nothing())
    return bool(res.rowcount)


def _notify(db: Session, t: SupportTicket, user_ids: List[uuid.UUID], title: str, body: str = "",
            severity: str = "info") -> None:
    if user_ids:
        notifications.create(db, user_ids, "support", title, body[:300], severity=severity)
    events.publish(db, "support.updated", {"ticket_id": t.id}, users=_participants(db, t.id), roles=["admin"])


def serialize_ticket(db: Session, t: SupportTicket, viewer: Optional[uuid.UUID] = None) -> Dict[str, Any]:
    last = None
    if viewer:
        last = db.execute(select(SupportParticipant.last_read_at).where(
            SupportParticipant.ticket_id == t.id, SupportParticipant.user_id == viewer)).scalar_one_or_none()
    order = db.get(Order, t.order_id) if t.order_id else None
    return {"id": t.id, "reason": t.reason, "reason_label": REASONS[t.reason], "subject": t.subject,
            "status": t.status, "status_label": STATUSES[t.status], "author": _brief(db, t.author_id),
            "order": {"id": str(order.id), "number": order.number} if order else None,
            "created_at": t.created_at.isoformat(), "last_message_at": t.last_message_at.isoformat(),
            "status_changed_at": t.status_changed_at.isoformat() if t.status_changed_at else None,
            "status_changed_by": _brief(db, t.status_changed_by),
            "unread": bool(viewer) and (last is None or last < t.last_message_at)}


def create(db: Session, p: Principal, data: Dict[str, Any]) -> SupportTicket:
    p.require("support.use")
    reason = data.get("reason")
    if reason not in REASONS:
        raise ValidationFailed("Выберите причину обращения")
    subject = (data.get("subject") or "").strip()
    body = (data.get("body") or "").strip()
    if len(subject) < 3:
        raise ValidationFailed("Тема — не короче 3 символов")
    order_id = data.get("order_id")
    if order_id is not None:
        from . import orders
        orders.visible_order(db, p, order_id)
    now = clock.now()
    t = SupportTicket(author_id=p.user_id, reason=reason, subject=subject[:200], order_id=order_id, status="new",
                      created_at=now, last_message_at=now)
    db.add(t)
    db.flush()
    _join(db, t.id, p.user_id, p.user_id)
    if body:
        db.add(SupportMessage(ticket_id=t.id, author_id=p.user_id, body=body[:5000], created_at=now))
    db.flush()
    audit.record(db, p.actor, "support.ticket_created", entity_type="support_ticket", entity_id=t.id,
                 details={"reason": reason, "subject": t.subject})
    admins = [u for u in notifications.users_with_role(db, "admin") if u != p.user_id]
    _notify(db, t, admins, f"Обращение в ТП №{t.id}: {REASONS[reason]}", f"{p.display_name}: {t.subject}", "warning")
    return t


def list_mine(db: Session, p: Principal) -> List[SupportTicket]:
    p.require("support.use")
    return list(db.execute(select(SupportTicket).join(
        SupportParticipant, SupportParticipant.ticket_id == SupportTicket.id).where(
        SupportParticipant.user_id == p.user_id).order_by(SupportTicket.last_message_at.desc()).limit(500)).scalars())


def list_all(db: Session, p: Principal, *, status: Optional[str] = None, reason: Optional[str] = None,
             q: Optional[str] = None, date_from=None, date_to=None) -> List[SupportTicket]:
    p.require("support.manage")
    stmt = select(SupportTicket)
    if status:
        stmt = stmt.where(SupportTicket.status == status)
    if reason:
        stmt = stmt.where(SupportTicket.reason == reason)
    if date_from:
        stmt = stmt.where(SupportTicket.created_at >= date_from)
    if date_to:
        stmt = stmt.where(SupportTicket.created_at < date_to)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.join(User, User.id == SupportTicket.author_id).where(or_(
            SupportTicket.subject.ilike(like), User.display_name.ilike(like), User.domain_username.ilike(like)))
    return list(db.execute(stmt.order_by(SupportTicket.created_at.desc()).limit(1000)).scalars())


def detail(db: Session, p: Principal, ticket_id: int) -> Dict[str, Any]:
    t = _visible(db, p, ticket_id)
    msgs = list(db.execute(select(SupportMessage).where(SupportMessage.ticket_id == t.id)
                           .order_by(SupportMessage.id)).scalars())
    atts: Dict[int, List[SupportAttachment]] = {}
    if msgs:
        for a in db.execute(select(SupportAttachment).where(SupportAttachment.message_id.in_([m.id for m in msgs]))):
            atts.setdefault(a[0].message_id, []).append(a[0])
    parts = db.execute(select(SupportParticipant).where(SupportParticipant.ticket_id == t.id)
                       .order_by(SupportParticipant.added_at)).scalars()
    # отметка «прочитано»
    db.execute(SupportParticipant.__table__.update().where(
        SupportParticipant.ticket_id == t.id, SupportParticipant.user_id == p.user_id).values(last_read_at=clock.now()))
    from .identity import effective_roles
    return {**serialize_ticket(db, t),
            "participants": [{**(_brief(db, x.user_id) or {}), "roles": sorted(effective_roles(db, x.user_id)),
                              "added_by": _brief(db, x.added_by), "added_at": x.added_at.isoformat()} for x in parts],
            "messages": [{"id": m.id, "author": _brief(db, m.author_id), "body": m.body, "created_at": m.created_at.isoformat(),
                          "system": m.author_id is None,
                          "attachments": [{"id": a.id, "content_type": a.content_type, "size_bytes": a.size_bytes,
                                           "filename": a.filename,
                                           "url": f"/api/v1/support/attachments/{a.id}"} for a in atts.get(m.id, [])]}
                         for m in msgs]}


def _touch(db: Session, p: Principal, t: SupportTicket) -> None:
    t.last_message_at = clock.now()
    # администратор, ответивший в обращении, становится его участником — оно появится в его списке
    _join(db, t.id, p.user_id, p.user_id)


def post_message(db: Session, p: Principal, ticket_id: int, body: str) -> SupportMessage:
    t = _visible(db, p, ticket_id, for_update=True)
    body = (body or "").strip()
    if not body:
        raise ValidationFailed("Пустое сообщение")
    m = SupportMessage(ticket_id=t.id, author_id=p.user_id, body=body[:5000], created_at=clock.now())
    db.add(m)
    _touch(db, p, t)
    db.flush()
    others = [u for u in _participants(db, t.id) if u != p.user_id]
    _notify(db, t, others, f"Обращение №{t.id}: сообщение от {p.display_name}", body)
    return m


def add_attachment(db: Session, p: Principal, ticket_id: int, data: bytes, filename: str, comment: str) -> SupportMessage:
    t = _visible(db, p, ticket_id, for_update=True)
    kind = sniff_media(data[:64])
    limit = VIDEO_MAX if kind.startswith("video/") else IMAGE_MAX
    if len(data) > limit:
        raise ValidationFailed(f"Файл больше {limit // (1024 * 1024)} МБ", code="attachment_too_large")
    m = SupportMessage(ticket_id=t.id, author_id=p.user_id, body=(comment or "").strip()[:5000], created_at=clock.now())
    db.add(m)
    db.flush()
    key = f"support/{t.id}/{m.id}-{uuid.uuid4().hex}{_EXT[kind]}"
    fd, tmp = tempfile.mkstemp(prefix="fpv-support-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        runtime.store().put_file(key, Path(tmp), kind)
    finally:
        Path(tmp).unlink(missing_ok=True)
    safe_name = "".join(ch for ch in (filename or "")[:120] if ch.isprintable() and ch not in '/\\\\"')
    db.add(SupportAttachment(message_id=m.id, object_key=key, content_type=kind, size_bytes=len(data),
                             sha256=hashlib.sha256(data).hexdigest(), filename=safe_name))
    _touch(db, p, t)
    db.flush()
    audit.record(db, p.actor, "support.attachment_added", entity_type="support_ticket", entity_id=t.id,
                 details={"message_id": m.id, "content_type": kind, "size": len(data)})
    others = [u for u in _participants(db, t.id) if u != p.user_id]
    _notify(db, t, others, f"Обращение №{t.id}: {'видео' if kind.startswith('video/') else 'фото'} от {p.display_name}",
            comment or "")
    return m


def attachment(db: Session, p: Principal, attachment_id: int) -> Tuple[Iterator[bytes], SupportAttachment]:
    a = db.get(SupportAttachment, attachment_id)
    if a is None:
        raise NotFound("Вложение не найдено")
    m = db.get(SupportMessage, a.message_id)
    _visible(db, p, m.ticket_id)
    return runtime.store().open(a.object_key), a


def set_status(db: Session, p: Principal, ticket_id: int, status: str) -> SupportTicket:
    p.require("support.manage")
    if status not in STATUSES or status == "new":
        raise ValidationFailed("Статус: в работе, отклонено или выполнено")
    t = _ticket(db, ticket_id, for_update=True)
    before = t.status
    t.status, t.status_changed_by, t.status_changed_at = status, p.user_id, clock.now()
    db.add(SupportMessage(ticket_id=t.id, author_id=None, created_at=clock.now(),
                          body=f"Статус: {STATUSES[status]} ({p.display_name})"))
    _touch(db, p, t)
    db.flush()
    audit.record(db, p.actor, "support.status_changed", entity_type="support_ticket", entity_id=t.id,
                 details={"before": before, "after": status})
    others = [u for u in _participants(db, t.id) if u != p.user_id]
    _notify(db, t, others, f"Обращение №{t.id}: {STATUSES[status]}", t.subject)
    return t


def add_participant(db: Session, p: Principal, ticket_id: int, user_id: uuid.UUID) -> SupportTicket:
    p.require("support.manage")
    t = _ticket(db, ticket_id, for_update=True)
    u = db.get(User, user_id)
    if u is None or u.status in ("deleted",):
        raise NotFound("Пользователь не найден")
    if _join(db, t.id, u.id, p.user_id):
        db.add(SupportMessage(ticket_id=t.id, author_id=None, created_at=clock.now(),
                              body=f"{p.display_name} добавил(а) в обсуждение: {u.display_name}"))
        _touch(db, p, t)
        db.flush()
        audit.record(db, p.actor, "support.participant_added", entity_type="support_ticket", entity_id=t.id,
                     details={"user": u.domain_username})
        _notify(db, t, [u.id], f"Вас добавили в обращение №{t.id}", t.subject)
    return t


def find_users(db: Session, p: Principal, q: str) -> List[Dict[str, Any]]:
    p.require("support.manage")
    like = f"%{(q or '').lower()}%"
    rows = db.execute(select(User).where(User.status.in_(("active", "suspended")), or_(
        User.display_name.ilike(like), User.domain_username.ilike(like))).order_by(User.display_name).limit(20)).scalars()
    return [{"id": str(u.id), "display_name": u.display_name, "username": u.domain_username} for u in rows]


def counts(db: Session, p: Principal) -> Dict[str, int]:
    p.require("support.manage")
    return dict(db.execute(select(SupportTicket.status, func.count()).group_by(SupportTicket.status)).all())
