"""Обновления в реальном времени: PostgreSQL LISTEN → WebSocket.

Каждый процесс приложения слушает канал ``fpv_events`` и рассылает события
подключённым клиентам с учётом адресатов (пользователи/роли). Сообщения
содержат только идентификаторы и краткие данные; клиент после получения
сигнала (и после переподключения) перечитывает состояние через REST, поэтому
потеря сигнала при обрыве связи не приводит к рассинхронизации.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Dict, Optional, Set
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.engine import make_url
from starlette.concurrency import run_in_threadpool

from .. import db as dbmod
from .. import runtime
from ..errors import AppError
from ..services.events import CHANNEL
from ..services.identity import authenticate_session
from .deps import SESSION_COOKIE

log = logging.getLogger("fpv.realtime")
router = APIRouter()


class Client:
    def __init__(self, ws: WebSocket, user_id: str, roles: Set[str], token: str) -> None:
        self.ws = ws
        self.user_id = user_id
        self.roles = roles
        self.token = token
        self.queue: "asyncio.Queue[str]" = asyncio.Queue(maxsize=500)


class Hub:
    def __init__(self) -> None:
        self.clients: Set[Client] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.connected = False

    def start(self, database_url: str) -> None:
        if self._thread is not None:
            return
        self.loop = asyncio.get_event_loop()
        url = make_url(database_url).set(drivername="postgresql")
        dsn = url.render_as_string(hide_password=False)
        self._thread = threading.Thread(target=self._listen, args=(dsn,), name="fpv-listen", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _listen(self, dsn: str) -> None:
        import psycopg
        backoff = 1.0
        while not self._stop.is_set():
            try:
                with psycopg.connect(dsn, autocommit=True) as conn:
                    conn.execute(f"LISTEN {CHANNEL}")
                    self.connected = True
                    backoff = 1.0
                    # Сигнал клиентам: после восстановления канала нужно перечитать состояние.
                    self._dispatch(json.dumps({"kind": "resync", "data": {}, "audience": {"broadcast": True}}))
                    while not self._stop.is_set():
                        for n in conn.notifies(timeout=1.0):
                            self._dispatch(n.payload)
            except Exception as exc:  # noqa: BLE001
                self.connected = False
                log.warning("LISTEN connection lost: %s; retry in %.0fs", exc.__class__.__name__, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def _dispatch(self, payload: str) -> None:
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(self._fanout, payload)

    def _fanout(self, payload: str) -> None:
        try:
            msg = json.loads(payload)
        except ValueError:
            return
        audience = msg.pop("audience", {}) or {}
        users = set(audience.get("users") or [])
        roles = set(audience.get("roles") or [])
        out = json.dumps(msg, ensure_ascii=False)
        for c in list(self.clients):
            if audience.get("broadcast") or c.user_id in users or (c.roles & roles):
                try:
                    c.queue.put_nowait(out)
                except asyncio.QueueFull:
                    pass


hub = Hub()


def _origin_allowed(ws: WebSocket) -> bool:
    """Защита от межсайтового перехвата WebSocket: Origin должен совпадать с Host."""
    origin = ws.headers.get("origin")
    if not origin:
        return False
    host = ws.headers.get("host", "")
    return urlparse(origin).netloc == host


def _authenticate(token: str):
    db = dbmod.new_session()
    try:
        principal, _ = authenticate_session(db, runtime.settings(), runtime.directory(), token, None)
        db.commit()
        return principal
    finally:
        db.close()


@router.websocket("/api/v1/ws")
async def websocket_endpoint(ws: WebSocket):
    token = ws.cookies.get(SESSION_COOKIE, "")
    if not _origin_allowed(ws):
        await ws.close(code=4403)
        return
    try:
        principal = await run_in_threadpool(_authenticate, token)
    except AppError:
        await ws.close(code=4401)
        return
    await ws.accept()
    client = Client(ws, str(principal.user_id), set(principal.roles), token)
    hub.clients.add(client)
    await ws.send_text(json.dumps({"kind": "hello", "data": {"user_id": client.user_id,
                                                             "realtime": hub.connected}}))

    async def sender():
        last_check = time.monotonic()
        while True:
            try:
                msg = await asyncio.wait_for(client.queue.get(), timeout=25)
                await ws.send_text(msg)
            except asyncio.TimeoutError:
                await ws.send_text('{"kind":"ping","data":{}}')
            if time.monotonic() - last_check > 60:
                last_check = time.monotonic()
                try:
                    await run_in_threadpool(_authenticate, client.token)   # отзыв доступа закрывает канал
                except AppError:
                    await ws.close(code=4401)
                    return

    send_task = asyncio.create_task(sender())
    try:
        while True:
            await ws.receive_text()   # входящие сообщения игнорируются (канал только для уведомлений)
    except WebSocketDisconnect:
        pass
    finally:
        send_task.cancel()
        hub.clients.discard(client)
