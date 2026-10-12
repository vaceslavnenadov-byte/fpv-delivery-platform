"""Таблица прав: проверка на backend для каждой роли, защита от несанкционированных изменений."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import text

from fpv import db as dbmod

ROOT = Path(__file__).resolve().parents[2]

# (роль, метод, путь, ожидаемый код при отказе)
DENIED = [
    ("customer", "get", "/api/v1/admin/users"),
    ("customer", "get", "/api/v1/admin/audit"),
    ("customer", "get", "/api/v1/orders/queue"),
    ("customer", "get", "/api/v1/drones?scope=all"),
    ("customer", "post", "/api/v1/admin/directory/sync"),
    ("operator", "get", "/api/v1/admin/users"),
    ("operator", "get", "/api/v1/drones?scope=all"),
    ("coordinator", "get", "/api/v1/orders/queue"),
    ("coordinator", "get", "/api/v1/admin/audit"),
    ("technician", "get", "/api/v1/orders?scope=all"),
    ("technician", "get", "/api/v1/admin/monitoring"),
    ("technician", "post", "/api/v1/admin/directory/sync"),
]


@pytest.mark.parametrize("role,method,path", DENIED)
def test_role_cannot_call_foreign_endpoints(api, role, method, path):
    a = api(role)
    r = a.post(path) if method == "post" else a.get(path)
    assert r.status_code == 403, (role, path, r.status_code, r.text)


def test_only_customer_creates_orders_and_drones(world):
    body = world.order_payload()
    for client in (world.operator, world.technician, world.coordinator, world.admin):
        r = client.post("/api/v1/orders", json=body)
        assert r.status_code == 403 and r.json()["error"]["details"]["permission"] == "order.create"
        r = client.post("/api/v1/drones", json={"serial_number": "SN-X", "registration_number": "RU-X",
                                                "manufacturer": "Maker", "model": "X1", "max_payload_g": 500,
                                                "max_range_m": 5000})
        assert r.status_code == 403


def test_customer_cannot_see_foreign_order(world, api):
    o = world.create_order()
    other = api("customer2")
    assert other.get(f"/api/v1/orders/{o['id']}").status_code == 404
    assert other.post(f"/api/v1/orders/{o['id']}/cancel", json={"reason": "x"}).status_code == 404


def test_customer_cannot_use_foreign_drone(world, api):
    other = api("customer2")
    body = world.order_payload()
    r = other.post("/api/v1/orders", json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "drone_invalid"


def test_operator_cannot_act_on_foreign_assignment(world, api, app):
    o = world.placed_order()
    assert world.operator.post(f"/api/v1/orders/{o['id']}/accept", json={}).status_code == 200
    op2 = api("operator2")
    op2.post("/api/v1/me/operator-application", json={"license_number": "OP-2"})
    world.admin.post(f"/api/v1/admin/operators/{op2.user['id']}/decision", json={"approve": True})
    r = op2.post(f"/api/v1/orders/{o['id']}/start", json={})
    assert r.status_code in (403, 404)


def test_coordinator_has_no_operator_rights(world):
    o = world.placed_order()
    coord_id = world.coordinator.user["id"]
    r = world.admin.post(f"/api/v1/orders/{o['id']}/coordinator", json={"coordinator_id": coord_id})
    assert r.status_code == 200, r.text
    assert world.coordinator.get(f"/api/v1/orders/{o['id']}").status_code == 200
    r = world.coordinator.post(f"/api/v1/orders/{o['id']}/accept", json={})
    assert r.status_code == 403
    r = world.coordinator.post(f"/api/v1/orders/{o['id']}/notes",
                               json={"kind": "message_to_operator", "body": "Ветер усиливается"})
    assert r.status_code == 201


def test_customer_cannot_see_drone_position(world):
    o = world.started_order()
    world.drone.telemetry()
    assert world.customer.get(f"/api/v1/orders/{o['id']}/telemetry").status_code == 404
    items = world.operator.get(f"/api/v1/orders/{o['id']}/telemetry").json()["items"]
    assert items and items[-1]["lat"] is not None


def test_technician_only_sets_serviceable(world, api):
    r = world.customer.post(f"/api/v1/drones/{world.drone_id}/inspections",
                            json={"result": "pass", "set_status": "serviceable"})
    assert r.status_code == 403
    r = world.technician.post(f"/api/v1/drones/{world.drone_id}/inspections",
                              json={"result": "fail", "set_status": "serviceable"})
    assert r.status_code == 422


def test_unknown_fields_rejected(world):
    body = world.order_payload()
    body["status"] = "completed"          # попытка подменить статус через API
    r = world.customer.post("/api/v1/orders", json=body)
    assert r.status_code == 422


def test_every_permission_used_in_code_exists(app):
    used = set()
    for path in (ROOT / "backend" / "fpv").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'(?:require|has)\("([a-z_.]+)"\)', src))
    with dbmod.get_engine().connect() as c:
        known = {r[0] for r in c.execute(text("SELECT code FROM permissions"))}
    assert used, "не найдено ни одной проверки прав"
    assert used <= known, used - known


def test_matrix_exposed_to_admin(api):
    data = api("admin").get("/api/v1/admin/roles").json()
    roles = {r["code"]: set(r["permissions"]) for r in data["roles"]}
    assert "order.accept" in roles["operator"]
    assert "order.accept" not in roles["coordinator"]
    assert "user.manage" in roles["admin"] and "order.create" not in roles["admin"]
