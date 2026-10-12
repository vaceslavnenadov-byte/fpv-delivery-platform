"""Симулятор дрона для тестирования без оборудования.

Имитирует: подключение, готовность, телеметрию, качество связи, полёт по
маршруту задания (как модель, а не управление), видеопоток, ошибки, потерю
и восстановление соединения. Через симулятор невозможно управлять реальным
оборудованием: он не открывает никаких аппаратных интерфейсов.
"""
from __future__ import annotations

import math
import os
import random
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import DroneAdapter, MissionInfo

HOME = {"lat": 55.7500, "lon": 37.6000}


@dataclass
class SimulatorScenario:
    """Сценарий имитации. Время — секунды от начала выполнения задания."""
    name: str = "normal"
    cruise_speed_mps: float = 12.0
    battery_drain_per_s: float = 0.05
    link_loss_at: Optional[float] = None
    link_loss_duration: float = 0.0
    critical_error_at: Optional[float] = None
    video_enabled: bool = True
    self_check_fail: bool = False

    @classmethod
    def preset(cls, name: str) -> "SimulatorScenario":
        presets = {
            "normal": cls(),
            "fast": cls(name="fast", cruise_speed_mps=60.0),
            "link-loss": cls(name="link-loss", link_loss_at=8.0, link_loss_duration=40.0),
            "link-blip": cls(name="link-blip", link_loss_at=8.0, link_loss_duration=12.0),
            "critical-error": cls(name="critical-error", critical_error_at=10.0),
            "no-video": cls(name="no-video", video_enabled=False),
            "faulty": cls(name="faulty", self_check_fail=True),
        }
        if name not in presets:
            raise ValueError(f"Неизвестный сценарий: {name}. Доступны: {', '.join(presets)}")
        return presets[name]


class SimulatedDrone(DroneAdapter):
    def __init__(self, scenario: Optional[SimulatorScenario] = None, serial: str = "SIM-0001",
                 rng: Optional[random.Random] = None, clock=time.monotonic) -> None:
        self.scenario = scenario or SimulatorScenario()
        self.serial = serial
        self.rng = rng or random.Random(42)
        self.clock = clock          # подменяется в тестах для ускоренной имитации
        self.connected = False
        self.battery = 98.0
        self.position = dict(HOME)
        self.alt = 0.0
        self.phase = "idle"
        self.mission: Optional[MissionInfo] = None
        self.mission_started: Optional[float] = None
        self._last_tick = self.clock()
        self._events: List[Dict[str, Any]] = []
        self._error_sent = False
        self._ffmpeg = shutil.which("ffmpeg")
        self._video_seq = 0

    # --- подключение ---
    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def _elapsed(self) -> float:
        return self.clock() - self.mission_started if self.mission_started else 0.0

    def link_available(self) -> bool:
        s = self.scenario
        if s.link_loss_at is None or self.mission_started is None:
            return True
        t = self._elapsed()
        return not (s.link_loss_at <= t < s.link_loss_at + s.link_loss_duration)

    # --- модель полёта (имитация, не управление) ---
    def on_mission_update(self, mission: MissionInfo) -> None:
        self.mission = mission
        if mission.active and mission.status == "in_progress" and self.mission_started is None:
            self.mission_started = self.clock()
            if mission.origin:
                self.position = {"lat": mission.origin["lat"], "lon": mission.origin["lon"]}
            self.phase = "takeoff"
        elif mission.active and mission.status == "ready":
            self.phase = "preflight"
        elif not mission.active and self.mission_started is not None:
            self.mission_started = None
            self.phase = "landed"
            self.alt = 0.0

    def _advance(self) -> None:
        now = self.clock()
        dt = now - self._last_tick
        self._last_tick = now
        if self.mission_started is None or not self.mission or not self.mission.destination:
            return
        self.battery = max(0.0, self.battery - self.scenario.battery_drain_per_s * dt)
        dest = self.mission.destination
        dlat = dest["lat"] - self.position["lat"]
        dlon = dest["lon"] - self.position["lon"]
        dist_m = math.hypot(dlat * 111_320, dlon * 111_320 * math.cos(math.radians(self.position["lat"])))
        step = self.scenario.cruise_speed_mps * dt
        if dist_m <= max(step, 1.0):
            self.position = {"lat": dest["lat"], "lon": dest["lon"]}
            if self.phase != "delivery":
                self.phase = "delivery"
            self.alt = max(0.0, self.alt - 5 * dt)
        else:
            frac = step / dist_m
            self.position["lat"] += dlat * frac
            self.position["lon"] += dlon * frac
            self.alt = min(80.0, self.alt + 4 * dt)
            self.phase = "cruise" if self.alt >= 30 else "takeoff"
        s = self.scenario
        if s.critical_error_at is not None and not self._error_sent and self._elapsed() >= s.critical_error_at:
            self._error_sent = True
            self._events.append({"kind": "error", "data": {"code": "SIM_MOTOR_OVERHEAT", "severity": "critical",
                                                            "message": "Имитация: перегрев двигателя"}})

    def read_telemetry(self) -> Dict[str, Any]:
        self._advance()
        link = self.rng.randint(80, 100) if self.link_available() else 0
        return {"lat": round(self.position["lat"], 6), "lon": round(self.position["lon"], 6),
                "alt_m": round(self.alt, 1),
                "ground_speed_mps": self.scenario.cruise_speed_mps if self.phase == "cruise" else 0.0,
                "heading_deg": 45.0, "battery_pct": int(self.battery), "link_quality": link, "gnss_fix": True,
                "flight_phase": self.phase}

    def self_check(self) -> Dict[str, Any]:
        if self.scenario.self_check_fail:
            return {"result": "fail", "error_codes": ["SIM_IMU_CALIBRATION"], "battery_pct": int(self.battery),
                    "firmware_version": "sim-1.0.0"}
        return {"result": "pass", "error_codes": [], "battery_pct": int(self.battery),
                "firmware_version": "sim-1.0.0", "sensors": {"imu": "ok", "gnss": "ok", "camera": "ok"}}

    def read_video_segment(self) -> Optional[bytes]:
        if not self.scenario.video_enabled or self.mission_started is None:
            return None
        self._video_seq += 1
        if self._ffmpeg:
            return self._ffmpeg_segment()
        # Без ffmpeg — синтетические байты в контейнере-заглушке (для проверки буфера и хранения).
        return b"SIMTS" + self._video_seq.to_bytes(4, "big") + os.urandom(2048)

    def _ffmpeg_segment(self) -> Optional[bytes]:
        clock_txt = time.strftime("%H:%M:%S").replace(":", "\\:")   # экранирование для drawtext
        text = "{} seg {} {}".format(self.serial, self._video_seq, clock_txt)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "seg.ts"
            cmd = [self._ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
                   "-t", "1", "-vf", f"drawtext=text='{text}':x=10:y=10:fontcolor=white:box=1:boxcolor=black",
                   "-c:v", "mpeg2video", "-f", "mpegts", str(out)]
            try:
                subprocess.run(cmd, check=True, timeout=10, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (subprocess.SubprocessError, OSError):
                cmd = [c for c in cmd if not c.startswith("drawtext")]
                idx = cmd.index("-vf")
                del cmd[idx:idx + 2]
                try:
                    subprocess.run(cmd, check=True, timeout=10, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except (subprocess.SubprocessError, OSError):
                    return None
            return out.read_bytes()

    def pending_events(self) -> List[Dict[str, Any]]:
        events, self._events = self._events, []
        return events

    @property
    def arrived(self) -> bool:
        return self.phase == "delivery"
