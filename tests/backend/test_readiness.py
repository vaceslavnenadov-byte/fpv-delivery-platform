"""Готовность к работе: координатор, оператор и техник отмечают «Готов / Не готов к работе»."""
from __future__ import annotations

import uuid

from fpv import db as dbmod
from fpv import runtime
from fpv.models import Order
from fpv.services import coordination

from test_coordination import _accepted_orders, _loads


def _set(client, role, ready):
    return client.put("/api/v1/me/readiness", json={"role": role, "ready": ready})


def _sweep():
    with dbmod.new_session() as s:
        stats = coordination.sweep(s, runtime.settings())
        s.commit()
    return stats


def test_default_ready_and_role_checks(world):
    r = world.operator.get("/api/v1/me/readiness?role=operator").json()
    assert r["ready"] is True and r["label"] == "Готов к работе"
    assert _set(world.operator, "coordinator", False).status_code == 403          # не своя роль
    assert _set(world.customer, "operator", False).status_code == 403             # нет права
    r = _set(world.operator, "operator", False).json()
    assert r["ready"] is False and r["label"] == "Не готов к работе"
    log = world.admin.get("/api/v1/admin/audit?action=work.readiness_changed").json()["items"]
    assert log and log[0]["details"] == {"role": "operator", "before": True, "after": False}


def test_not_ready_coordinator_gets_no_new_orders_and_keeps_own(world, api):
    c2, c3 = api("coordinator2"), api("coordinator3")
    coords = [world.coordinator, c2, c3]
    assert _set(c3, "coordinator", False).status_code == 200
    _accepted_orders(world, 6)
    assert _sweep()["assigned"] == 6
    assert _loads(world, [world.coordinator, c2]) == [3, 3]
    assert _loads(world, [c3]) == [0]                          # неготовому — ничего
    # Вернулся к работе — на него сразу переводятся последние поступившие заказы, нагрузка выравнивается.
    with dbmod.new_session() as s:
        newest = {}
        for o in s.query(Order).filter(Order.status == "accepted").order_by(Order.created_at).all():
            newest[o.coordinator_id] = o.id                    # самый новый заказ каждого координатора
    r = _set(c3, "coordinator", True).json()
    assert r["ready"] is True and r["moved"] == 2
    assert _loads(world, coords) == [2, 2, 2]
    with dbmod.new_session() as s:
        moved = {o.id for o in s.query(Order).filter(Order.coordinator_id == uuid.UUID(c3.user["id"]))}
    assert moved == set(newest.values())
    # Ушёл «не готов» — его заказы остаются за ним и не перераспределяются.
    _set(c2, "coordinator", False)
    _sweep()
    assert _loads(world, [c2]) == [2]


def test_not_ready_operator_sees_no_queue(world):
    o = world.placed_order()
    _set(world.operator, "operator", False)
    assert world.operator.get("/api/v1/orders/queue").json()["items"] == []
    r = world.operator.post(f"/api/v1/orders/{o['id']}/accept", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_ready_for_work"
    _set(world.operator, "operator", True)
    assert o["id"] in [x["id"] for x in world.operator.get("/api/v1/orders/queue").json()["items"]]
    assert world.operator.post(f"/api/v1/orders/{o['id']}/accept", json={}).status_code == 200


def test_not_ready_technician_pauses_fleet(world):
    placed = world.placed_order()                              # размещён до паузы — остаётся
    _set(world.technician, "technician", False)
    ids = [x["id"] for x in world.customer.get("/api/v1/available-drones").json()["items"]]
    assert placed["drone_id"] not in ids
    r = world.customer.post("/api/v1/orders", json=world.order_payload())
    assert r.status_code == 422 and r.json()["error"]["code"] == "fleet_paused"
    assert world.admin.get(f"/api/v1/orders/{placed['id']}").json()["status"] == "awaiting_operator"
    _set(world.technician, "technician", True)
    assert placed["drone_id"] in [x["id"] for x in world.customer.get("/api/v1/available-drones").json()["items"]]
    assert world.customer.post("/api/v1/orders", json=world.order_payload()).status_code == 201
