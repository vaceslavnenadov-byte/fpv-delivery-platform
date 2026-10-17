"""REST API техподдержки."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..errors import ValidationFailed
from ..services import support
from ..services.identity import Principal
from . import schemas
from .deps import current_principal, get_db

router = APIRouter(prefix="/api/v1/support", tags=["support"])


@router.get("/meta")
def meta(p: Principal = Depends(current_principal)):
    p.require("support.use")
    return {"reasons": support.REASONS, "statuses": support.STATUSES,
            "limits": {"image_mb": support.IMAGE_MAX // 2 ** 20, "video_mb": support.VIDEO_MAX // 2 ** 20}}


@router.get("/tickets")
def my_tickets(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"items": [support.serialize_ticket(db, t, p.user_id) for t in support.list_mine(db, p)]}


@router.get("/admin/tickets")
def all_tickets(status: Optional[str] = None, reason: Optional[str] = None, q: Optional[str] = None,
                date_from: Optional[dt.date] = None, date_to: Optional[dt.date] = None,
                p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    to = (date_to + dt.timedelta(days=1)) if date_to else None
    items = support.list_all(db, p, status=status, reason=reason, q=q, date_from=date_from, date_to=to)
    return {"items": [support.serialize_ticket(db, t, p.user_id) for t in items], "counts": support.counts(db, p)}


@router.post("/tickets", status_code=201)
def create_ticket(body: schemas.SupportTicketIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    t = support.create(db, p, body.model_dump())
    db.commit()
    return support.serialize_ticket(db, t, p.user_id)


@router.get("/tickets/{ticket_id}")
def ticket(ticket_id: int, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    data = support.detail(db, p, ticket_id)
    db.commit()                                 # отметка «прочитано»
    return data


@router.post("/tickets/{ticket_id}/messages", status_code=201)
def message(ticket_id: int, body: schemas.SupportMessageIn, p: Principal = Depends(current_principal),
            db: Session = Depends(get_db)):
    m = support.post_message(db, p, ticket_id, body.body)
    db.commit()
    return {"id": m.id}


@router.post("/tickets/{ticket_id}/files", status_code=201)
async def upload(ticket_id: int, request: Request, filename: str = Query("", max_length=200),
                 comment: str = Query("", max_length=2000), p: Principal = Depends(current_principal),
                 db: Session = Depends(get_db)):
    """Тело запроса — файл (фото или видео); подпись и имя файла — в строке запроса."""
    limit = support.VIDEO_MAX
    if int(request.headers.get("content-length") or 0) > limit:
        raise ValidationFailed(f"Файл больше {limit // 2 ** 20} МБ", code="attachment_too_large")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise ValidationFailed(f"Файл больше {limit // 2 ** 20} МБ", code="attachment_too_large")
    if not data:
        raise ValidationFailed("Пустой файл")
    from fastapi.concurrency import run_in_threadpool

    def store():
        m = support.add_attachment(db, p, ticket_id, bytes(data), filename, comment)
        db.commit()
        return {"id": m.id}
    return await run_in_threadpool(store)


@router.get("/attachments/{attachment_id}")
def attachment(attachment_id: int, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    body, a = support.attachment(db, p, attachment_id)
    return StreamingResponse(body, media_type=a.content_type, headers={
        "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'", "Content-Length": str(a.size_bytes),
        "Content-Disposition": "inline"})


@router.post("/tickets/{ticket_id}/status")
def status(ticket_id: int, body: schemas.SupportStatusIn, p: Principal = Depends(current_principal),
           db: Session = Depends(get_db)):
    t = support.set_status(db, p, ticket_id, body.status)
    db.commit()
    return support.serialize_ticket(db, t, p.user_id)


@router.post("/tickets/{ticket_id}/participants")
def participant(ticket_id: int, body: schemas.SupportParticipantIn, p: Principal = Depends(current_principal),
                db: Session = Depends(get_db)):
    support.add_participant(db, p, ticket_id, body.user_id)
    db.commit()
    return {"ok": True}


@router.get("/users")
def users(q: str = Query("", max_length=100), p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"items": support.find_users(db, p, q)}
