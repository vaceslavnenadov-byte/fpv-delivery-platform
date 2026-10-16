"""Video Evidence Service.

Кольцевой буфер: во время выполнения заказа сервисный модуль дрона передаёт
видеопоток сегментами (MPEG-TS, ~1 с). Сервер хранит в spool-каталоге только
сегменты, покрывающие последние ``video_buffer_seconds`` секунд (+ запас),
и постоянно вытесняет более старые.

При событии завершения (или прерывания) из буфера вырезается клип —
сегменты, пересекающиеся с интервалом [событие − 15 с, событие] по серверному
времени получения (не зависит от расхождения часов дрона). Клип склеивается
(MPEG-TS допускает побайтовую конкатенацию), хешируется SHA-256 в момент
захвата и асинхронно (outbox, с повторами) загружается в защищённое
хранилище. Если видеопотока нет — фиксируется отсутствие доказательства с
причиной; фиктивная запись не создаётся.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock, runtime
from ..config import Settings
from ..errors import Conflict, NotFound, ValidationFailed
from ..models import Order, VideoAccessLog, VideoEvidence
from . import audit, events
from .identity import Principal
from .storage import StorageError, sha256_file

log = logging.getLogger("fpv.video")
_SEG_RE = re.compile(r"^(\d{13})_(\d{1,7})_(\d{1,9})\.seg$")
ALLOWED_CONTENT_TYPES = ("video/mp2t", "application/octet-stream")
SPOOL_MARGIN_S = 5


@dataclass
class Segment:
    path: Path
    received_ms: int
    duration_ms: int
    seq: int

    @property
    def start_ms(self) -> int:
        return self.received_ms - self.duration_ms


def _order_dir(settings: Settings, order_id: uuid.UUID) -> Path:
    return Path(settings.video_spool_dir) / str(order_id)


def ring_dir(settings: Settings, order_id: uuid.UUID, source: str = "drone") -> Path:
    """Кольцевой буфер источника: ``drone`` — доказательная база, ``customer`` — камера заказчика."""
    base = _order_dir(settings, order_id)
    return base / "ring" if source == "drone" else base / source / "ring"


def open_buffer(settings: Settings, order_id: uuid.UUID, source: str = "drone") -> Path:
    path = ring_dir(settings, order_id, source)
    path.mkdir(parents=True, exist_ok=True, mode=0o750)
    return path


def _segments(settings: Settings, order_id: uuid.UUID, source: str = "drone") -> List[Segment]:
    ring = ring_dir(settings, order_id, source)
    if not ring.is_dir():
        return []
    result = []
    for p in ring.iterdir():
        m = _SEG_RE.match(p.name)
        if m:
            result.append(Segment(p, int(m.group(1)), int(m.group(2)), int(m.group(3))))
    result.sort(key=lambda s: (s.received_ms, s.seq))
    return result


def _ms(ts: dt.datetime) -> int:
    return int(ts.timestamp() * 1000)


def add_segment(settings: Settings, order: Order, seq: int, duration_ms: int, data: bytes,
                content_type: str) -> Dict[str, Any]:
    """Добавляет сегмент в кольцевой буфер заказа и вытесняет устаревшие."""
    if order.status != "in_progress":
        raise Conflict("Видеопоток принимается только во время выполнения заказа", code="order_not_in_progress")
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValidationFailed("Неподдерживаемый формат видеосегмента")
    if not data or len(data) > settings.video_max_segment_bytes:
        raise ValidationFailed("Недопустимый размер видеосегмента")
    if not (100 <= duration_ms <= 10000) or seq < 0:
        raise ValidationFailed("Недопустимые параметры видеосегмента")
    return write_segment(settings, order.id, "drone", seq, duration_ms, data,
                         keep_s=settings.video_buffer_seconds + SPOOL_MARGIN_S)


def write_segment(settings: Settings, order_id: uuid.UUID, source: str, seq: int, duration_ms: int,
                  data: bytes, *, keep_s: int) -> Dict[str, Any]:
    """Атомарная запись сегмента в кольцевой буфер источника и вытеснение устаревших."""
    ring = open_buffer(settings, order_id, source)
    received_ms = _ms(clock.now())
    if any(s.seq == seq for s in _segments(settings, order_id, source)):
        return {"accepted": False, "duplicate": True}
    name = f"{received_ms:013d}_{duration_ms}_{seq}.seg"
    fd, tmp = tempfile.mkstemp(dir=ring, prefix=".incoming-")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, ring / name)
    horizon = received_ms - keep_s * 1000
    evicted = 0
    for s in _segments(settings, order_id, source):
        if s.received_ms < horizon:
            s.path.unlink(missing_ok=True)
            evicted += 1
    return {"accepted": True, "duplicate": False, "evicted": evicted}


def buffer_state(settings: Settings, order_id: uuid.UUID, source: str = "drone") -> Dict[str, Any]:
    segs = _segments(settings, order_id, source)
    if not segs:
        return {"segments": 0, "covered_seconds": 0.0, "last_segment_age_s": None}
    covered = (segs[-1].received_ms - segs[0].start_ms) / 1000
    return {"segments": len(segs), "covered_seconds": round(covered, 1),
            "last_segment_age_s": round((_ms(clock.now()) - segs[-1].received_ms) / 1000, 1)}


def capture_clip(db: Session, settings: Settings, order: Order, event_at: dt.datetime, actor: audit.Actor,
                 trigger: str) -> VideoEvidence:
    """Фиксирует видеоподтверждение для события завершения. Идемпотентно (одна запись на заказ)."""
    existing = db.execute(select(VideoEvidence).where(VideoEvidence.order_id == order.id)).scalar_one_or_none()
    if existing is not None:
        return existing
    event_ms = _ms(event_at)
    window_start_ms = event_ms - settings.video_buffer_seconds * 1000
    segs = [s for s in _segments(settings, order.id)
            if s.received_ms <= event_ms and s.received_ms > window_start_ms]
    retention = clock.now() + dt.timedelta(days=settings.video_retention_days)
    if not segs:
        all_segs = _segments(settings, order.id)
        if not all_segs:
            reason = "Видеопоток не поступал во время выполнения заказа"
        else:
            reason = "Видеопоток прерван: нет данных за {} с до события завершения".format(
                settings.video_buffer_seconds)
        ev = VideoEvidence(order_id=order.id, status="missing", missing_reason=reason, completion_event_at=event_at,
                           retention_until=retention, created_at=clock.now())
        db.add(ev)
        db.flush()
        audit.record(db, actor, "video.missing", entity_type="order", entity_id=order.id,
                     details={"reason": reason, "trigger": trigger})
        _cleanup_ring(settings, order.id)
        return ev

    pending_dir = _order_dir(settings, order.id) / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
    clip_path = pending_dir / "clip.ts"
    h = hashlib.sha256()
    size = 0
    with open(clip_path, "wb") as out:
        for s in segs:
            with open(s.path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
                    out.write(chunk)
                    size += len(chunk)
        out.flush()
        os.fsync(out.fileno())
    clip_start_ms = max(segs[0].start_ms, window_start_ms)
    coverage = (segs[-1].received_ms - segs[0].start_ms) / 1000
    meta = {"segments": [{"seq": s.seq, "received_ms": s.received_ms, "duration_ms": s.duration_ms} for s in segs],
            "event_ms": event_ms, "coverage_seconds": coverage, "trigger": trigger}
    (pending_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    ev = VideoEvidence(order_id=order.id, status="pending_upload", sha256=h.hexdigest(), size_bytes=size,
                       content_type="video/mp2t", completion_event_at=event_at,
                       clip_start_at=dt.datetime.fromtimestamp(clip_start_ms / 1000, tz=dt.timezone.utc),
                       clip_end_at=dt.datetime.fromtimestamp(segs[-1].received_ms / 1000, tz=dt.timezone.utc),
                       segment_count=len(segs), retention_until=retention, created_at=clock.now())
    db.add(ev)
    db.flush()
    audit.record(db, actor, "video.captured", entity_type="order", entity_id=order.id,
                 details={"evidence_id": ev.id, "sha256": ev.sha256, "segments": len(segs),
                          "coverage_seconds": coverage, "trigger": trigger})
    events.publish(db, "video.upload", {"evidence_id": str(ev.id), "order_id": str(order.id)},
                   roles=["admin"], max_attempts=10)
    _cleanup_ring(settings, order.id)
    return ev


def _cleanup_ring(settings: Settings, order_id: uuid.UUID) -> None:
    shutil.rmtree(_order_dir(settings, order_id) / "ring", ignore_errors=True)


def _object_key(ev: VideoEvidence, suffix: str) -> str:
    ts = ev.completion_event_at
    return f"evidence/{ts:%Y}/{ts:%m}/{ev.order_id}/{ev.id}{suffix}"


def _make_preview(settings: Settings, src: Path) -> Optional[Path]:
    """MP4-копия для просмотра в браузере (оригинал MPEG-TS остаётся доказательством)."""
    ffmpeg = shutil.which(settings.ffmpeg_path) if not os.path.isabs(settings.ffmpeg_path) else settings.ffmpeg_path
    if not ffmpeg:
        return None
    dst = src.with_name("preview.mp4")
    try:
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-c", "copy",
                        "-movflags", "+faststart", str(dst)], check=True, timeout=120,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("preview generation failed: %s", exc)
        return None
    return dst if dst.is_file() and dst.stat().st_size > 0 else None


@events.handler("video.upload", name="video.upload")
def upload_clip(db: Session, payload: Dict[str, Any], event_id: uuid.UUID) -> None:
    settings = runtime.settings()
    store = runtime.store()
    ev = db.execute(select(VideoEvidence).where(VideoEvidence.id == uuid.UUID(payload["evidence_id"]))
                    .with_for_update()).scalar_one_or_none()
    if ev is None or ev.status != "pending_upload":
        return
    key = _object_key(ev, ".ts")
    pending_dir = _order_dir(settings, ev.order_id) / "pending"
    clip = pending_dir / "clip.ts"
    if clip.is_file():
        if sha256_file(clip) != ev.sha256:
            ev.status = "corrupted"
            audit.record(db, audit.Actor.system("video-service"), "video.integrity_failed", entity_type="order",
                         entity_id=ev.order_id, outcome="failure", details={"stage": "before_upload"})
            return
        if not store.exists(key):
            store.put_file(key, clip, "video/mp2t", retain_until=ev.retention_until)
        preview = _make_preview(settings, clip)
        if preview is not None and not store.exists(_object_key(ev, ".preview.mp4")):
            store.put_file(_object_key(ev, ".preview.mp4"), preview, "video/mp4", retain_until=ev.retention_until)
            ev.preview_object_key = _object_key(ev, ".preview.mp4")
    elif not store.exists(key):
        raise StorageError("Файл клипа в буфере не найден и объект отсутствует в хранилище")
    if store.sha256(key) != ev.sha256:
        raise StorageError("Контрольная сумма объекта в хранилище не совпадает")
    ev.object_key = key
    ev.status = "stored"
    ev.integrity_ok = True
    ev.integrity_checked_at = clock.now()
    db.flush()
    audit.record(db, audit.Actor.system("video-service"), "video.stored", entity_type="order",
                 entity_id=ev.order_id, details={"evidence_id": ev.id, "object_key": key, "sha256": ev.sha256})
    events.notify(db, "video.stored", {"order_id": ev.order_id}, roles=["admin"])
    shutil.rmtree(pending_dir, ignore_errors=True)


def evidence_for_order(db: Session, order_id: uuid.UUID) -> Optional[VideoEvidence]:
    return db.execute(select(VideoEvidence).where(VideoEvidence.order_id == order_id)).scalar_one_or_none()


def stream(db: Session, p: Principal, evidence_id: uuid.UUID, variant: str) -> tuple:
    """Выдаёт содержимое клипа уполномоченному сотруднику и фиксирует факт просмотра."""
    p.require("video.read")
    ev = db.get(VideoEvidence, evidence_id)
    if ev is None:
        raise NotFound("Видеоподтверждение не найдено")
    if ev.status != "stored":
        raise Conflict("Видео недоступно для просмотра (статус: {})".format(ev.status), code="video_unavailable")
    key = ev.preview_object_key if variant == "preview" and ev.preview_object_key else ev.object_key
    content_type = "video/mp4" if key and key.endswith(".mp4") else "video/mp2t"
    action = "view" if variant == "preview" else "download"
    db.add(VideoAccessLog(video_id=ev.id, user_id=p.user_id, action=action, details={"variant": variant},
                          created_at=clock.now()))
    audit.record(db, p.actor, f"video.{action}", entity_type="order", entity_id=ev.order_id,
                 details={"evidence_id": ev.id})
    try:
        body: Iterator[bytes] = runtime.store().open(key)
    except StorageError as exc:
        raise NotFound("Объект видео не найден в хранилище") from exc
    return body, content_type


def verify_integrity(db: Session, ev: VideoEvidence, actor: audit.Actor,
                     user_id: Optional[uuid.UUID] = None) -> bool:
    if ev.status not in ("stored", "corrupted"):
        return False
    actual = runtime.store().sha256(ev.object_key) if ev.object_key else None
    ok = actual == ev.sha256
    ev.integrity_ok = ok
    ev.integrity_checked_at = clock.now()
    if not ok and ev.status == "stored":
        ev.status = "corrupted"
    db.add(VideoAccessLog(video_id=ev.id, user_id=user_id, action="integrity_check",
                          details={"ok": ok}, created_at=clock.now()))
    audit.record(db, actor, "video.integrity_check", entity_type="order", entity_id=ev.order_id,
                 outcome="success" if ok else "failure", details={"evidence_id": ev.id, "ok": ok})
    return ok


def review(db: Session, p: Principal, order_id: uuid.UUID, *, approved: bool, comment: str) -> VideoEvidence:
    p.require("video.read")
    p.require("order.review")
    ev = evidence_for_order(db, order_id)
    if ev is None:
        raise NotFound("Видеоподтверждение отсутствует")
    if ev.status != "stored":
        if approved:
            raise Conflict("Нельзя подтвердить видео, которое не сохранено (статус: {})".format(ev.status),
                           code="video_unavailable")
        return ev
    ev.review_status = "approved" if approved else "rejected"
    ev.reviewed_by = p.user_id
    ev.reviewed_at = clock.now()
    ev.review_comment = comment
    db.add(VideoAccessLog(video_id=ev.id, user_id=p.user_id, action="review",
                          details={"approved": approved}, created_at=clock.now()))
    audit.record(db, p.actor, "video.reviewed", entity_type="order", entity_id=order_id,
                 details={"evidence_id": ev.id, "approved": approved, "comment": comment})
    return ev


def purge_expired(db: Session) -> int:
    """Удаление объектов по истечении срока хранения; запись о видео сохраняется (status='purged')."""
    n = 0
    rows = list(db.execute(select(VideoEvidence).where(VideoEvidence.status == "stored",
                                                       VideoEvidence.retention_until < clock.now())).scalars())
    for ev in rows:
        for key in (ev.object_key, ev.preview_object_key):
            if key:
                runtime.store().delete(key)
        ev.status = "purged"
        audit.record(db, audit.Actor.system("retention"), "video.purged", entity_type="order",
                     entity_id=ev.order_id, details={"evidence_id": ev.id})
        n += 1
    return n
