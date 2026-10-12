"""Потеря связи, отказ компонентов, восстановление после сбоя, повторная обработка событий."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from sqlalchemy import text

from conftest import run_outbox
from fpv import db as dbmod
from fpv import runtime
from fpv.services import events
from fpv.worker import link_monitor_once, receipt_timeouts_once


def _q(sql, **kw):
    with dbmod.get_engine().connect() as c:
        return c.execute(text(sql), kw).scalar()


def test_link_loss_warning_then_safe_abort(world, fake_clock, settings):
    o = world.started_order()
    fake_clock.advance(seconds=15)
    link_monitor_once()
    assert _q("SELECT link_status FROM drones") == "degraded"
    fake_clock.advance(seconds=20)
    link_monitor_once()
    assert _q("SELECT link_status FROM drones") == "lost"
    assert world.status(o["id"]) == "in_progress"            # кратковременная потеря — только предупреждение
    notes = world.operator.get("/api/v1/me/notifications").json()["items"]
    assert any("потеря связи" in n["title"] for n in notes)
    fake_clock.advance(seconds=settings.link_loss_abort_s)
    link_monitor_once()
    assert world.status(o["id"]) == "aborted_technical"
    assert _q("SELECT health_status FROM drones") == "requires_check"   # не допускается до проверки техником


def test_link_restored_before_threshold(world, fake_clock):
    o = world.started_order()
    fake_clock.advance(seconds=40)
    link_monitor_once()
    assert _q("SELECT link_status FROM drones") == "lost"
    # Переподключение: модуль досылает накопленную телеметрию, повторы отбрасываются.
    first = world.drone.telemetry(n=5)
    assert first.json() == {"received": 5, "stored": 5, "duplicates": 0}
    world.drone.seq -= 5
    replay = world.drone.telemetry(n=5)
    assert replay.json()["duplicates"] == 5
    assert _q("SELECT link_status FROM drones") == "online"
    assert world.status(o["id"]) == "in_progress"
    link_monitor_once()
    assert world.status(o["id"]) == "in_progress"


def test_critical_drone_error_aborts_order(world):
    o = world.started_order()
    r = world.drone.event("error", code="BATT_CELL_FAIL", severity="critical", message="Отказ ячейки батареи")
    assert r.status_code == 200
    assert world.status(o["id"]) == "aborted_technical"
    drone = world.technician.get(f"/api/v1/drones/{world.drone_id}").json()
    assert drone["health_status"] == "requires_check"
    assert drone["health_checks"][0]["error_codes"] == ["BATT_CELL_FAIL"]
    assert drone["readiness"]["ready"] is False


def test_operator_abort_and_report(world):
    o = world.started_order()
    r = world.operator.post(f"/api/v1/orders/{o['id']}/abort", json={"reason": "Сильный боковой ветер"})
    assert r.json()["status"] == "aborted_technical"
    r = world.operator.post(f"/api/v1/orders/{o['id']}/report", json={"summary": "Задание прервано"})
    assert r.status_code == 201


def test_storage_outage_retries_then_recovers(world, fake_clock, settings):
    o = world.started_order()
    for i in range(16):
        fake_clock.advance(seconds=1)
        world.drone.segment(i)
    world.operator.post(f"/api/v1/orders/{o['id']}/complete")
    # Отказ хранилища: корень занят файлом — запись невозможна.
    root = Path(settings.object_store_root)
    shutil.rmtree(root, ignore_errors=True)
    root.write_text("not a directory")
    stats = events.process_batch(dbmod.new_session)
    assert stats["retry"] >= 1
    assert _q("SELECT status FROM video_evidence") == "pending_upload"
    assert _q("SELECT last_error FROM outbox_events WHERE topic='video.upload'").startswith("video.upload")
    root.unlink()
    fake_clock.advance(minutes=1)        # истекла задержка повтора
    run_outbox()
    assert _q("SELECT status FROM video_evidence") == "stored"


def test_outbox_dead_letter_after_max_attempts(world, fake_clock):
    calls = {"n": 0}

    @events.handler("test.always_fails", name="test.always_fails")
    def failing(db, payload, event_id):
        calls["n"] += 1
        raise RuntimeError("boom")

    with dbmod.session_scope() as s:
        events.publish(s, "test.always_fails", {"x": 1}, realtime=False, max_attempts=3)
    for _ in range(5):
        events.process_batch(dbmod.new_session)
        fake_clock.advance(minutes=20)
    assert calls["n"] == 3
    assert _q("SELECT status FROM outbox_events WHERE topic='test.always_fails'") == "dead"
    mon = world.admin.get("/api/v1/admin/monitoring").json()
    outbox = [s for s in mon["services"] if s["name"].startswith("Очередь")][0]
    assert outbox["ok"] is False


def test_handler_side_effects_are_idempotent(world):
    o = world.placed_order()
    run_outbox()
    before = _q("SELECT count(*) FROM notifications")
    # Имитация сбоя после выполнения обработчика: событие возвращается в очередь.
    with dbmod.get_engine().begin() as c:
        c.execute(text("UPDATE outbox_events SET status='pending', processed_at=NULL"))
        c.execute(text("DELETE FROM processed_events"))
    run_outbox()
    assert _q("SELECT count(*) FROM notifications") == before


def test_crash_after_capture_before_upload(world, fake_clock, settings):
    """Сервер упал после фиксации клипа: после перезапуска воркер загружает клип из spool."""
    o = world.started_order()
    for i in range(16):
        fake_clock.advance(seconds=1)
        world.drone.segment(i)
    world.operator.post(f"/api/v1/orders/{o['id']}/complete")
    # «Перезапуск»: новый пул соединений, событие было захвачено воркером, но не обработано.
    with dbmod.get_engine().begin() as c:
        c.execute(text("UPDATE outbox_events SET status='processing', locked_until=now() - interval '1 minute'"))
    dbmod.init_engine(settings.database_url)
    run_outbox()
    assert _q("SELECT status FROM video_evidence") == "stored"


def test_receipt_timeout_moves_to_admin_review(world, fake_clock):
    o = world.started_order()
    world.operator.post(f"/api/v1/orders/{o['id']}/complete")
    fake_clock.advance(hours=25)
    world.admin.login()
    assert receipt_timeouts_once() == 1
    detail = world.admin.get(f"/api/v1/orders/{o['id']}").json()
    assert detail["status"] == "admin_review" and detail["receipt_overdue"] is True


def test_database_outage_reported_by_readiness(app, settings):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    assert client.get("/health/ready").status_code == 200
    dbmod.init_engine(settings.database_url.replace(":5433/", ":1/").replace(":5432/", ":1/"),
                      connect_args={"connect_timeout": 1})
    r = client.get("/health/ready")
    assert r.status_code in (500, 503)
    dbmod.init_engine(settings.database_url)
    assert client.get("/health/ready").status_code == 200


def test_unknown_drone_key_and_blocked_drone(world):
    r = world.admin.post(f"/api/v1/drones/{world.drone_id}/decision", json={"action": "block", "reason": "Проверка"})
    assert r.status_code == 200
    assert world.drone.telemetry().status_code == 403
    assert world.drone.client.get("/drone-link/v1/mission", headers={"Authorization": "Bearer x"}).status_code == 401
