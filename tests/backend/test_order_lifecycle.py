"""Сквозной сценарий заказа: создание → назначение → выполнение → видео → проверка → баллы."""
from __future__ import annotations

import datetime as dt

from conftest import run_outbox


def test_full_lifecycle_with_video_and_scoring(world, fake_clock):
    w = world
    o = w.placed_order()
    assert o["quote_amount"] is not None
    assert o["route"]["approval_status"] == "approved"

    # Заказ виден оператору в очереди.
    queue = w.operator.get("/api/v1/orders/queue").json()["items"]
    assert [x["id"] for x in queue] == [o["id"]]

    r = w.operator.post(f"/api/v1/orders/{o['id']}/accept", json={})
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert w.operator.get("/api/v1/orders/queue").json()["items"] == []

    r = w.operator.post(f"/api/v1/orders/{o['id']}/readiness-check")
    assert r.json()["ready"] is True and r.json()["order"]["status"] == "ready"

    # Наступает согласованное окно.
    fake_clock.advance(minutes=40)
    w.drone.telemetry()
    r = w.operator.post(f"/api/v1/orders/{o['id']}/start", json={})
    assert r.status_code == 200 and r.json()["status"] == "in_progress", r.text

    # Сервисный модуль передаёт видео 30 секунд (сегменты по 1 с) и телеметрию.
    for i in range(30):
        fake_clock.advance(seconds=1)
        assert w.drone.segment(i).status_code == 200
        if i % 5 == 0:
            w.drone.telemetry()
    mission = w.drone.client.get("/drone-link/v1/mission", headers=w.drone.headers).json()
    assert mission["active"] and mission["order_id"] == o["id"]

    r = w.operator.post(f"/api/v1/orders/{o['id']}/complete")
    assert r.status_code == 200 and r.json()["status"] == "awaiting_delivery_confirmation", r.text
    video = r.json()["video"]
    assert video["status"] == "pending_upload"
    assert video["segment_count"] == 15          # ровно последние 15 секунд

    stats = run_outbox()
    assert stats["dead"] == 0
    r = w.operator.post(f"/api/v1/orders/{o['id']}/report",
                        json={"summary": "Груз передан получателю", "handed_over_to": "Иван"})
    assert r.status_code == 201, r.text

    # Предварительный результат до проверки.
    prelim = w.operator.get(f"/api/v1/orders/{o['id']}/score").json()
    assert prelim["preliminary"] is True
    assert prelim["time_window"]["points"] == 30

    r = w.customer.post(f"/api/v1/orders/{o['id']}/receipt", json={"received": True, "comment": "Получено"})
    assert r.status_code == 200 and r.json()["status"] == "admin_review"

    detail = w.admin.get(f"/api/v1/orders/{o['id']}").json()
    assert detail["video"]["status"] == "stored"
    ev_id = detail["video"]["id"]
    content = w.admin.get(f"/api/v1/videos/{ev_id}/content?variant=original")
    assert content.status_code == 200 and content.content.startswith(b"TS000015")

    r = w.admin.post(f"/api/v1/orders/{o['id']}/review",
                     json={"approve": True, "comment": "Подтверждено", "video_verified": True})
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text

    final = w.operator.get(f"/api/v1/orders/{o['id']}/score").json()
    assert final["preliminary"] is False
    assert final["final_points"] == 100
    me = w.operator.get("/api/v1/scores/me").json()
    assert me["total_points"] == 100 and me["completed_orders"] == 1

    history = [h["to"] for h in w.customer.get(f"/api/v1/orders/{o['id']}/history").json()["items"]]
    assert history == ["draft", "pending_confirmation", "awaiting_operator", "accepted", "awaiting_readiness",
                       "ready", "in_progress", "awaiting_delivery_confirmation", "admin_review", "completed"]
