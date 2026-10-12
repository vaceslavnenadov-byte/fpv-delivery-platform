"""Начисление баллов: однократность, предварительный/окончательный результат, споры, апелляции."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from conftest import run_outbox
from fpv import db as dbmod


def _deliver(world, fake_clock, *, late_minutes=0, video=True, receipt=True, report=True):
    o = world.started_order()
    # окно: +30..+90 мин от создания
    fake_clock.advance(minutes=60 + late_minutes + (30 if late_minutes else 0))
    world.drone.telemetry()
    if video:
        for i in range(16):
            fake_clock.advance(seconds=1)
            world.drone.segment(i)
    world.operator.post(f"/api/v1/orders/{o['id']}/complete")
    run_outbox()
    if report:
        world.operator.post(f"/api/v1/orders/{o['id']}/report", json={"summary": "Доставлено"})
    if receipt:
        world.customer.post(f"/api/v1/orders/{o['id']}/receipt", json={"received": True})
    return o


def _review(world, o, approve=True, video=True):
    return world.admin.post(f"/api/v1/orders/{o['id']}/review",
                            json={"approve": approve, "comment": "Проверено" if approve else "Груз повреждён",
                                  "video_verified": video})


def test_points_awarded_once(world, fake_clock):
    o = _deliver(world, fake_clock)
    assert _review(world, o).status_code == 200
    assert _review(world, o).status_code == 409       # повторная проверка невозможна
    import uuid
    from fpv.models import Order, User
    from fpv.services import scoring
    from fpv.services.identity import build_principal
    with dbmod.session_scope() as s:                  # повторный вызов finalize ничего не меняет
        order = s.get(Order, uuid.UUID(o["id"]))
        admin = s.query(User).filter_by(domain_username="admin").one()
        scoring.finalize(s, build_principal(s, admin), order)
    with dbmod.get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM score_transactions WHERE kind='final'")).scalar() == 1
        assert c.execute(text("SELECT total_points FROM operators")).scalar() == 100
        with pytest.raises(IntegrityError):
            c.execute(text("INSERT INTO score_transactions (operator_id, order_id, kind, points) "
                           "SELECT operator_id, order_id, 'final', 10 FROM score_transactions LIMIT 1"))


def test_late_delivery_reduces_time_points_only(world, fake_clock):
    o = _deliver(world, fake_clock, late_minutes=15)     # опоздание ≈ 15 мин при допуске 30 мин
    _review(world, o)
    s = world.operator.get(f"/api/v1/orders/{o['id']}/score").json()
    assert 13 <= s["time_window"]["points"] <= 17
    assert s["result"]["points"] == 70
    assert "speed" not in str(s).lower()


def test_preliminary_then_final(world, fake_clock):
    o = _deliver(world, fake_clock)
    pre = world.operator.get(f"/api/v1/orders/{o['id']}/score").json()
    assert pre["preliminary"] is True and pre["result"]["items"]["admin_approved"] == 0
    assert pre["total"] == 30 + 20 + 10       # окно + получение + отчёт (видео и администратор — после проверки)
    _review(world, o)
    fin = world.operator.get(f"/api/v1/orders/{o['id']}/score").json()
    assert fin["preliminary"] is False and fin["final_points"] == 100


def test_admin_rejects_delivery(world, fake_clock):
    o = _deliver(world, fake_clock)
    r = _review(world, o, approve=False, video=False)
    assert r.json()["status"] == "rejected"
    fin = world.operator.get(f"/api/v1/orders/{o['id']}/score").json()
    assert fin["final_points"] == 0
    r = world.admin.post(f"/api/v1/orders/{o['id']}/review", json={"approve": False})
    assert r.status_code == 409


def test_reject_requires_reason(world, fake_clock):
    o = _deliver(world, fake_clock)
    r = world.admin.post(f"/api/v1/orders/{o['id']}/review", json={"approve": False, "comment": ""})
    assert r.status_code == 422


def test_cancelled_order_gets_no_points(world):
    o = world.ready_order()
    world.customer.post(f"/api/v1/orders/{o['id']}/cancel", json={"reason": "Отмена"})
    with dbmod.get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM score_transactions")).scalar() == 0


def test_dispute_before_review_reduces_result(world, fake_clock):
    o = _deliver(world, fake_clock, receipt=False)
    r = world.customer.post(f"/api/v1/orders/{o['id']}/dispute", json={"description": "Упаковка повреждена"})
    assert r.status_code == 201
    assert world.status(o["id"]) == "disputed"
    complaint_id = r.json()["complaint_id"]
    r = world.admin.post(f"/api/v1/admin/complaints/{complaint_id}/resolve",
                         json={"upheld": True, "resolution": "Претензия обоснована: повреждение упаковки"})
    assert r.status_code == 200
    assert world.status(o["id"]) == "admin_review"
    _review(world, o)
    fin = world.operator.get(f"/api/v1/orders/{o['id']}/score").json()
    # нет подтверждения получения (−20) и обоснованная претензия (−20)
    assert fin["final_points"] == 30 + 70 - 20 - 20


def test_dispute_after_completion_adjusts_with_audit(world, fake_clock):
    o = _deliver(world, fake_clock)
    _review(world, o)
    r = world.customer.post(f"/api/v1/orders/{o['id']}/dispute", json={"description": "Не тот получатель"})
    cid = r.json()["complaint_id"]
    world.admin.post(f"/api/v1/admin/complaints/{cid}/resolve",
                     json={"upheld": True, "resolution": "Подтверждено по видео", "points_adjustment": -25})
    assert world.status(o["id"]) == "completed"
    s = world.operator.get(f"/api/v1/orders/{o['id']}/score").json()
    assert s["final_points"] == 100 and s["total_with_adjustments"] == 75
    r = world.admin.post(f"/api/v1/admin/complaints/{cid}/resolve",
                         json={"upheld": True, "resolution": "Повтор решения"})
    assert r.status_code == 409          # повторная корректировка невозможна
    audit = world.admin.get("/api/v1/admin/audit?action=score.adjusted").json()["items"]
    assert len(audit) == 1 and audit[0]["details"]["reason"].startswith("Обоснованная претензия")


def test_operator_appeal(world, fake_clock):
    o = _deliver(world, fake_clock, report=False)
    _review(world, o)
    s = world.operator.get(f"/api/v1/orders/{o['id']}/score").json()
    assert s["final_points"] == 90
    r = world.operator.post(f"/api/v1/orders/{o['id']}/appeal",
                            json={"description": "Отчёт передан на бумаге координатору"})
    cid = r.json()["complaint_id"]
    r = world.admin.post(f"/api/v1/admin/complaints/{cid}/resolve",
                         json={"upheld": True, "resolution": "Бумажный отчёт приложен", "points_adjustment": 50})
    assert r.status_code == 200
    s = world.operator.get(f"/api/v1/orders/{o['id']}/score").json()
    assert s["total_with_adjustments"] == 100           # не выше максимума 100
    assert world.operator.get("/api/v1/scores/me").json()["total_points"] == 100


def test_scoring_rules_validation_and_versioning(world):
    bad = {"time_window": {"max_points": 50, "grace_minutes": 30},
           "result": {"receipt_confirmed": 20, "report_submitted": 10, "video_verified": 20, "admin_approved": 20,
                      "upheld_complaint_penalty": 20}}
    r = world.admin.put("/api/v1/admin/scoring-rules", json={"rules": bad})
    assert r.status_code == 422
    bad["time_window"]["max_points"] = 30
    bad["time_window"]["grace_minutes"] = 60
    r = world.admin.put("/api/v1/admin/scoring-rules", json={"rules": bad, "comment": "Мягче к опозданиям"})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert world.operator.put("/api/v1/admin/scoring-rules", json={"rules": bad}).status_code == 403
