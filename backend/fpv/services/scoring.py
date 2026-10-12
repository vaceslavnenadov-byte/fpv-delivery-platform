"""Scoring Service: прозрачная оценка работы оператора.

Модель (настраивается администратором, версионируется в scoring_rules):
* соблюдение согласованного временного окна — до 30 баллов. Оценивается
  отклонение момента завершения доставки от окна [window_start, window_end];
  абсолютная скорость полёта не учитывается;
* подтверждённый результат — до 70 баллов: подтверждение получения, отчёт,
  проверенное видео, подтверждение администратора, минус обоснованные претензии.

Окончательные баллы начисляются только после проверки администратором и
только один раз (уникальный индекс score_transactions_final_uq). Корректировки —
отдельные транзакции с обязательной причиной, привязанные к жалобе/апелляции.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import Conflict, NotFound, ValidationFailed
from ..models import (Complaint, DeliveryConfirmation, Operator, Order, ScoreTransaction, ScoringRules,
                      VideoEvidence)
from . import audit, events
from .identity import Principal

RESULT_KEYS = ("receipt_confirmed", "report_submitted", "video_verified", "admin_approved")


def active_rules(db: Session) -> ScoringRules:
    rules = db.execute(select(ScoringRules).where(ScoringRules.is_active.is_(True))).scalar_one_or_none()
    if rules is None:
        raise NotFound("Не заданы активные правила начисления баллов")
    return rules


def validate_rules(rules: Dict[str, Any]) -> None:
    try:
        tw = rules["time_window"]
        res = rules["result"]
        tw_max = int(tw["max_points"])
        grace = int(tw["grace_minutes"])
        parts = [int(res[k]) for k in RESULT_KEYS]
        penalty = int(res["upheld_complaint_penalty"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationFailed("Некорректная структура правил начисления баллов") from exc
    if min([tw_max, grace, penalty] + parts) < 0:
        raise ValidationFailed("Значения правил не могут быть отрицательными")
    if tw_max + sum(parts) != 100:
        raise ValidationFailed("Сумма максимальных баллов должна составлять 100")


def _time_points(order: Order, tw: Dict[str, Any]) -> Dict[str, Any]:
    max_points = int(tw["max_points"])
    grace = max(1, int(tw["grace_minutes"]))
    if order.delivered_at is None:
        return {"points": 0, "deviation_minutes": None, "note": "Доставка не завершена"}
    if order.window_start <= order.delivered_at <= order.window_end:
        deviation = 0.0
    elif order.delivered_at > order.window_end:
        deviation = (order.delivered_at - order.window_end).total_seconds() / 60
    else:
        deviation = (order.window_start - order.delivered_at).total_seconds() / 60
    points = round(max_points * max(0.0, 1 - deviation / grace))
    return {"points": int(points), "deviation_minutes": round(deviation, 1), "max": max_points}


def compute(db: Session, order: Order, rules: Dict[str, Any], *, preliminary: bool) -> Dict[str, Any]:
    res_rules = rules["result"]
    confirmations = {c.kind: c for c in db.execute(
        select(DeliveryConfirmation).where(DeliveryConfirmation.order_id == order.id)).scalars()}
    video = db.execute(select(VideoEvidence).where(VideoEvidence.order_id == order.id)).scalar_one_or_none()
    upheld = db.execute(select(func.count()).select_from(Complaint).where(
        Complaint.order_id == order.id, Complaint.kind == "dispute", Complaint.status == "upheld")).scalar_one()

    receipt = confirmations.get("recipient_receipt")
    admin = confirmations.get("admin_review")
    facts = {
        "receipt_confirmed": bool(receipt and receipt.decision == "confirmed"),
        "report_submitted": "operator_report" in confirmations,
        "video_verified": bool(video and video.status == "stored" and video.review_status == "approved"),
        "admin_approved": bool(admin and admin.decision == "confirmed"),
    }
    result_items = {k: (int(res_rules[k]) if facts[k] else 0) for k in RESULT_KEYS}
    penalty = int(res_rules["upheld_complaint_penalty"]) * int(upheld)
    result_max = sum(int(res_rules[k]) for k in RESULT_KEYS)
    result_points = max(0, min(result_max, sum(result_items.values()) - penalty))
    time_part = _time_points(order, rules["time_window"])
    total = time_part["points"] + result_points
    if not preliminary and order.status == "rejected":
        total, result_points = 0, 0
    return {
        "preliminary": preliminary,
        "total": int(total),
        "time_window": time_part,
        "result": {"points": result_points, "max": result_max, "items": result_items, "facts": facts,
                   "upheld_complaints": int(upheld), "penalty": penalty,
                   "video_status": video.status if video else None},
        "rejected": order.status == "rejected",
    }


def preview(db: Session, order: Order) -> Dict[str, Any]:
    """Предварительный (до проверки) или окончательный результат по заказу."""
    final = db.execute(select(ScoreTransaction).where(ScoreTransaction.order_id == order.id,
                                                      ScoreTransaction.kind == "final")).scalar_one_or_none()
    adjustments = list(db.execute(select(ScoreTransaction).where(
        ScoreTransaction.order_id == order.id, ScoreTransaction.kind == "adjustment")
        .order_by(ScoreTransaction.id)).scalars())
    if final is not None:
        data = dict(final.breakdown)
        data["preliminary"] = False
        data["final_points"] = final.points
        data["adjustments"] = [{"points": a.points, "reason": a.reason, "created_at": a.created_at.isoformat()}
                               for a in adjustments]
        data["total_with_adjustments"] = final.points + sum(a.points for a in adjustments)
        data["rule_version"] = final.rule_version
        return data
    rules = active_rules(db)
    data = compute(db, order, rules.rules, preliminary=True)
    data["rule_version"] = rules.version
    return data


def finalize(db: Session, p: Principal, order: Order) -> Optional[ScoreTransaction]:
    """Окончательное начисление после решения администратора. Повторный вызов ничего не меняет."""
    if order.operator_id is None or order.status not in ("completed", "rejected"):
        return None
    existing = db.execute(select(ScoreTransaction).where(ScoreTransaction.order_id == order.id,
                                                         ScoreTransaction.kind == "final")).scalar_one_or_none()
    if existing is not None:
        return existing
    rules = active_rules(db)
    breakdown = compute(db, order, rules.rules, preliminary=False)
    tx = ScoreTransaction(operator_id=order.operator_id, order_id=order.id, kind="final",
                          points=breakdown["total"], breakdown=breakdown, rule_version=rules.version,
                          reason="Окончательное начисление", created_by=p.user_id, created_at=clock.now())
    db.add(tx)
    db.flush()
    op = db.get(Operator, order.operator_id)
    if op is not None:
        op.total_points += tx.points
        if order.status == "completed":
            op.completed_orders += 1
    audit.record(db, p.actor, "score.finalized", entity_type="order", entity_id=order.id,
                 details={"operator_id": order.operator_id, "points": tx.points, "rule_version": rules.version})
    events.publish(db, "score.finalized", {"order_id": str(order.id), "points": tx.points},
                   users=[order.operator_id], roles=["admin"])
    return tx


def adjust(db: Session, p: Principal, order: Order, points: int, reason: str,
           complaint: Optional[Complaint] = None) -> ScoreTransaction:
    """Корректировка баллов (апелляция или спор). Причина обязательна, запись в аудит."""
    p.require("complaint.resolve")
    if not reason or len(reason.strip()) < 5:
        raise ValidationFailed("Укажите причину корректировки (не менее 5 символов)")
    final = db.execute(select(ScoreTransaction).where(ScoreTransaction.order_id == order.id,
                                                      ScoreTransaction.kind == "final")).scalar_one_or_none()
    if final is None:
        raise Conflict("Корректировка возможна только после окончательного начисления", code="score_not_final")
    if complaint is not None and db.execute(select(ScoreTransaction.id).where(
            ScoreTransaction.complaint_id == complaint.id)).first():
        raise Conflict("По этой жалобе корректировка уже выполнена", code="adjustment_exists")
    current = db.execute(select(func.coalesce(func.sum(ScoreTransaction.points), 0)).where(
        ScoreTransaction.order_id == order.id)).scalar_one()
    new_total = max(0, min(100, int(current) + int(points)))
    delta = new_total - int(current)
    tx = ScoreTransaction(operator_id=final.operator_id, order_id=order.id, kind="adjustment", points=delta,
                          breakdown={"requested": points, "before": int(current), "after": new_total},
                          reason=reason.strip(), complaint_id=complaint.id if complaint else None,
                          created_by=p.user_id, created_at=clock.now())
    db.add(tx)
    db.flush()
    op = db.get(Operator, final.operator_id)
    if op is not None:
        op.total_points += delta
    audit.record(db, p.actor, "score.adjusted", entity_type="order", entity_id=order.id,
                 details={"operator_id": final.operator_id, "requested": points, "applied": delta,
                          "reason": reason, "complaint_id": complaint.id if complaint else None})
    events.publish(db, "score.adjusted", {"order_id": str(order.id), "points": delta},
                   users=[final.operator_id], roles=["admin"])
    return tx


def operator_summary(db: Session, operator_id: uuid.UUID) -> Dict[str, Any]:
    op = db.get(Operator, operator_id)
    rows = list(db.execute(select(ScoreTransaction).where(ScoreTransaction.operator_id == operator_id)
                           .order_by(ScoreTransaction.id.desc()).limit(200)).scalars())
    finals = [r for r in rows if r.kind == "final"]
    rating = round(sum(r.points for r in finals) / len(finals), 1) if finals else None
    return {
        "total_points": op.total_points if op else 0,
        "completed_orders": op.completed_orders if op else 0,
        "rating": rating,
        "transactions": [{"id": r.id, "order_id": str(r.order_id), "kind": r.kind, "points": r.points,
                          "reason": r.reason, "created_at": r.created_at.isoformat()} for r in rows],
    }


def set_rules(db: Session, p: Principal, rules: Dict[str, Any], comment: str) -> ScoringRules:
    p.require("scoring.manage")
    validate_rules(rules)
    current = active_rules(db)
    current.is_active = False
    db.flush()
    new = ScoringRules(rules=rules, is_active=True, comment=comment, created_by=p.user_id, created_at=clock.now())
    db.add(new)
    db.flush()
    audit.record(db, p.actor, "scoring.rules_changed", entity_type="scoring_rules", entity_id=new.version,
                 details={"before": current.rules, "after": rules, "comment": comment})
    return new
