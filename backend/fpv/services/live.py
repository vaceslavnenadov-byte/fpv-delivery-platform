"""Прямая видеотрансляция выполняемого заказа (HLS).

Источник — те же сегменты MPEG-TS, что поступают от сервисного модуля дрона в кольцевой буфер
(``video.add_segment``). Для воспроизведения в браузере каждый сегмент перекодируется в H.264
(MPEG-TS) и складывается в ``<буфер>/live``; исходные сегменты — доказательная база — не
изменяются. Список воспроизведения — скользящее окно из последних ``live_window_segments``.

Порядковые номера HLS (EXT-X-MEDIA-SEQUENCE) назначаются сервером в порядке поступления, а не
берутся из номера сегмента устройства: после перезапуска модуля дрона нумерация устройства
начинается заново. Разрыв потока (потеря связи, перезапуск) помечается EXT-X-DISCONTINUITY.

Доступ: разрешение ``video.live`` и право видеть заказ (заказчик — свой заказ, оператор и
координатор — назначенный, администратор — любой). Начало просмотра фиксируется в аудите.

Источники: ``drone`` — камера дрона (во время выполнения заказа), ``customer`` — камера заказчика
(``services/customer_stream.py``, пока заказ в работе у оператора). Обработка одинаковая.
"""
from __future__ import annotations

import fcntl
import json
import math
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from .. import runtime
from ..config import Settings
from ..errors import Conflict, NotFound, ServiceUnavailable
from ..models import Order
from . import audit, video
from . import orders as order_svc
from .identity import Principal

GAP_TOLERANCE_MS = 1500       # больший промежуток между сегментами считается разрывом потока
DAY_MS = 86_400_000


def _ffmpeg(settings: Settings) -> Optional[str]:
    path = settings.ffmpeg_path
    return path if os.path.isabs(path) and os.access(path, os.X_OK) else shutil.which(path)


# Статусы заказа, в которых источник транслируется.
ACTIVE_STATUSES = {
    "drone": ("in_progress",),
    "customer": ("accepted", "awaiting_readiness", "ready", "in_progress", "awaiting_delivery_confirmation"),
}
URL_PREFIX = {"drone": "live", "customer": "live/customer"}


def live_order(db: Session, p: Principal, order_id: uuid.UUID, source: str = "drone") -> Order:
    p.require("video.live")
    order = order_svc.visible_order(db, p, order_id)      # 404 для чужих заказов
    if order.status not in ACTIVE_STATUSES[source]:
        raise Conflict("Трансляция доступна только во время выполнения заказа", code="live_not_active")
    return order


def _live_dir(settings: Settings, order_id: uuid.UUID, source: str = "drone") -> Path:
    # Внутри каталога кольцевого буфера: удаляется вместе с ним после завершения заказа.
    if source != "drone" and not video.ring_dir(settings, order_id, source).is_dir():
        # Трансляция заказчика остановлена (буфер удалён) — зрители не создают его заново.
        raise NotFound("Видеопоток пока не поступает", code="live_no_video")
    path = video.open_buffer(settings, order_id, source) / "live"
    path.mkdir(exist_ok=True, mode=0o750)
    return path


def _key(seg: video.Segment) -> str:
    return f"{seg.received_ms:013d}_{seg.seq}"


def _assign(live: Path, segments: List[video.Segment]) -> Dict[str, List[int]]:
    """Номер HLS и номер разрыва для каждого сегмента; согласовано между процессами API."""
    with open(live / ".lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state_path = live / "index.json"
        try:
            state = json.loads(state_path.read_text())
        except (OSError, ValueError):
            state = {"next": 0, "disc": 0, "last_end_ms": None, "map": {}}
        mapping = state["map"]
        for seg in segments:
            k = _key(seg)
            if k in mapping:
                continue
            last_end = state["last_end_ms"]
            if last_end is not None and (seg.start_ms - last_end > GAP_TOLERANCE_MS
                                         or seg.start_ms // DAY_MS != last_end // DAY_MS):
                state["disc"] += 1
            mapping[k] = [state["next"], state["disc"]]
            state["next"] += 1
            state["last_end_ms"] = seg.received_ms
        current = {_key(s) for s in segments}
        state["map"] = {k: v for k, v in mapping.items() if k in current}
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        os.replace(tmp, state_path)
        return state["map"]


def _transcode(settings: Settings, ffmpeg: str, seg: video.Segment, live: Path) -> Optional[Path]:
    """H.264-копия сегмента для браузера; результат кешируется, сбой запоминается."""
    out = live / f"{_key(seg)}.ts"
    bad = live / f"{_key(seg)}.bad"
    if out.is_file():
        return out
    if bad.exists() or not seg.path.is_file():
        return None
    fd, tmp = tempfile.mkstemp(dir=live, prefix=".part-", suffix=".ts")
    os.close(fd)
    offset = (seg.start_ms % DAY_MS) / 1000
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(seg.path), "-map", "0:v:0", "-an",
           "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-profile:v", "main", "-pix_fmt", "yuv420p",
           "-output_ts_offset", f"{offset:.3f}", "-f", "mpegts", tmp]
    try:
        subprocess.run(cmd, check=True, timeout=20, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE)
        if os.path.getsize(tmp) == 0:
            raise OSError("пустой результат")
        os.replace(tmp, out)
        return out
    except (subprocess.SubprocessError, OSError):
        bad.touch()
        return None
    finally:
        Path(tmp).unlink(missing_ok=True)


def _prune(live: Path, keep: set) -> None:
    for f in live.iterdir():
        if f.suffix in (".ts", ".bad", ".jpg") and not f.name.startswith(".") and f.stem not in keep:
            f.unlink(missing_ok=True)


def start(db: Session, p: Principal, order_id: uuid.UUID, source: str = "drone") -> dict:
    """Открытие трансляции: проверка доступа и запись в журнал аудита."""
    settings = runtime.settings()
    order = live_order(db, p, order_id, source)
    if not _ffmpeg(settings):
        raise ServiceUnavailable("Трансляция недоступна: на сервере не установлен ffmpeg", code="live_unavailable")
    audit.record(db, p.actor, "video.live.view", entity_type="order", entity_id=order.id,
                 details={"order_number": order.number, "source": source})
    base = f"/api/v1/orders/{order.id}/{URL_PREFIX[source]}"
    return {"playlist_url": f"{base}/playlist.m3u8", "frame_url": f"{base}/frame.jpg",
            "window_segments": settings.live_window_segments,
            "buffer": video.buffer_state(settings, order.id, source)}


def playlist(db: Session, p: Principal, order_id: uuid.UUID, source: str = "drone") -> str:
    order = live_order(db, p, order_id, source)
    return build_playlist(runtime.settings(), order.id, source)


def build_playlist(settings: Settings, key: uuid.UUID, source: str) -> str:
    """Плейлист HLS буфера ``key`` (заказ или — для проверки видео техником — дрон)."""
    ffmpeg = _ffmpeg(settings)
    if not ffmpeg:
        raise ServiceUnavailable("Трансляция недоступна: на сервере не установлен ffmpeg", code="live_unavailable")
    live = _live_dir(settings, key, source)
    segments = video._segments(settings, key, source)
    mapping = _assign(live, segments)
    window = segments[-settings.live_window_segments:]
    entries: List[Tuple[video.Segment, int, int]] = []
    for seg in window:
        index, disc = mapping[_key(seg)]
        if _transcode(settings, ffmpeg, seg, live) is None:
            entries = []          # окно — только непрерывный хвост из воспроизводимых сегментов
            continue
        entries.append((seg, index, disc))
    _prune(live, {_key(s) for s in segments})
    if not entries:
        raise NotFound("Видеопоток пока не поступает", code="live_no_video")
    target = max(1, math.ceil(max(s.duration_ms for s, _, _ in entries) / 1000))
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{target}",
             f"#EXT-X-MEDIA-SEQUENCE:{entries[0][1]}", f"#EXT-X-DISCONTINUITY-SEQUENCE:{entries[0][2]}"]
    prev_disc = entries[0][2]
    for seg, index, disc in entries:
        if disc != prev_disc:
            lines.append("#EXT-X-DISCONTINUITY")
            prev_disc = disc
        lines.append(f"#EXTINF:{seg.duration_ms / 1000:.3f},")
        lines.append(f"{_key(seg)}.ts")
    return "\n".join(lines) + "\n"


def segment(db: Session, p: Principal, order_id: uuid.UUID, name: str, source: str = "drone") -> Path:
    order = live_order(db, p, order_id, source)
    return segment_path(runtime.settings(), order.id, name, source)


def segment_path(settings: Settings, key: uuid.UUID, name: str, source: str) -> Path:
    live = _live_dir(settings, key, source)
    path = live / f"{name}.ts"
    if not path.is_file():
        raise NotFound("Сегмент трансляции не найден")
    return path


def frame(db: Session, p: Principal, order_id: uuid.UUID, source: str = "drone") -> Path:
    """Последний кадр потока (JPEG) — режим для браузеров без H.264 и медленных каналов."""
    order = live_order(db, p, order_id, source)
    return frame_path(runtime.settings(), order.id, source)


def frame_path(settings: Settings, key: uuid.UUID, source: str) -> Path:
    ffmpeg = _ffmpeg(settings)
    if not ffmpeg:
        raise ServiceUnavailable("Трансляция недоступна: на сервере не установлен ffmpeg", code="live_unavailable")
    live = _live_dir(settings, key, source)
    for seg in reversed(video._segments(settings, key, source)[-3:]):
        out = live / f"{_key(seg)}.jpg"
        if out.is_file():
            return out
        if (live / f"{_key(seg)}.bad").exists() or not seg.path.is_file():
            continue
        fd, tmp = tempfile.mkstemp(dir=live, prefix=".part-", suffix=".jpg")
        os.close(fd)
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(seg.path), "-map", "0:v:0",
               "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", "-q:v", "5", "-f", "image2", tmp]
        try:
            subprocess.run(cmd, check=True, timeout=10, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE)
            if os.path.getsize(tmp) == 0:
                raise OSError("пустой результат")
            os.replace(tmp, out)
            return out
        except (subprocess.SubprocessError, OSError):
            (live / f"{_key(seg)}.bad").touch()
        finally:
            Path(tmp).unlink(missing_ok=True)
    raise NotFound("Видеопоток пока не поступает", code="live_no_video")
