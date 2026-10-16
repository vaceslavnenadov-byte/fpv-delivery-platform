"""Сервисный модуль: связывает адаптер оборудования с контуром телеметрии сервера.

* телеметрия раз в секунду, при отсутствии связи накапливается в локальной
  очереди и досылается после восстановления (номера seq сохраняются на диске,
  сервер отбрасывает повторы);
* самодиагностика раз в минуту;
* видеосегменты (1 с) во время выполнения задания;
* опрос справочной информации о задании;
* переподключение с экспоненциальной задержкой.

Модуль не принимает от сервера команд управления полётом.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Deque, Dict, Optional

from .adapters.base import DroneAdapter, MissionInfo

log = logging.getLogger("fpv.agent")


class LinkDown(Exception):
    pass


class ServerClient:
    def __init__(self, base_url: str, api_key: str, ca_file: Optional[str] = None, timeout: float = 5.0) -> None:
        if not base_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ValueError("Подключение к серверу допускается только по HTTPS (кроме локальной отладки)")
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.timeout = timeout
        self.ctx = ssl.create_default_context(cafile=ca_file) if base_url.startswith("https://") else None

    def request(self, method: str, path: str, body: Any = None, *, raw: Optional[bytes] = None,
                headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        h = {"Authorization": f"Drone {self.key}", "User-Agent": "fpv-agent/0.1"}
        data = None
        if raw is not None:
            data = raw
            h["Content-Type"] = "video/mp2t"
        elif body is not None:
            data = json.dumps(body, default=str).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            try:
                detail = json.loads(payload)
            except ValueError:
                detail = {"raw": payload[:200].decode(errors="replace")}
            if exc.code in (401, 403):
                raise PermissionError(f"Сервер отклонил ключ устройства: {detail}") from exc
            return {"_status": exc.code, **detail}
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise LinkDown(str(exc)) from exc


class ServiceModule:
    def __init__(self, adapter: DroneAdapter, client: ServerClient, state_dir: Path,
                 auto_complete: bool = False, max_queue: int = 10000) -> None:
        self.adapter = adapter
        self.client = client
        self.state_file = state_dir / "agent-state.json"
        state_dir.mkdir(parents=True, exist_ok=True)
        self.seq = self._load_seq()
        self.queue: Deque[Dict[str, Any]] = collections.deque(maxlen=max_queue)
        self.mission = MissionInfo(active=False)
        self.auto_complete = auto_complete
        self.completed_sent: Optional[str] = None
        self.video_seq = 0
        self.backoff = 1.0
        self.online = True
        self._last_check = 0.0
        self._last_mission = 0.0
        self._last_inventory = 0.0

    def _load_seq(self) -> int:
        try:
            return int(json.loads(self.state_file.read_text())["seq"])
        except (OSError, ValueError, KeyError):
            return 0

    def _save_seq(self) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"seq": self.seq}))
        os.replace(tmp, self.state_file)

    def _send(self, method: str, path: str, body: Any = None, **kw) -> Dict[str, Any]:
        if not self.adapter.link_available():
            raise LinkDown("канал связи недоступен (имитация)")
        return self.client.request(method, path, body, **kw)

    def step(self) -> None:
        """Один такт (≈1 с)."""
        self.seq += 1
        record = {"seq": self.seq, "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                  **self.adapter.read_telemetry()}
        self._save_seq()
        self.queue.append(record)
        try:
            self._flush_telemetry()
            now = time.monotonic()
            if now - self._last_check > 60:
                self._last_check = now
                self._send("POST", "/drone-link/v1/events", {"kind": "self_check", "data": self.adapter.self_check()})
            if now - self._last_mission > 2:
                self._last_mission = now
                info = self._send("GET", "/drone-link/v1/mission")
                self.mission = MissionInfo.from_api(info)
                self.adapter.on_mission_update(self.mission)
            # Список источников видео: при изменении и не реже раза в 5 минут.
            inv = self.adapter.video_inventory(force=now - self._last_inventory > 300)
            if inv is not None:
                self._last_inventory = 0.0      # при обрыве связи — повторить на следующем такте
                res = self._send("POST", "/drone-link/v1/video/sources", inv)
                if not res.get("_status"):
                    self._last_inventory = now
                else:
                    log.error("список источников видео отклонён: %s", res)
            for ev in self.adapter.pending_events():
                self._send("POST", "/drone-link/v1/events", ev)
            if self.mission.active and self.mission.status == "in_progress":
                segment = self.adapter.read_video_segment()
                if segment:
                    self.video_seq += 1
                    self._send("POST", "/drone-link/v1/video/segments", raw=segment,
                               headers={"X-Segment-Seq": str(self.video_seq), "X-Segment-Duration-Ms": "1000"})
                if self.auto_complete and getattr(self.adapter, "arrived", False) \
                        and self.completed_sent != self.mission.order_id:
                    res = self._send("POST", "/drone-link/v1/events", {"kind": "delivery_completed", "data": {}})
                    self.completed_sent = self.mission.order_id
                    log.info("delivery_completed sent: %s", res)
            if not self.online:
                log.info("связь восстановлена, очередь досылки: %d", len(self.queue))
            self.online = True
            self.backoff = 1.0
        except LinkDown as exc:
            if self.online:
                log.warning("потеря связи: %s; телеметрия накапливается локально", exc)
            self.online = False
            self.backoff = min(self.backoff * 2, 30.0)

    def _flush_telemetry(self) -> None:
        while self.queue:
            batch = list(self.queue)[:200]
            res = self._send("POST", "/drone-link/v1/telemetry", {"records": batch})
            if res.get("_status"):
                log.error("телеметрия отклонена: %s", res)
                for _ in batch:
                    self.queue.popleft()
                return
            for _ in batch:
                self.queue.popleft()

    def run(self, duration_s: Optional[float] = None, tick_s: float = 1.0) -> None:
        self.adapter.connect()
        started = time.monotonic()
        try:
            while duration_s is None or time.monotonic() - started < duration_s:
                t0 = time.monotonic()
                self.step()
                delay = tick_s if self.online else min(self.backoff, 5.0)
                time.sleep(max(0.0, delay - (time.monotonic() - t0)))
        finally:
            self.adapter.disconnect()
