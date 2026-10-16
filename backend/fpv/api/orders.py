"""REST API заказов (Order Management Service)."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import runtime
from ..errors import NotFound
from ..models import DeliveryConfirmation, Order, OrderNote, Route, TelemetryRecord, User
from ..services import complaints as complaint_svc
from ..services import orders as svc
from ..services import chat, scoring, video
from ..services.identity import Principal
from . import schemas, serialize
from .deps import current_principal, get_db, idempotent

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


def _view(db: Session, p: Principal, order: Order, status: int = 200):
    db.flush()
    return status, serialize.order(db, order, p)


@router.post("", status_code=201)
def create_order(body: schemas.OrderIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db),
                 idempotency_key: Optional[str] = Header(default=None)):
    return idempotent(db, p, idempotency_key, "orders.create", body.model_dump(mode="json"),
                      lambda: _view(db, p, svc.create_draft(db, runtime.settings(), p, body.model_dump()), 201))


@router.get("")
def list_orders(scope: str = Query("mine", pattern="^(mine|all)$"), status: Optional[str] = None,
                limit: int = Query(200, ge=1, le=1000),
                as_role: Optional[str] = Query(None, alias="as", pattern="^(customer|operator|coordinator)$"),
                p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    if scope == "all":
        p.require("order.read_all")
        stmt = select(Order)
        if status:
            stmt = stmt.where(Order.status == status)
        rows = list(db.execute(stmt.order_by(Order.created_at.desc()).limit(limit)).unique().scalars())
    else:
        rows = svc.my_orders(db, p, status, as_role)
    return {"items": [serialize.order(db, o, p, brief=True) for o in rows]}


@router.get("/queue")
def order_queue(max_distance_m: Optional[int] = Query(None, ge=1), cargo_type: Optional[str] = None,
                p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = svc.queue(db, p, max_distance_m=max_distance_m, cargo_type=cargo_type)
    return {"items": [serialize.order(db, o, p, brief=True) for o in rows]}


@router.get("/{order_id}")
def get_order(order_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return serialize.order(db, svc.visible_order(db, p, order_id), p)


@router.get("/{order_id}/history")
def order_history(order_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    svc.visible_order(db, p, order_id)
    rows = svc.history(db, order_id)
    names = {}
    for r in rows:
        if r.actor_id and r.actor_id not in names:
            u = db.get(User, r.actor_id)
            names[r.actor_id] = u.display_name if u else None
    return {"items": [{"id": r.id, "from": r.from_status, "to": r.to_status, "actor_kind": r.actor_kind,
                       "actor": names.get(r.actor_id) if r.actor_id else r.actor_kind, "reason": r.reason,
                       "created_at": serialize.ts(r.created_at)} for r in rows]}


@router.get("/{order_id}/telemetry")
def order_telemetry(order_id: uuid.UUID, limit: int = Query(300, ge=1, le=5000),
                    p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    order = svc.visible_order(db, p, order_id)
    assigned = p.user_id in (order.operator_id, order.coordinator_id) and p.has("telemetry.read_assigned")
    if not (assigned or p.has("telemetry.read_all")):
        # Позиция дрона раскрывается только при наличии соответствующего разрешения.
        raise NotFound("Телеметрия недоступна")
    rows = list(db.execute(select(TelemetryRecord).where(TelemetryRecord.order_id == order.id)
                           .order_by(TelemetryRecord.recorded_at.desc()).limit(limit)).scalars())
    rows.reverse()
    buffer = video.buffer_state(runtime.settings(), order.id) if order.status == "in_progress" else None
    return {"items": [serialize.telemetry(t) for t in rows], "video_buffer": buffer}


@router.get("/{order_id}/notes")
def order_notes(order_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    order = svc.visible_order(db, p, order_id)
    stmt = select(OrderNote).where(OrderNote.order_id == order.id)
    if chat.is_customer(p, order):
        stmt = stmt.where(OrderNote.kind == "chat")        # заказчику — только «Связь по заказу»
    rows = db.execute(stmt.order_by(OrderNote.id)).scalars()
    return {"items": [{"id": n.id, "kind": n.kind, "severity": n.severity, "body": n.body,
                       "author": serialize.user_brief(db, n.author_id),
                       "author_role": chat.role_in_order(order, n.author_id), "created_at": serialize.ts(n.created_at),
                       "attachment_url": f"/api/v1/orders/{order.id}/notes/{n.id}/attachment"
                       if n.attachment_key else None}
                      for n in rows]}


@router.post("/{order_id}/chat", status_code=201)
def post_chat(order_id: uuid.UUID, body: schemas.ChatIn, p: Principal = Depends(current_principal),
              db: Session = Depends(get_db)):
    note = chat.post(db, p, order_id, body.body, attach_frame=body.attach_frame)
    db.commit()
    return {"id": note.id, "has_attachment": note.attachment_key is not None}


@router.get("/{order_id}/notes/{note_id}/attachment")
def note_attachment(order_id: uuid.UUID, note_id: int, p: Principal = Depends(current_principal),
                    db: Session = Depends(get_db)):
    content, media_type = chat.attachment(db, p, order_id, note_id)
    return StreamingResponse(content, media_type=media_type,
                             headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


@router.get("/{order_id}/confirmations")
def order_confirmations(order_id: uuid.UUID, p: Principal = Depends(current_principal),
                        db: Session = Depends(get_db)):
    order = svc.visible_order(db, p, order_id)
    rows = db.execute(select(DeliveryConfirmation).where(DeliveryConfirmation.order_id == order.id)
                      .order_by(DeliveryConfirmation.id)).scalars()
    return {"items": [{"kind": c.kind, "decision": c.decision, "comment": c.comment, "report": c.report,
                       "actor": serialize.user_brief(db, c.actor_id), "created_at": serialize.ts(c.created_at)}
                      for c in rows]}


@router.get("/{order_id}/score")
def order_score(order_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    order = svc.visible_order(db, p, order_id)
    return scoring.preview(db, order)


# --- действия (критические операции поддерживают Idempotency-Key) ---

def _action(db, p, key, name, order_id, body, func):
    return idempotent(db, p, key, f"orders.{name}:{order_id}", body, lambda: _view(db, p, func()))


@router.post("/{order_id}/submit")
def submit(order_id: uuid.UUID, body: schemas.VersionIn = schemas.VersionIn(), p: Principal = Depends(current_principal),
           db: Session = Depends(get_db), idempotency_key: Optional[str] = Header(default=None)):
    return _action(db, p, idempotency_key, "submit", order_id, body.model_dump(),
                   lambda: svc.submit(db, runtime.settings(), p, order_id, body.expected_version))


@router.post("/{order_id}/confirm")
def confirm(order_id: uuid.UUID, body: schemas.VersionIn = schemas.VersionIn(),
            p: Principal = Depends(current_principal), db: Session = Depends(get_db),
            idempotency_key: Optional[str] = Header(default=None)):
    return _action(db, p, idempotency_key, "confirm", order_id, body.model_dump(),
                   lambda: svc.confirm(db, runtime.settings(), p, order_id, body.expected_version))


@router.post("/{order_id}/cancel")
def cancel(order_id: uuid.UUID, body: schemas.ReasonIn, p: Principal = Depends(current_principal),
           db: Session = Depends(get_db), idempotency_key: Optional[str] = Header(default=None)):
    return _action(db, p, idempotency_key, "cancel", order_id, body.model_dump(),
                   lambda: svc.cancel(db, p, order_id, body.reason, body.expected_version))


@router.post("/{order_id}/accept")
def accept(order_id: uuid.UUID, body: schemas.VersionIn = schemas.VersionIn(),
           p: Principal = Depends(current_principal), db: Session = Depends(get_db),
           idempotency_key: Optional[str] = Header(default=None)):
    return _action(db, p, idempotency_key, "accept", order_id, body.model_dump(),
                   lambda: svc.accept(db, p, order_id, body.expected_version))


@router.post("/{order_id}/decline")
def decline(order_id: uuid.UUID, body: schemas.ReasonIn, p: Principal = Depends(current_principal),
            db: Session = Depends(get_db), idempotency_key: Optional[str] = Header(default=None)):
    return _action(db, p, idempotency_key, "decline", order_id, body.model_dump(),
                   lambda: svc.decline(db, p, order_id, body.reason, body.expected_version))


@router.post("/{order_id}/readiness-check")
def readiness_check(order_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    order, reasons = svc.check_readiness(db, runtime.settings(), p, order_id)
    data = serialize.order(db, order, p)
    db.commit()
    return {"order": data, "ready": not reasons, "reasons": reasons}


@router.post("/{order_id}/start")
def start(order_id: uuid.UUID, body: schemas.VersionIn = schemas.VersionIn(), p: Principal = Depends(current_principal),
          db: Session = Depends(get_db), idempotency_key: Optional[str] = Header(default=None)):
    return _action(db, p, idempotency_key, "start", order_id, body.model_dump(),
                   lambda: svc.start(db, runtime.settings(), p, order_id, body.expected_version))


@router.post("/{order_id}/complete")
def complete(order_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db),
             idempotency_key: Optional[str] = Header(default=None)):
    return _action(db, p, idempotency_key, "complete", order_id, {},
                   lambda: svc.operator_complete(db, runtime.settings(), p, order_id))


@router.post("/{order_id}/abort")
def abort(order_id: uuid.UUID, body: schemas.AbortIn, p: Principal = Depends(current_principal),
          db: Session = Depends(get_db), idempotency_key: Optional[str] = Header(default=None)):
    return _action(db, p, idempotency_key, "abort", order_id, body.model_dump(),
                   lambda: svc.operator_abort(db, runtime.settings(), p, order_id, body.reason))


@router.post("/{order_id}/report", status_code=201)
def report(order_id: uuid.UUID, body: schemas.ReportIn, p: Principal = Depends(current_principal),
           db: Session = Depends(get_db)):
    svc.submit_report(db, p, order_id, body.model_dump())
    db.commit()
    return {"ok": True}


@router.post("/{order_id}/receipt")
def receipt(order_id: uuid.UUID, body: schemas.ReceiptIn, p: Principal = Depends(current_principal),
            db: Session = Depends(get_db), idempotency_key: Optional[str] = Header(default=None)):
    return _action(db, p, idempotency_key, "receipt", order_id, body.model_dump(),
                   lambda: svc.confirm_receipt(db, p, order_id, body.received, body.comment))


@router.post("/{order_id}/dispute", status_code=201)
def dispute(order_id: uuid.UUID, body: schemas.DisputeIn, p: Principal = Depends(current_principal),
            db: Session = Depends(get_db)):
    c = svc.open_dispute(db, runtime.settings(), p, order_id, body.description)
    db.commit()
    return {"complaint_id": c.id}


@router.post("/{order_id}/dispute/review")
def dispute_review(order_id: uuid.UUID, body: schemas.DisputeReviewIn, p: Principal = Depends(current_principal),
                   db: Session = Depends(get_db)):
    """Координатор заказа удовлетворяет (передаёт администратору) или отклоняет претензию."""
    c = complaint_svc.coordinator_review(db, p, order_id, upheld=body.upheld, comment=body.comment)
    db.commit()
    return {"complaint_id": c.id, "status": c.status, "coordinator_verdict": c.coordinator_verdict}


@router.post("/{order_id}/appeal", status_code=201)
def appeal(order_id: uuid.UUID, body: schemas.DisputeIn, p: Principal = Depends(current_principal),
           db: Session = Depends(get_db)):
    c = complaint_svc.create_appeal(db, p, order_id, body.description)
    db.commit()
    return {"complaint_id": c.id}


@router.post("/{order_id}/review")
def review(order_id: uuid.UUID, body: schemas.ReviewIn, p: Principal = Depends(current_principal),
           db: Session = Depends(get_db), idempotency_key: Optional[str] = Header(default=None)):
    return _action(db, p, idempotency_key, "review", order_id, body.model_dump(),
                   lambda: svc.admin_review(db, runtime.settings(), p, order_id, approve=body.approve,
                                            comment=body.comment, video_verified=body.video_verified,
                                            expected_version=body.expected_version))


@router.post("/{order_id}/coordinator")
def assign_coordinator(order_id: uuid.UUID, body: schemas.AssignCoordinatorIn,
                       p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    order = svc.assign_coordinator(db, p, order_id, body.coordinator_id)
    data = serialize.order(db, order, p)
    db.commit()
    return data


@router.post("/{order_id}/notes", status_code=201)
def add_note(order_id: uuid.UUID, body: schemas.NoteIn, p: Principal = Depends(current_principal),
             db: Session = Depends(get_db)):
    note = svc.add_note(db, p, order_id, body.kind, body.body, body.severity)
    db.commit()
    return {"id": note.id}
