"""Фоновый воркер: outbox, контроль связи с дронами, тайм-ауты подтверждения,
сроки хранения. Запуск: ``python -m fpv.worker`` (systemd: fpv-worker.service).

Несколько экземпляров безопасны: события выбираются через SKIP LOCKED,
плановые задачи сериализуются advisory-блокировками.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import signal
import time
from typing import Dict

from sqlalchemy import select, text

from . import clock, db, runtime
from .config import load_settings
from .models import Order
from .services import audit, coordination, customer_stream, events, fleets, orders, telemetry, video
from .services import directory as directory_mod
from .services import storage

log = logging.getLogger("fpv.worker")


def _locked(session, key: int) -> bool:
    return bool(session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": key}).scalar())


def link_monitor_once() -> Dict[str, int]:
    with db.session_scope() as s:
        if not _locked(s, 7243101):
            return {}
        return telemetry.monitor_links(s, runtime.settings())


def receipt_timeouts_once() -> int:
    """Заказ без подтверждения получения дольше receipt_timeout_h передаётся администратору."""
    settings = runtime.settings()
    n = 0
    with db.session_scope() as s:
        if not _locked(s, 7243102):
            return 0
        limit = clock.now() - dt.timedelta(hours=settings.receipt_timeout_h)
        rows = list(s.execute(select(Order).where(Order.status == "awaiting_delivery_confirmation",
                                                  Order.delivered_at < limit)
                              .with_for_update(of=Order, skip_locked=True)).unique().scalars())
        for o in rows:
            o.receipt_overdue = True
            orders.transition(s, o, "admin_review", audit.Actor.system("receipt-timeout"), actor_kind="system",
                              reason=f"Получение не подтверждено в течение {settings.receipt_timeout_h} ч")
            n += 1
    return n


def retention_once() -> Dict[str, int]:
    settings = runtime.settings()
    with db.session_scope() as s:
        if not _locked(s, 7243103):
            return {}
        purged = video.purge_expired(s)
        cutoff = clock.now() - dt.timedelta(days=settings.telemetry_retention_days)
        res = s.execute(text("DELETE FROM telemetry_records WHERE recorded_at < :c"), {"c": cutoff})
        removed = res.rowcount or 0
        if purged or removed:
            audit.record(s, audit.Actor.system("retention"), "retention.run",
                         details={"videos_purged": purged, "telemetry_removed": removed})
        return {"videos_purged": purged, "telemetry_removed": removed}


def customer_stream_sweep_once() -> int:
    with db.session_scope() as s:
        if not _locked(s, 7243104):
            return 0
        coordination.sweep(s, runtime.settings())        # недостающие координаторы, выравнивание нагрузки
        removed = customer_stream.sweep(s, runtime.settings())
        return removed + fleets.sweep_checks(s, runtime.settings())   # буферы проверки видео техником


def tick(state: Dict[str, float]) -> None:
    now = time.monotonic()
    events.process_batch(db.new_session)
    if now - state.get("links", 0) >= 5:
        state["links"] = now
        link_monitor_once()
    if now - state.get("receipt", 0) >= 60:
        state["receipt"] = now
        receipt_timeouts_once()
    if now - state.get("customer_stream", 0) >= 30:
        state["customer_stream"] = now
        customer_stream_sweep_once()
    if now - state.get("retention", 0) >= 3600:
        state["retention"] = now
        retention_once()


async def run_loop_async() -> None:
    from starlette.concurrency import run_in_threadpool
    state: Dict[str, float] = {}
    while True:
        try:
            await run_in_threadpool(tick, state)
        except Exception:  # noqa: BLE001
            log.exception("worker tick failed")
        await asyncio.sleep(1)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = load_settings()
    runtime.runtime.settings = settings
    runtime.runtime.directory = directory_mod.build_provider(settings)
    runtime.runtime.store = storage.build_store(settings)
    db.init_engine(settings.database_url)
    from .services import notifications  # noqa: F401  (регистрация обработчиков)

    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    state: Dict[str, float] = {}
    log.info("worker started")
    while not stop["flag"]:
        try:
            tick(state)
        except Exception:  # noqa: BLE001
            log.exception("worker tick failed")
        time.sleep(1)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
