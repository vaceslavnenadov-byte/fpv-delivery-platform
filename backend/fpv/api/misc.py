"""Видео (выдача клипов), баллы оператора, проверка состояния сервиса."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import runtime
from ..services import scoring, video
from ..services.identity import Principal
from .deps import current_principal, get_db, require

router = APIRouter(tags=["misc"])


@router.get("/api/v1/videos/{evidence_id}/content")
def video_content(evidence_id: uuid.UUID, variant: str = Query("preview", pattern="^(preview|original)$"),
                  p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    body, content_type = video.stream(db, p, evidence_id, variant)
    db.commit()     # факт просмотра фиксируется до выдачи содержимого
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if variant == "original":
        headers["Content-Disposition"] = f'attachment; filename="evidence-{evidence_id}.ts"'
    return StreamingResponse(body, media_type=content_type, headers=headers)


@router.get("/api/v1/scores/me")
def my_scores(p: Principal = Depends(require("score.read_own")), db: Session = Depends(get_db)):
    return scoring.operator_summary(db, p.user_id)


@router.get("/health/live")
def live():
    return {"status": "ok"}


@router.get("/health/ready")
def ready(db: Session = Depends(get_db)):
    checks = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:  # pragma: no cover
        checks["database"] = "fail"
    try:
        runtime.directory().ping()
        checks["directory"] = "ok"
    except Exception:
        checks["directory"] = "fail"
    try:
        runtime.store().ping()
        checks["storage"] = "ok"
    except Exception:  # pragma: no cover
        checks["storage"] = "fail"
    from fastapi.responses import JSONResponse
    ok = checks["database"] == "ok" and checks["storage"] == "ok"
    # Недоступность каталога не делает сервис «неготовым»: активные сессии продолжают работать.
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ok" if ok else "fail", "checks": checks})
