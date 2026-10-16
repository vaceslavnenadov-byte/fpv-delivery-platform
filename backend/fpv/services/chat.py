"""Связь по заказу: переписка заказчика, оператора и координатора (монитор полёта, карточка заказа).

Сообщения — записи ``order_notes`` вида ``chat``; координатор по-прежнему может оставлять
замечания и инциденты (``add_note``), они показываются в той же ленте. К сообщению можно
приложить текущий кадр с камеры дрона (только во время выполнения заказа): кадр копируется
из буфера трансляции в объектное хранилище и не изменяется.

Писать могут заказчик (по своему заказу, после принятия оператором), назначенные оператор и
координатор, администратор. Заказчик видит только сообщения переписки — служебные замечания и
инциденты координатора ему не показываются; прикладывать кадр с камеры может только исполнитель.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Iterator, Tuple

from sqlalchemy.orm import Session

from .. import clock, runtime
from ..errors import Forbidden, NotFound
from ..models import Order, OrderNote
from . import audit, events, live, notifications
from . import orders as order_svc
from .identity import Principal
from .storage import StorageError, sha256_file


CUSTOMER_CHAT_STATUSES = ("accepted", "awaiting_readiness", "ready", "in_progress",
                          "awaiting_delivery_confirmation", "disputed")


def _assigned(p: Principal, order: Order) -> bool:
    return p.user_id in (order.operator_id, order.coordinator_id) or p.has("order.read_all")


def is_customer(p: Principal, order: Order) -> bool:
    return order.customer_id == p.user_id and not _assigned(p, order)


def participants(order: Order) -> list:
    return [u for u in (order.customer_id, order.operator_id, order.coordinator_id) if u]


def role_in_order(order: Order, user_id) -> str:
    if user_id == order.customer_id:
        return "customer"
    if user_id == order.operator_id:
        return "operator"
    if user_id == order.coordinator_id:
        return "coordinator"
    return "admin"


def post(db: Session, p: Principal, order_id: uuid.UUID, body: str, *, attach_frame: bool = False) -> OrderNote:
    p.require("order.chat")
    order = order_svc.visible_order(db, p, order_id)
    if is_customer(p, order):
        if order.status not in CUSTOMER_CHAT_STATUSES:
            raise Forbidden("Связь по заказу доступна после того, как оператор примет заказ")
        if attach_frame:
            raise Forbidden("Кадр с камеры прикладывает исполнитель заказа")
    elif not _assigned(p, order):
        raise Forbidden("Заказ не назначен вам")
    note = OrderNote(order_id=order.id, author_id=p.user_id, kind="chat", severity="info", body=body,
                     created_at=clock.now())
    frame: Path | None = live.frame(db, p, order.id) if attach_frame else None   # 409/404, если кадра нет
    db.add(note)
    db.flush()
    if frame is not None:
        key = f"chat/{order.id}/{note.id}.jpg"
        runtime.store().put_file(key, frame, "image/jpeg")
        note.attachment_key, note.attachment_type, note.attachment_sha256 = key, "image/jpeg", sha256_file(frame)
    audit.record(db, p.actor, "order.chat.message", entity_type="order", entity_id=order.id,
                 details={"note_id": note.id, "frame": frame is not None})
    others = [u for u in participants(order) if u != p.user_id]
    if others:
        notifications.create(db, others, "order.chat", f"Заказ №{order.number}: сообщение от {p.display_name}",
                             body[:300], order_id=order.id)
    events.notify(db, "order.note_added", {"order_id": order.id}, users=participants(order), roles=["admin"])
    return note


def attachment(db: Session, p: Principal, order_id: uuid.UUID, note_id: int) -> Tuple[Iterator[bytes], str]:
    order = order_svc.visible_order(db, p, order_id)
    note = db.get(OrderNote, note_id)
    if note is None or note.order_id != order.id or not note.attachment_key:
        raise NotFound("Вложение не найдено")
    if is_customer(p, order) and note.kind != "chat":
        raise NotFound("Вложение не найдено")
    try:
        return runtime.store().open(note.attachment_key), note.attachment_type or "application/octet-stream"
    except StorageError as exc:
        raise NotFound("Файл вложения не найден в хранилище") from exc
