"""Жалобы, споры и апелляции оценок."""
from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..models import Complaint, ScoreTransaction
from . import audit, events, notifications, orders, scoring
from .identity import Principal


def create_appeal(db: Session, p: Principal, order_id: uuid.UUID, description: str) -> Complaint:
    """Оператор обжалует окончательную оценку."""
    p.require("score.appeal")
    order = orders.visible_order(db, p, order_id)
    if order.operator_id != p.user_id:
        raise Forbidden("Обжаловать можно только оценку собственного заказа")
    final = db.execute(select(ScoreTransaction.id).where(ScoreTransaction.order_id == order.id,
                                                         ScoreTransaction.kind == "final")).first()
    if not final:
        raise Conflict("Оценка ещё не окончательная", code="score_not_final")
    if db.execute(select(Complaint.id).where(Complaint.order_id == order.id, Complaint.kind == "score_appeal",
                                             Complaint.status == "open")).first():
        raise Conflict("Апелляция уже подана", code="appeal_exists")
    c = Complaint(order_id=order.id, author_id=p.user_id, kind="score_appeal", description=description,
                  created_at=clock.now())
    db.add(c)
    db.flush()
    audit.record(db, p.actor, "complaint.created", entity_type="complaint", entity_id=c.id,
                 details={"order_id": order.id, "kind": "score_appeal"})
    notifications.create(db, notifications.users_with_role(db, "admin"), "complaint.new",
                         f"Апелляция оценки по заказу №{order.number}", description[:300], order_id=order.id)
    return c


def list_complaints(db: Session, p: Principal, status: Optional[str] = None) -> List[Complaint]:
    stmt = select(Complaint)
    if not p.has("complaint.resolve"):
        stmt = stmt.where(Complaint.author_id == p.user_id)
    if status:
        stmt = stmt.where(Complaint.status == status)
    # сначала — претензии, удовлетворённые координатором и ждущие решения администратора
    escalated = (Complaint.coordinator_verdict == "upheld") & (Complaint.status == "open")
    return list(db.execute(stmt.order_by(escalated.desc().nulls_last(), Complaint.id.desc()).limit(500)).scalars())


def resolve(db: Session, p: Principal, complaint_id: int, *, upheld: bool, resolution: str,
            points_adjustment: Optional[int]) -> Complaint:
    """Рассмотрение жалобы администратором.

    Спор (dispute):
      * если по заказу ещё нет окончательной оценки — заказ возвращается на
        проверку администратора; обоснованная претензия уменьшает баллы за результат;
      * если оценка уже окончательная — заказ возвращается в итоговый статус,
        при обоснованной претензии выполняется корректировка баллов.
    Апелляция (score_appeal): при удовлетворении — корректировка на указанную величину.
    """
    p.require("complaint.resolve")
    c = db.execute(select(Complaint).where(Complaint.id == complaint_id).with_for_update()).scalar_one_or_none()
    if c is None:
        raise NotFound("Жалоба не найдена")
    if c.status != "open":
        raise Conflict("Жалоба уже рассмотрена", code="complaint_closed")
    if not resolution or len(resolution.strip()) < 5:
        raise ValidationFailed("Опишите решение (не менее 5 символов)")
    order = orders.get_order(db, c.order_id, for_update=True)
    c.status = "upheld" if upheld else "dismissed"
    c.resolution = resolution.strip()
    c.resolved_by = p.user_id
    c.resolved_at = clock.now()
    c.points_adjustment = points_adjustment
    db.flush()
    audit.record(db, p.actor, "complaint.resolved", entity_type="complaint", entity_id=c.id,
                 details={"order_id": order.id, "kind": c.kind, "upheld": upheld, "resolution": resolution,
                          "points_adjustment": points_adjustment})

    if c.kind == "dispute":
        _close_dispute(db, p, order, c, upheld, resolution, points_adjustment)
    elif c.kind == "score_appeal" and upheld:
        if points_adjustment is None or points_adjustment == 0:
            raise ValidationFailed("Укажите величину корректировки баллов")
        scoring.adjust(db, p, order, points_adjustment, f"Апелляция удовлетворена: {resolution}", complaint=c)

    notifications.create(db, [c.author_id, order.operator_id], "complaint.resolved",
                         f"Заказ №{order.number}: жалоба {'удовлетворена' if upheld else 'отклонена'}",
                         resolution[:300], order_id=order.id)
    return c


def _close_dispute(db: Session, p: Principal, order, c: Complaint, upheld: bool, resolution: str,
                   points_adjustment: Optional[int]) -> None:
    """Вернуть заказ из спора в дальнейший ход (общая часть решения администратора и отклонения
    претензии координатором)."""
    if order.status != "disputed":
        raise Conflict("Заказ не находится в статусе спора", code="invalid_transition")
    has_final = db.execute(select(ScoreTransaction.id).where(ScoreTransaction.order_id == order.id,
                                                             ScoreTransaction.kind == "final")).first()
    if has_final:
        target = order.status_before_dispute if order.status_before_dispute in ("completed", "rejected") \
            else "completed"
        orders.transition(db, order, target, p.actor, reason=f"Спор рассмотрен: {resolution}")
        if upheld:
            penalty = scoring.active_rules(db).rules["result"]["upheld_complaint_penalty"]
            amount = points_adjustment if points_adjustment is not None else -int(penalty)
            scoring.adjust(db, p, order, amount, f"Обоснованная претензия: {resolution}", complaint=c)
    else:
        orders.transition(db, order, "admin_review", p.actor, reason=f"Спор рассмотрен: {resolution}")
    order.status_before_dispute = None


def open_dispute_of(db: Session, order_id: uuid.UUID, *, for_update: bool = False) -> Optional[Complaint]:
    stmt = select(Complaint).where(Complaint.order_id == order_id, Complaint.kind == "dispute",
                                   Complaint.status == "open")
    if for_update:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def latest_dispute(db: Session, order_id: uuid.UUID) -> Optional[Complaint]:
    return db.execute(select(Complaint).where(Complaint.order_id == order_id, Complaint.kind == "dispute")
                      .order_by(Complaint.id.desc()).limit(1)).scalar_one_or_none()


def coordinator_review(db: Session, p: Principal, order_id: uuid.UUID, *, upheld: bool, comment: str) -> Complaint:
    """Предварительное рассмотрение претензии координатором заказа.

    * «Отклонить» — спор закрывается так же, как при отклонении администратором;
    * «Удовлетворить» — решение фиксируется, претензия остаётся открытой и передаётся
      администратору: ему приходит уведомление, окончательное решение и баллы — за ним.
    """
    p.require("complaint.review")
    order = orders.visible_order(db, p, order_id, for_update=True)
    if order.coordinator_id != p.user_id:
        raise Forbidden("Рассматривать претензию может только координатор этого заказа")
    if order.status != "disputed":
        raise Conflict("Заказ не находится на рассмотрении претензии", code="invalid_transition")
    c = open_dispute_of(db, order.id, for_update=True)
    if c is None:
        raise NotFound("Открытая претензия не найдена")
    if c.coordinator_verdict is not None:
        raise Conflict("Претензия уже рассмотрена координатором и передана администратору",
                       code="already_reviewed")
    comment = (comment or "").strip()
    if len(comment) < 5:
        raise ValidationFailed("Обоснуйте решение (не менее 5 символов)")
    now = clock.now()
    c.coordinator_verdict = "upheld" if upheld else "dismissed"
    c.coordinator_comment = comment
    c.coordinator_id = p.user_id
    c.coordinator_at = now
    db.flush()
    audit.record(db, p.actor, "complaint.coordinator_review", entity_type="complaint", entity_id=c.id,
                 details={"order_id": order.id, "upheld": upheld, "comment": comment})
    parties = [c.author_id, order.customer_id, order.operator_id]
    if upheld:
        notifications.create(db, notifications.users_with_role(db, "admin"), "complaint.escalated",
                             f"Заказ №{order.number}: координатор удовлетворил претензию — нужно ваше решение",
                             comment[:300], order_id=order.id, severity="warning")
        notifications.create(db, parties, "complaint.escalated",
                             f"Заказ №{order.number}: претензия удовлетворена координатором и передана администратору",
                             comment[:300], order_id=order.id)
        events.publish(db, "order.complaint_reviewed", {"order_id": str(order.id), "upheld": True},
                       users=[*parties, order.coordinator_id], roles=["admin"])
    else:
        c.status = "dismissed"
        c.resolution = f"Отклонена координатором: {comment}"
        c.resolved_by = p.user_id
        c.resolved_at = now
        db.flush()
        _close_dispute(db, p, order, c, False, c.resolution, None)
        notifications.create(db, parties, "complaint.resolved",
                             f"Заказ №{order.number}: претензия отклонена координатором", comment[:300],
                             order_id=order.id)
    return c
