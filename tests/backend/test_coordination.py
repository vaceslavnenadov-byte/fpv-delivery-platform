"""Автоматическое назначение координаторов и выравнивание нагрузки; видеостена координатора."""
from __future__ import annotations

import uuid

from fpv import db as dbmod
from fpv import runtime
from fpv.models import Order
from fpv.services import coordination


def _accepted_orders(world, n):
    """n заказов в статусе «принят» без координатора (оператор принимает только один — остальные через БД)."""
    ids = []
    for i in range(n):
        drone_id, _ = world.make_drone(world.technician, f"SN-C{i:03d}", f"RU-C{i:03d}")
        ids.append(world.create_order(drone_id=drone_id)["id"])
    with dbmod.new_session() as s:
        for i in ids:
            o = s.get(Order, uuid.UUID(i))
            o.status, o.operator_id, o.coordinator_id = "accepted", uuid.UUID(world.operator.user["id"]), None
            o.accepted_at = o.created_at
        s.commit()
    return ids


def _loads(world, coords):
    with dbmod.new_session() as s:
        ld = coordination.loads(s, [uuid.UUID(c.user["id"]) for c in coords])
    return sorted(ld.values())


def test_accept_assigns_least_loaded_coordinator(world, api):
    c2, c3 = api("coordinator2"), api("coordinator3")
    o = world.placed_order()
    r = world.operator.post(f"/api/v1/orders/{o['id']}/accept", json={})
    assert r.status_code == 200
    detail = world.admin.get(f"/api/v1/orders/{o['id']}").json()
    assert detail["coordinator_id"] in {world.coordinator.user["id"], c2.user["id"], c3.user["id"]}
    log = world.admin.get("/api/v1/admin/audit?action=order.coordinator_assigned").json()["items"]
    assert any(e["entity_id"] == o["id"] and e["details"].get("auto") for e in log)


def test_sweep_assigns_evenly_and_rebalances(world, api):
    coords = [world.coordinator, api("coordinator2"), api("coordinator3")]
    _accepted_orders(world, 7)
    with dbmod.new_session() as s:
        stats = coordination.sweep(s, runtime.settings())
        s.commit()
    assert stats["assigned"] == 7
    assert _loads(world, coords) == [2, 2, 3]                  # разница не больше 1
    # Перекос: все заказы у одного — фоновая задача выравнивает.
    with dbmod.new_session() as s:
        s.query(Order).filter(Order.status == "accepted").update(
            {Order.coordinator_id: uuid.UUID(world.coordinator.user["id"])})
        s.commit()
    with dbmod.new_session() as s:
        stats = coordination.sweep(s, runtime.settings())
        s.commit()
    assert stats["moved"] >= 4 and max(_loads(world, coords)) - min(_loads(world, coords)) <= 1


def test_capacity_limit(world, api, monkeypatch):
    monkeypatch.setattr(runtime.settings(), "coordinator_max_orders", 2)
    _accepted_orders(world, 3)                                 # один координатор, предел 2
    with dbmod.new_session() as s:
        stats = coordination.sweep(s, runtime.settings())
        s.commit()
    assert stats["assigned"] == 2 and _loads(world, [world.coordinator]) == [2]


def test_coordinator_wall(world, api):
    o = world.started_order()
    wall = world.coordinator.get("/api/v1/coordinator/wall").json()
    assert [x["id"] for x in wall["items"]] == [o["id"]] and wall["capacity"] == 9 and wall["load"] == 1
    assert wall["items"][0]["drone"]["registration_number"] == "RU-0001"
    assert world.operator.get("/api/v1/coordinator/wall").status_code == 403
    assert api("coordinator2").get("/api/v1/coordinator/wall").json()["items"] == []
