"""Видео дрона: источники и захват в сегменты MPEG-TS по 1 с.

Источники (идентификатор — как их видит платформа):

* ``usb:videoN`` — камеры V4L2 (USB-камера, плата захвата аналогового FPV-приёмника и т.п.);
* ``rtsp:<имя>`` — RTSP-потоки IP-камер из ``/etc/fpv-agent/cameras.conf`` (строки ``имя=rtsp://…``;
  адреса с паролями остаются на модуле, платформе сообщаются только имена);
* ``file:<имя файла>`` — тестовые ролики из папки модуля (``/var/lib/fpv-agent/videos``),
  воспроизводятся по кругу в реальном темпе;
* ``test`` — тестовая таблица ffmpeg.

Список источников модуль отправляет на сервер; оператор выбирает один из них, выбор приходит
со справкой о задании. Источники только читаются; команд дрону через них не передаётся.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("fpv_agent.video")

MAX_BACKLOG = 3
VIDEO_EXT = (".mp4", ".mkv", ".webm", ".mov", ".avi", ".ts", ".m4v")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,96}$")
RTSP_RE = re.compile(r"^rtsps?://[^\s'\"]{3,500}$")


@dataclass
class Source:
    id: str
    kind: str          # usb | rtsp | file | test
    label: str
    target: str = ""   # устройство, адрес или путь — остаётся на модуле

    def public(self) -> Dict[str, str]:
        return {"id": self.id, "kind": self.kind, "label": self.label}


def discover(video_dir: Path, cameras_file: Path, sys_v4l: Path = Path("/sys/class/video4linux"),
             dev: Path = Path("/dev")) -> List[Source]:
    out: List[Source] = []
    # USB / V4L2: только основной узел устройства (index 0); остальные узлы — метаданные.
    if sys_v4l.is_dir():
        for node in sorted(sys_v4l.iterdir(), key=lambda p: (len(p.name), p.name)):
            if not re.fullmatch(r"video\d+", node.name):
                continue
            try:
                if (node / "index").read_text().strip() != "0":
                    continue
                name = (node / "name").read_text().strip()
            except OSError:
                name = ""
            if (dev / node.name).exists():
                out.append(Source(f"usb:{node.name}", "usb", f"{name or 'Камера'} ({node.name})", str(dev / node.name)))
    # RTSP: из конфигурации модуля
    try:
        lines = cameras_file.read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, url = (x.strip() for x in line.split("=", 1))
        if any(s.id == f"rtsp:{name}" for s in out):
            log.warning("cameras.conf: повтор имени %r — используется первая строка", name[:40])
            continue
        if NAME_RE.match(name) and RTSP_RE.match(url):
            out.append(Source(f"rtsp:{name}", "rtsp", f"RTSP: {name}", url))
        else:
            log.warning("cameras.conf: пропущена строка с именем %r (ожидается имя=rtsp://…)", name[:40])
    # Тестовые ролики
    if video_dir.is_dir():
        for f in sorted(video_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in VIDEO_EXT and NAME_RE.match(f.name):
                out.append(Source(f"file:{f.name}", "file", f"Ролик: {f.name}", str(f)))
    out.append(Source("test", "test", "Тестовая таблица"))
    return out


class Segmenter:
    """Один процесс ffmpeg: источник → сегменты MPEG-TS по 1 с в рабочем каталоге."""

    def __init__(self, source: Source, size: str, fps: int, ffmpeg: str, label: str = "") -> None:
        self.source, self.size, self.fps, self.ffmpeg, self.label = source, size, fps, ffmpeg, label
        self._proc: Optional[subprocess.Popen] = None
        self._dir: Optional[tempfile.TemporaryDirectory] = None
        self.started_at = 0.0

    def input_args(self) -> List[str]:
        s = self.source
        if s.kind == "usb":
            return ["-f", "v4l2", "-framerate", str(self.fps), "-video_size", self.size, "-i", s.target]
        if s.kind == "rtsp":
            return ["-rtsp_transport", "tcp", "-timeout", "5000000", "-i", s.target]
        if s.kind == "file":
            return ["-re", "-stream_loop", "-1", "-i", s.target]
        return ["-re", "-f", "lavfi", "-i", f"testsrc=size={self.size}:rate={self.fps}"]

    def command(self, out_dir: Path) -> List[str]:
        w, h = self.size.split("x")
        vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={self.fps}"
        if self.label:
            vf += f",drawtext=text='{self.label}':x=10:y=10:fontcolor=white:box=1:boxcolor=black@0.5"
        return [self.ffmpeg, "-loglevel", "error", "-nostdin", *self.input_args(),
                "-an", "-vf", vf, "-c:v", "mpeg2video", "-q:v", "5", "-g", str(self.fps),
                "-f", "segment", "-segment_time", "1", "-segment_format", "mpegts", "-reset_timestamps", "1",
                str(out_dir / "seg%08d.ts")]

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        self.stop()
        self._dir = tempfile.TemporaryDirectory(prefix="fpv-video-")
        try:
            self._proc = subprocess.Popen(self.command(Path(self._dir.name)), stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError as exc:
            log.warning("не удалось запустить видео %s: %s", self.source.id, exc)
            self.stop()
            return False
        self.started_at = time.monotonic()
        log.info("видео: %s (%s, %d к/с)", self.source.id, self.size, self.fps)
        return True

    def stop(self) -> None:
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            elif self._proc.returncode and self._proc.stderr is not None:
                err = self._proc.stderr.read().decode(errors="replace").strip()
                if err:
                    log.warning("видео %s остановилось: %s", self.source.id, err[-300:])
            self._proc = None
        if self._dir is not None:
            self._dir.cleanup()
            self._dir = None

    def read_segment(self) -> Optional[bytes]:
        if self._dir is None:
            return None
        files = sorted(Path(self._dir.name).glob("seg*.ts"))
        ready = files[:-1] if self.running else files    # последний сегмент ещё дописывается
        if len(ready) > MAX_BACKLOG:                      # отправка отстала — оставить только свежий
            for old in ready[:-1]:
                old.unlink(missing_ok=True)
            ready = ready[-1:]
        if not ready:
            return None
        seg = ready[0]
        try:
            data = seg.read_bytes()
        finally:
            seg.unlink(missing_ok=True)
        return data or None


class VideoManager:
    """Выбор и переключение источника видео дрона."""

    RESCAN_S = 30.0
    RETRY_S = 5.0

    def __init__(self, video_dir: Path, cameras_file: Path, default: str = "test",
                 size: str = "640x480", fps: int = 15, ffmpeg: Optional[str] = None, label: str = "",
                 sys_v4l: Path = Path("/sys/class/video4linux"), dev: Path = Path("/dev")) -> None:
        self.video_dir, self.cameras_file = video_dir, cameras_file
        self.default, self.size, self.fps = default, size, fps
        self.ffmpeg = ffmpeg if ffmpeg is not None else shutil.which("ffmpeg")
        self.label = label
        self._sys_v4l, self._dev = sys_v4l, dev
        self.sources: List[Source] = []
        self.selected: Optional[str] = None          # выбор оператора (с сервера)
        self._seg: Optional[Segmenter] = None
        self._scanned = 0.0
        self._failed_at = 0.0
        self.rescan(force=True)

    # --- список источников ---
    def rescan(self, force: bool = False) -> bool:
        """Обновить список; True — если он изменился."""
        now = time.monotonic()
        if not force and now - self._scanned < self.RESCAN_S:
            return False
        self._scanned = now
        found = discover(self.video_dir, self.cameras_file, self._sys_v4l, self._dev)
        changed = [s.public() for s in found] != [s.public() for s in self.sources]
        self.sources = found
        return changed

    def inventory(self) -> Dict[str, object]:
        return {"sources": [s.public() for s in self.sources], "active": self.active_id}

    @property
    def wanted(self) -> Optional[Source]:
        by_id = {s.id: s for s in self.sources}
        for sid in (self.selected, self.default, "test"):
            if sid and sid in by_id:
                return by_id[sid]
        return None

    @property
    def active_id(self) -> Optional[str]:
        return self._seg.source.id if self._seg and self._seg.running else None

    def select(self, source_id: Optional[str]) -> None:
        if source_id != self.selected:
            self.selected = source_id
            self._failed_at = 0.0
            if source_id and source_id not in {s.id for s in self.sources}:
                self.rescan(force=True)        # мог появиться новый ролик или камера

    # --- захват ---
    def read_segment(self) -> Optional[bytes]:
        if not self.ffmpeg:
            return None
        want = self.wanted
        if want is None:
            return None
        if self._seg is None or self._seg.source.id != want.id or not self._seg.running:
            data = self._seg.read_segment() if self._seg and self._seg.source.id == want.id else None
            if data:
                return data
            if self._seg is not None and self._seg.source.id == want.id and self._failed_at \
                    and time.monotonic() - self._failed_at < self.RETRY_S:
                return None
            self.stop()
            self._seg = Segmenter(want, self.size, self.fps, self.ffmpeg, self.label)
            if not self._seg.start():
                self._failed_at = time.monotonic()
                return None
            self._failed_at = time.monotonic()      # если процесс сразу упадёт — повтор не чаще RETRY_S
            return None
        return self._seg.read_segment()

    def stop(self) -> None:
        if self._seg is not None:
            self._seg.stop()
            self._seg = None
