"""Запуск сервисного модуля с симулятором:

    FPV_DRONE_KEY_FILE=/etc/fpv-agent/key python -m fpv_agent --server https://fpv.example --scenario normal

Ключ устройства передаётся только через файл или переменную окружения
(FPV_DRONE_KEY_FILE / FPV_DRONE_KEY), но не аргументом командной строки.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

from .adapters import SimulatedDrone, SimulatorScenario
from .agent import ServerClient, ServiceModule
from .video import VideoManager


def _read_key() -> str:
    path = os.environ.get("FPV_DRONE_KEY_FILE")
    if path:
        p = Path(path)
        if p.stat().st_mode & 0o077:
            print("ОШИБКА: файл ключа доступен другим пользователям (нужны права 600)", file=sys.stderr)
            sys.exit(2)
        return p.read_text().strip()
    key = os.environ.get("FPV_DRONE_KEY", "")
    if not key:
        print("ОШИБКА: задайте FPV_DRONE_KEY_FILE или FPV_DRONE_KEY", file=sys.stderr)
        sys.exit(2)
    return key


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Сервисный модуль дрона (симулятор)")
    ap.add_argument("--server", required=True)
    ap.add_argument("--scenario", default="normal")
    ap.add_argument("--serial", default="SIM-0001")
    ap.add_argument("--state-dir", default=os.path.expanduser("~/.fpv-agent"))
    ap.add_argument("--ca-file")
    ap.add_argument("--duration", type=float, help="ограничить время работы, с")
    ap.add_argument("--auto-complete", action="store_true",
                    help="отправлять событие завершения при прибытии в точку Б")
    ap.add_argument("--video-device", default=os.environ.get("FPV_AGENT_VIDEO_DEVICE", ""),
                    help="источник видео по умолчанию: /dev/videoN; пусто — тестовая таблица "
                         "(оператор может выбрать другой источник в мониторе полёта)")
    ap.add_argument("--video-dir", default=os.environ.get("FPV_AGENT_VIDEO_DIR", ""),
                    help="папка тестовых роликов (по умолчанию <state-dir>/videos)")
    ap.add_argument("--cameras-file", default=os.environ.get("FPV_AGENT_CAMERAS_FILE", "/etc/fpv-agent/cameras.conf"),
                    help="RTSP-камеры: строки имя=rtsp://…")
    ap.add_argument("--video-size", default=os.environ.get("FPV_AGENT_VIDEO_SIZE", "640x480"))
    ap.add_argument("--video-fps", type=int, default=int(os.environ.get("FPV_AGENT_VIDEO_FPS", "15") or 15))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.video_device and not re.fullmatch(r"/dev/video\d+", args.video_device):
        print("ОШИБКА: --video-device: ожидается /dev/videoN", file=sys.stderr)
        return 2
    if not re.fullmatch(r"\d{2,4}x\d{2,4}", args.video_size) or not 1 <= args.video_fps <= 60:
        print("ОШИБКА: --video-size ШИРИНАxВЫСОТА, --video-fps 1..60", file=sys.stderr)
        return 2
    state_dir = Path(args.state_dir)
    video = VideoManager(Path(args.video_dir) if args.video_dir else state_dir / "videos", Path(args.cameras_file),
                         default="usb:" + Path(args.video_device).name if args.video_device else "test",
                         size=args.video_size, fps=args.video_fps)
    adapter = SimulatedDrone(SimulatorScenario.preset(args.scenario), serial=args.serial, video=video)
    client = ServerClient(args.server, _read_key(), ca_file=args.ca_file)
    ServiceModule(adapter, client, state_dir, auto_complete=args.auto_complete).run(args.duration)
    return 0


if __name__ == "__main__":
    sys.exit(main())
