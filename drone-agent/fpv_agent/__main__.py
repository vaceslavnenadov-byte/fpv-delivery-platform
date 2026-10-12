"""Запуск сервисного модуля с симулятором:

    FPV_DRONE_KEY_FILE=/etc/fpv-agent/key python -m fpv_agent --server https://fpv.example --scenario normal

Ключ устройства передаётся только через файл или переменную окружения
(FPV_DRONE_KEY_FILE / FPV_DRONE_KEY), но не аргументом командной строки.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .adapters import SimulatedDrone, SimulatorScenario
from .agent import ServerClient, ServiceModule


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
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    adapter = SimulatedDrone(SimulatorScenario.preset(args.scenario), serial=args.serial)
    client = ServerClient(args.server, _read_key(), ca_file=args.ca_file)
    ServiceModule(adapter, client, Path(args.state_dir), auto_complete=args.auto_complete).run(args.duration)
    return 0


if __name__ == "__main__":
    sys.exit(main())
