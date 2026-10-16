"""Видеокамера дрона: захват с устройства V4L2 (/dev/videoN) в сегменты MPEG-TS по 1 с.

Камера подключена к компьютеру, на котором работает сервисный модуль (на дроне — бортовой
компьютер; на стенде — ВМ с проброшенной веб-камерой). Модуль только читает видео:
камера — устройство ввода, команд дрону через неё не передаётся.

Один процесс ffmpeg пишет сегменты в каталог; готовым считается любой сегмент, кроме
последнего (его ffmpeg ещё дописывает). При медленной отправке старые сегменты отбрасываются —
для трансляции важна актуальность.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("fpv_agent.camera")

MAX_BACKLOG = 3


class CameraSource:
    def __init__(self, device: str, size: str = "640x480", fps: int = 15, ffmpeg: Optional[str] = None) -> None:
        self.device = device
        self.size = size
        self.fps = fps
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg")
        self._proc: Optional[subprocess.Popen] = None
        self._dir: Optional[tempfile.TemporaryDirectory] = None
        self._warned = False

    def command(self, out_dir: Path) -> List[str]:
        return [self.ffmpeg or "ffmpeg", "-loglevel", "error", "-nostdin",
                "-f", "v4l2", "-framerate", str(self.fps), "-video_size", self.size, "-i", self.device,
                "-an", "-c:v", "mpeg2video", "-q:v", "5", "-g", str(self.fps),
                "-f", "segment", "-segment_time", "1", "-segment_format", "mpegts", "-reset_timestamps", "1",
                str(out_dir / "seg%08d.ts")]

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        if self.running:
            return True
        self.stop()
        if not self.ffmpeg:
            self._warn("ffmpeg не установлен — видео с камеры недоступно")
            return False
        if not Path(self.device).exists():
            self._warn(f"камера {self.device} не найдена")
            return False
        self._dir = tempfile.TemporaryDirectory(prefix="fpv-cam-")
        try:
            self._proc = subprocess.Popen(self.command(Path(self._dir.name)), stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError as exc:
            self._warn(f"не удалось запустить захват с {self.device}: {exc}")
            self.stop()
            return False
        log.info("захват видео с %s (%s, %d к/с)", self.device, self.size, self.fps)
        self._warned = False
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
                    log.warning("захват с %s завершился: %s", self.device, err[-300:])
            self._proc = None
        if self._dir is not None:
            self._dir.cleanup()
            self._dir = None

    def read_segment(self) -> Optional[bytes]:
        """Самый свежий готовый сегмент (или None). Перезапускает захват, если он прервался."""
        if not self.running and not self.start():
            return None
        assert self._dir is not None
        files = sorted(Path(self._dir.name).glob("seg*.ts"))
        ready = files[:-1]
        if not ready:
            return None
        if len(ready) > MAX_BACKLOG:          # отправка отстала — оставить только свежий
            for old in ready[:-1]:
                old.unlink(missing_ok=True)
            ready = ready[-1:]
        seg = ready[0]
        try:
            data = seg.read_bytes()
        finally:
            seg.unlink(missing_ok=True)
        return data or None

    def _warn(self, msg: str) -> None:
        if not self._warned:
            log.warning(msg)
            self._warned = True
