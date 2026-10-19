"""REST API квалификации операторов (лицензии на типы БПЛА)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..errors import ValidationFailed
from ..services import licenses
from ..services.identity import Principal
from . import schemas
from .deps import current_principal, get_db

router = APIRouter(tags=["licenses"])


async def _body(request: Request) -> bytes:
    limit = licenses.SCAN_MAX
    if int(request.headers.get("content-length") or 0) > limit:
        raise ValidationFailed("Скан больше 15 МБ", code="attachment_too_large")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise ValidationFailed("Скан больше 15 МБ", code="attachment_too_large")
    return bytes(data)


@router.get("/api/v1/me/licenses")
def my_licenses(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"items": [licenses.serialize(db, x) for x in licenses.mine(db, p)], "statuses": licenses.STATUS_LABELS}


@router.post("/api/v1/me/licenses", status_code=201)
async def submit_license(request: Request, drone_model_id: str = Query(...), license_number: str = Query("", max_length=100),
                         issued_on: str = Query("", max_length=10), issued_by: str = Query("", max_length=300),
                         filename: str = Query("", max_length=200),
                         p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Тело запроса — скан лицензии (изображение или PDF); поля заявки — в строке запроса."""
    data = await _body(request)

    def store():
        lic = licenses.submit(db, p, {"drone_model_id": drone_model_id, "license_number": license_number,
                                      "issued_on": issued_on, "issued_by": issued_by}, data, filename)
        out = licenses.serialize(db, lic)
        db.commit()
        return out
    return await run_in_threadpool(store)


@router.put("/api/v1/me/licenses/{license_id}")
async def resubmit_license(license_id: int, request: Request, license_number: str = Query("", max_length=100),
                           issued_on: str = Query("", max_length=10), issued_by: str = Query("", max_length=300),
                           filename: str = Query("", max_length=200),
                           p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Исправленная заявка; пустое тело — скан прежний."""
    data = await _body(request)

    def store():
        lic = licenses.resubmit(db, p, license_id, {"license_number": license_number, "issued_on": issued_on,
                                                    "issued_by": issued_by}, data, filename)
        out = licenses.serialize(db, lic)
        db.commit()
        return out
    return await run_in_threadpool(store)


@router.get("/api/v1/licenses")
def license_requests(status: Optional[str] = Query(None, pattern="^(pending|fix|approved|rejected)$"),
                     p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"items": [licenses.serialize(db, x) for x in licenses.all_requests(db, p, status)],
            "statuses": licenses.STATUS_LABELS}


@router.get("/api/v1/licenses/{license_id}")
def license_card(license_id: int, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return licenses.serialize(db, licenses.visible(db, p, license_id))


@router.get("/api/v1/licenses/{license_id}/scan")
def license_scan(license_id: int, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    body, lic = licenses.scan(db, p, license_id)
    headers = {"Cache-Control": "private, max-age=600", "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"}
    if lic.scan_type.startswith("image/"):
        headers["Content-Security-Policy"] = "default-src 'none'"
    return StreamingResponse(body, media_type=lic.scan_type, headers=headers)


@router.post("/api/v1/licenses/{license_id}/decision")
def license_decision(license_id: int, body: schemas.LicenseDecisionIn, p: Principal = Depends(current_principal),
                     db: Session = Depends(get_db)):
    lic = licenses.decide(db, p, license_id, body.status, body.comment)
    out = licenses.serialize(db, lic)
    db.commit()
    return out
