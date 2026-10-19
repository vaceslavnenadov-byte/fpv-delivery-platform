"""Notification Service: уведомления в приложении (БД + сигнал реального времени).

Уведомления создаются асинхронно обработчиками outbox; повторная обработка
того же события не создаёт дубликатов благодаря ``dedup_key``.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import Notification, Operator, Order, Role, User, UserRole
from . import events

STATUS_LABELS = {
    "draft": "Черновик",
    "pending_confirmation": "Ожидает подтверждения",
    "awaiting_operator": "Ожидает оператора",
    "accepted": "Принят оператором",
    "awaiting_readiness": "Ожидает проверки готовности",
    "ready": "Готов к выполнению",
    "in_progress": "Выполняется",
    "awaiting_delivery_confirmation": "Ожидает подтверждения доставки",
    "admin_review": "На проверке координатора",
    "awaiting_coordinator": "На согласовании координатора",
    "completed": "Выполнен",
    "rejected": "Отклонён",
    "cancelled": "Отменён",
    "aborted_technical": "Прерван по техническим причинам",
    "disputed": "Передан на рассмотрение спора",
}


def create(db: Session, user_ids: Iterable[uuid.UUID], kind: str, title: str, body: str = "", *,
           order_id: Optional[uuid.UUID] = None, severity: str = "info",
           dedup_key: Optional[str] = None) -> int:
    created = 0
    targets = {u for u in user_ids if u}
    for uid in targets:
        stmt = pg_insert(Notification).values(
            user_id=uid, kind=kind, title=title[:200], body=body[:2000], order_id=order_id,
            severity=severity, dedup_key=dedup_key)
        if dedup_key:
            stmt = stmt.on_conflict_do_nothing(index_elements=["user_id", "dedup_key"],
                                               index_where=Notification.dedup_key.isnot(None))
        res = db.execute(stmt)
        created += res.rowcount or 0
    if targets:
        events.notify(db, "notification.created", {"order_id": order_id, "kind": kind}, users=targets)
    return created


def users_with_role(db: Session, role_code: str) -> List[uuid.UUID]:
    rows = db.execute(select(UserRole.user_id).join(Role, Role.id == UserRole.role_id)
                      .join(User, User.id == UserRole.user_id)
                      .where(Role.code == role_code, User.status == "active", User.directory_enabled.is_(True))
                      .distinct()).scalars()
    return list(rows)


def approved_operators(db: Session) -> List[uuid.UUID]:
    ops = set(db.execute(select(Operator.user_id).where(Operator.status == "approved")).scalars())
    return [u for u in users_with_role(db, "operator") if u in ops]


@events.handler("order.status_changed", name="notifications.order_status")
def on_order_status_changed(db: Session, payload: Dict[str, Any], event_id: uuid.UUID) -> None:
    order = db.get(Order, uuid.UUID(payload["order_id"]))
    if order is None:
        return
    to_status = payload["to_status"]
    label = STATUS_LABELS.get(to_status, to_status)
    title = f"Заказ №{order.number}: {label}"
    body = payload.get("reason") or ""
    key = f"order-status:{event_id}"
    severity = "warning" if to_status in ("aborted_technical", "rejected", "disputed") else "info"
    parties = [order.customer_id, order.operator_id, order.coordinator_id]
    create(db, parties, "order.status", title, body, order_id=order.id, severity=severity, dedup_key=key)
    # Оператору заказ предлагает services/operator_dispatch.py — уведомление приходит только ему.
    if to_status in ("disputed", "aborted_technical"):
        create(db, users_with_role(db, "admin"), "order.attention", title, body, order_id=order.id,
               severity=severity, dedup_key=key)
    if to_status == "aborted_technical":
        create(db, users_with_role(db, "technician"), "drone.attention",
               f"Дрон заказа №{order.number} требует проверки", body, order_id=order.id,
               severity="warning", dedup_key=key)
