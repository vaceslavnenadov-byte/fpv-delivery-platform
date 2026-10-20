"""Готовность к работе: «Готов / Не готов к работе» и распределение заказов между теми, кто на смене."""
from __future__ import annotations

import uuid

from fpv import db as dbmod
from fpv import runtime
from fpv.models import Order
from fpv.services import coordination, operator_dispatch, tech_cover

from conftest import BASE
from test_consignees_approval import PNG

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


def _approve_operator(world, client):
    assert client.post("/api/v1/me/operator-application", json={"license_number": "OP-0002"}).status_code == 201
    r = world.admin.post(f"/api/v1/admin/operators/{client.user['id']}/decision", json={"approve": True, "notes": "ok"})
    assert r.status_code == 200


def _offered(world, order_id):
    o = world.admin.get(f"/api/v1/orders/{order_id}").json()
    return (o["offered_operator"] or {}).get("username")


def _queue(client):
    return [x["id"] for x in client.get("/api/v1/orders/queue").json()["items"]]


def test_coordinator_not_ready_hands_over_preflight_orders(world, api):
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
    assert r["ready"] is True and r["moved"] == 2 and r["note"] == "На вас переведено заказов: 2"
    assert _loads(world, coords) == [2, 2, 2]
    with dbmod.new_session() as s:
        moved = {o.id for o in s.query(Order).filter(Order.coordinator_id == uuid.UUID(c3.user["id"]))}
    assert moved == set(newest.values())
    # Ушёл «не готов»: заказ в полёте остаётся за ним, ещё не вылетевший — сразу другим координаторам.
    with dbmod.new_session() as s:
        flying = s.query(Order).filter(Order.coordinator_id == uuid.UUID(c2.user["id"])).first()
        flying.status = "in_progress"
        flying_id = flying.id
        s.commit()
    r = _set(c2, "coordinator", False).json()
    assert r["moved"] == 1 and r["note"] == "Передано другим координаторам заказов: 1"
    with dbmod.new_session() as s:
        left = [o.id for o in s.query(Order).filter(Order.coordinator_id == uuid.UUID(c2.user["id"]))]
    assert left == [flying_id]
    assert sorted(_loads(world, [world.coordinator, c3])) == [2, 3]


def test_operator_offers_decline_handover_and_wait(world, api):
    op2 = api("operator2")
    o = world.placed_order()
    assert _offered(world, o["id"]) == "operator"              # единственный допущенный и готовый
    assert _queue(world.operator) == [o["id"]]
    _approve_operator(world, op2)
    assert _queue(op2) == []                                   # чужое предложение не видно
    r = op2.post(f"/api/v1/orders/{o['id']}/accept", json={})
    assert r.status_code in (404, 409)
    # Оператор отклоняет — заказ сразу предлагается другому подходящему оператору.
    assert world.operator.post(f"/api/v1/orders/{o['id']}/decline", json={"reason": "Далеко"}).status_code == 200
    assert _offered(world, o["id"]) == "operator2" and _queue(op2) == [o["id"]] and _queue(world.operator) == []
    # Второй ушёл со смены: отказавшемуся заказ не возвращается — подходящих нет, заказ ждёт и не виден никому.
    r = _set(op2, "operator", False).json()
    assert r["moved"] == 1 and r["note"] == "Передано другим операторам заказов: 1"
    assert _offered(world, o["id"]) is None and _queue(op2) == [] and _queue(world.operator) == []
    assert world.admin.get(f"/api/v1/orders/{o['id']}").json()["status"] == "awaiting_operator"
    # Вернулся — ждущий заказ сразу у него; принимает.
    r = _set(op2, "operator", True).json()
    assert r["note"] == "Вам предложено заказов: 1" and _queue(op2) == [o["id"]]
    assert op2.post(f"/api/v1/orders/{o['id']}/accept", json={}).status_code == 200
    # Отказ от принятого — заказ снова предлагается, но уже не ему.
    assert op2.post(f"/api/v1/orders/{o['id']}/decline", json={"reason": "Нет связи"}).status_code == 200
    assert _offered(world, o["id"]) is None                   # оба отказались — ждёт нового оператора


def test_operator_load_balancing_on_return(world, api):
    op2 = api("operator2")
    _approve_operator(world, op2)
    _set(world.operator, "operator", False)
    ids = [world.placed_order()["id"] for _ in range(2)]
    assert [_offered(world, i) for i in ids] == ["operator2", "operator2"]
    with dbmod.new_session() as s:
        newest = max(s.query(Order).filter(Order.id.in_([uuid.UUID(i) for i in ids])).all(),
                     key=lambda o: o.submitted_at).id
    r = _set(world.operator, "operator", True).json()
    assert r["moved"] == 1                                     # 2 : 0 → 1 : 1
    assert _queue(world.operator) == [str(newest)]             # перешёл последний поступивший
    with dbmod.new_session() as s:
        assert operator_dispatch.sweep(s)["moved"] == 0


def test_operator_qualification_respected(world, api):
    op2 = api("operator2")
    _approve_operator(world, op2)
    m = world.admin.post("/api/v1/catalog/drone-models", json={
        "model": "Курьер-К", "manufacturer": "АэроТех", "link_type": "4G", "construction": "Квадрокоптер",
        "engine_type": "Электрический", "max_range_m": 20000, "max_payload_g": 2500}).json()
    d = world.technician.post("/api/v1/drones", json={
        "serial_number": "SN-Q", "registration_number": "RU-Q", "manufacturer": "xx", "model": "y",
        "max_payload_g": 2000, "max_range_m": 20000, "fleet_id": world.fleet_id, "drone_model_id": m["id"]}).json()["id"]
    world.admin.post(f"/api/v1/drones/{d}/decision", json={"action": "approve"})
    world.technician.post(f"/api/v1/drones/{d}/inspections",
                          json={"result": "pass", "set_status": "serviceable", "battery_pct": 95, "comment": "ok"})
    world.technician.post(f"/api/v1/drones/{d}/key")
    world.technician.post(f"/api/v1/drones/{d}/publish", json={"published": True})
    o = world.create_order(drone_id=d)
    world.customer.post(f"/api/v1/orders/{o['id']}/submit", json={})
    world.customer.post(f"/api/v1/orders/{o['id']}/confirm", json={})
    world.coordinator_approve(o["id"])
    assert _offered(world, o["id"]) is None                    # лицензии ни у кого — заказ ждёт
    r = op2.post(f"/api/v1/me/licenses?drone_model_id={m['id']}&license_number=L-1&issued_on=2026-01-15"
                 f"&issued_by=Росавиация&filename=l.png", content=PNG)
    assert r.status_code == 201, r.text
    world.admin.post(f"/api/v1/licenses/{r.json()['id']}/decision", json={"status": "approved"})
    assert _offered(world, o["id"]) == "operator2"             # первый подходящий получил сразу


def test_technician_cover_by_zone_and_return(world, api):
    t2 = api("technician2")
    assert t2.post("/api/v1/fleets", json={"name": "Парк «Север»", "base": BASE}).status_code == 201
    o = world.placed_order()
    assert world.admin.get(f"/api/v1/orders/{o['id']}").json()["duty_technician"] is None
    r = _set(world.technician, "technician", False).json()
    assert r["note"] == "Заказы вашего парка ведут техники зоны: 1"
    assert world.admin.get(f"/api/v1/orders/{o['id']}").json()["duty_technician"]["username"] == "technician2"
    # Замещающему открыт заказ и дрон: список заказов, подключение, проверка готовности.
    assert o["id"] in [x["id"] for x in t2.get("/api/v1/fleet-orders").json()["items"]]
    assert t2.get(f"/api/v1/drones/{world.drone_id}/connection").status_code == 200
    assert t2.post(f"/api/v1/orders/{o['id']}/tech-ready").status_code == 200
    assert t2.post(f"/api/v1/drones/{world.drone_id}/publish", json={"published": False}).status_code == 404   # парк — не его
    # Новый заказ, пока техник парка не на смене, сразу у техника зоны.
    o2 = world.placed_order()
    assert world.admin.get(f"/api/v1/orders/{o2['id']}").json()["duty_technician"]["username"] == "technician2"
    # Замещающий ушёл — готовых в зоне нет, заказы остаются у техника парка.
    _set(t2, "technician", False)
    assert world.admin.get(f"/api/v1/orders/{o['id']}").json()["duty_technician"] is None
    assert t2.get(f"/api/v1/drones/{world.drone_id}/connection").status_code == 404
    _set(t2, "technician", True)
    assert world.admin.get(f"/api/v1/orders/{o['id']}").json()["duty_technician"]["username"] == "technician2"
    # Техник парка вернулся — заказы снова у него.
    r = _set(world.technician, "technician", True).json()
    assert r["note"] == "Вернулось к вам заказов вашего парка: 2"
    assert world.admin.get(f"/api/v1/orders/{o['id']}").json()["duty_technician"] is None
    with dbmod.new_session() as s:
        assert tech_cover.sweep(s) == {"covered": 0, "returned": 0, "moved": 0}


def test_audit_labels_in_russian(world):
    _set(world.operator, "operator", False)
    r = world.admin.get("/api/v1/admin/audit?action=work.").json()
    row = r["items"][0]
    assert row["action_label"] == "Изменена готовность к работе"
    assert row["entity_label"] == "Пользователь" and row["outcome_label"] == "Успешно"
    assert {"prefix": "order.", "label": "Заказы"} in r["sections"]
