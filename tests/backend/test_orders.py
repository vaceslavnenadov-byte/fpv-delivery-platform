"""Создание заказа, проверки перед подтверждением, назначение, статусы и их переходы."""
from __future__ import annotations

import datetime as dt
import threading

from fpv import clock


def _submit(world, o):
    return world.customer.post(f"/api/v1/orders/{o['id']}/submit", json={})


def test_validation_rejects_prohibited_cargo(world):
    o = world.create_order(cargo_description="Пиротехника для праздника")
    r = _submit(world, o)
    assert r.status_code == 200 and r.json()["status"] == "draft"
    failed = {c["code"] for c in r.json()["validation_result"]["checks"] if not c["ok"]}
    assert failed == {"cargo_prohibited"}


def test_validation_rejects_overweight_and_no_fly_zone(world):
    o = world.create_order(cargo_weight_g=5000,
                           origin={"lat": 55.58, "lon": 37.38, "address": "Рядом с аэродромом"},
                           destination={"lat": 55.62, "lon": 37.43, "address": "За аэродромом"})
    r = _submit(world, o)
    failed = {c["code"] for c in r.json()["validation_result"]["checks"] if not c["ok"]}
    assert {"cargo_weight", "airspace", "drone"} <= failed
    assert r.json()["route"]["approval_status"] == "rejected"


def test_validation_rejects_window_in_past(world):
    now = clock.now()
    o = world.create_order(window_start=(now - dt.timedelta(hours=1)).isoformat(),
                           window_end=(now + dt.timedelta(hours=1)).isoformat())
    failed = {c["code"] for c in _submit(world, o).json()["validation_result"]["checks"] if not c["ok"]}
    assert failed == {"time_window"}


def test_faulty_drone_not_admitted(world):
    r = world.technician.post(f"/api/v1/drones/{world.drone_id}/inspections",
                              json={"result": "fail", "set_status": "grounded", "error_codes": ["MOTOR_3"]})
    assert r.status_code == 201
    o = world.create_order()
    failed = {c["code"] for c in _submit(world, o).json()["validation_result"]["checks"] if not c["ok"]}
    assert "drone" in failed


def test_drone_fault_between_accept_and_start(world):
    o = world.ready_order()
    world.technician.post(f"/api/v1/drones/{world.drone_id}/inspections",
                          json={"result": "fail", "set_status": "requires_check", "error_codes": ["GPS"]})
    r = world.operator.post(f"/api/v1/orders/{o['id']}/start", json={})
    assert r.json()["status"] == "awaiting_readiness"     # старт не выполнен, повторная проверка
    r = world.operator.post(f"/api/v1/orders/{o['id']}/readiness-check")
    assert r.json()["ready"] is False and any("техником" in x for x in r.json()["reasons"])


def test_drone_offline_blocks_readiness(world, fake_clock):
    o = world.placed_order()
    world.operator.post(f"/api/v1/orders/{o['id']}/accept", json={})
    world.tech_confirm(o["id"])
    fake_clock.advance(minutes=2)
    r = world.operator.post(f"/api/v1/orders/{o['id']}/readiness-check")
    assert r.json()["ready"] is False
    assert r.json()["order"]["status"] == "awaiting_readiness"
    world.drone.telemetry()
    r = world.operator.post(f"/api/v1/orders/{o['id']}/readiness-check")
    assert r.json()["ready"] is True


def test_invalid_transitions_rejected(world):
    o = world.create_order()
    for action in ("confirm", "accept", "start", "complete"):
        actor = world.customer if action == "confirm" else world.operator
        r = actor.post(f"/api/v1/orders/{o['id']}/{action}", json={})
        assert r.status_code in (403, 404, 409), (action, r.text)
    o = world.started_order()
    r = world.customer.post(f"/api/v1/orders/{o['id']}/cancel", json={"reason": "Передумал"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_transition"
    r = world.admin.post(f"/api/v1/orders/{o['id']}/review", json={"approve": True})
    assert r.status_code == 409


def test_double_start_and_double_complete(world):
    o = world.started_order()
    r = world.operator.post(f"/api/v1/orders/{o['id']}/start", json={})
    assert r.status_code == 409
    assert world.operator.post(f"/api/v1/orders/{o['id']}/complete").status_code == 200
    # Повторное событие завершения (например, и от оператора, и от дрона) не меняет заказ.
    r = world.drone.event("delivery_completed")
    assert r.status_code == 200
    r = world.operator.post(f"/api/v1/orders/{o['id']}/complete")
    assert r.status_code == 200 and r.json()["status"] == "awaiting_delivery_confirmation"
    hist = [h["to"] for h in world.admin.get(f"/api/v1/orders/{o['id']}/history").json()["items"]]
    assert hist.count("awaiting_delivery_confirmation") == 1


def test_idempotent_order_creation_after_reconnect(world):
    body = world.order_payload()
    r1 = world.customer.post("/api/v1/orders", json=body, idem="create-order-0001")
    r2 = world.customer.post("/api/v1/orders", json=body, idem="create-order-0001")
    assert r1.status_code == r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
    assert r2.headers.get("Idempotent-Replay") == "true"
    assert len(world.customer.get("/api/v1/orders").json()["items"]) == 1
    body["cargo_weight_g"] = 400
    r3 = world.customer.post("/api/v1/orders", json=body, idem="create-order-0001")
    assert r3.status_code == 409 and r3.json()["error"]["code"] == "idempotency_mismatch"


def test_operator_decline_hides_order_and_release_returns_to_queue(world, api):
    o = world.placed_order()
    r = world.operator.post(f"/api/v1/orders/{o['id']}/decline", json={"reason": "Не мой район"})
    assert r.status_code == 200
    assert world.operator.get("/api/v1/orders/queue").json()["items"] == []
    op2 = api("operator2")
    op2.post("/api/v1/me/operator-application", json={"license_number": "OP-2"})
    world.admin.post(f"/api/v1/admin/operators/{op2.user['id']}/decision", json={"approve": True})
    op2.login()
    assert op2.post(f"/api/v1/orders/{o['id']}/accept", json={}).status_code == 200
    r = op2.post(f"/api/v1/orders/{o['id']}/decline", json={"reason": "Плохая погода"})
    assert r.json()["status"] == "awaiting_operator" and r.json()["operator_id"] is None


def test_concurrent_accept_only_one_wins(world, api):
    o = world.placed_order()
    op2 = api("operator2")
    op2.post("/api/v1/me/operator-application", json={"license_number": "OP-2"})
    world.admin.post(f"/api/v1/admin/operators/{op2.user['id']}/decision", json={"approve": True})
    results = []

    def go(client):
        results.append(client.post(f"/api/v1/orders/{o['id']}/accept", json={}).status_code)

    threads = [threading.Thread(target=go, args=(c,)) for c in (world.operator, op2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results)[0] == 200 and sorted(results)[1] in (404, 409)
    detail = world.admin.get(f"/api/v1/orders/{o['id']}").json()
    assert detail["status"] == "accepted"


def test_version_conflict(world):
    o = world.placed_order()
    r = world.operator.post(f"/api/v1/orders/{o['id']}/accept", json={"expected_version": o["version"] - 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "version_conflict"


def test_customer_cancel_before_start(world):
    o = world.ready_order()
    r = world.customer.post(f"/api/v1/orders/{o['id']}/cancel", json={"reason": "Груз не готов"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    # Дрон освобождён: можно оформить новый заказ.
    world.placed_order()
    n = world.operator.get("/api/v1/scores/me").json()
    assert n["total_points"] == 0


def test_one_active_order_per_drone(world):
    o1 = world.placed_order()
    world.operator.post(f"/api/v1/orders/{o1['id']}/accept", json={})
    o2 = world.create_order()
    failed = {c["code"] for c in _submit(world, o2).json()["validation_result"]["checks"] if not c["ok"]}
    assert "drone_available" in failed


def test_status_change_notifies_parties(world):
    from conftest import run_outbox
    o = world.placed_order()
    run_outbox()
    titles = [n["title"] for n in world.operator.get("/api/v1/me/notifications").json()["items"]]
    assert any(f"№{o['number']}" in t for t in titles)
    world.operator.post(f"/api/v1/orders/{o['id']}/accept", json={})
    run_outbox()
    run_outbox()   # повторная обработка не создаёт дубликатов
    notes = world.customer.get("/api/v1/me/notifications").json()["items"]
    accepted = [n for n in notes if "Принят оператором" in n["title"]]
    assert len(accepted) == 1


def test_order_list_by_role_for_multi_role_user(world):
    """Пользователь с ролями оператора и заказчика: клиент каждой роли видит только свои заказы."""
    o = world.ready_order()                                   # world.operator — исполнитель заказа
    r = world.admin.post(f"/api/v1/admin/users/{world.operator.user['id']}/roles",
                         json={"role": "customer", "grant": True})
    assert r.status_code == 200, r.text
    world.operator.login()                                    # права пересчитываются при входе
    ids = lambda q: [i["id"] for i in world.operator.get(f"/api/v1/orders{q}").json()["items"]]
    assert o["id"] in ids("") and o["id"] in ids("?as=operator")
    assert o["id"] not in ids("?as=customer")                 # чужой заказ не попадает в «Мои заказы»
    assert world.customer.get("/api/v1/orders?as=operator").status_code == 403
    assert world.customer.get("/api/v1/orders?as=driver").status_code == 422
