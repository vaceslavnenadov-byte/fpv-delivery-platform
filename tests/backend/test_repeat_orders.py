"""Повторный вылет: запрос заказчика, согласование координатором, приоритетный заказ."""
from __future__ import annotations

from test_scoring import _deliver, _review


def test_repeat_flow(world, fake_clock, api):
    o = _deliver(world, fake_clock)
    _review(world, o)
    assert world.status(o["id"]) == "completed"
    plain = world.create_order()                      # обычный заказ для сравнения порядка в очереди
    world.customer.post(f"/api/v1/orders/{plain['id']}/submit", json={})
    world.customer.post(f"/api/v1/orders/{plain['id']}/confirm", json={})
    # запрос — только заказчик
    assert world.operator.post(f"/api/v1/orders/{o['id']}/repeat", json={}).status_code == 403
    r = world.customer.post(f"/api/v1/orders/{o['id']}/repeat", json={"comment": "Нужно доставить ещё один комплект"})
    assert r.status_code == 201 and r.json()["status"] == "pending"
    rid = r.json()["id"]
    assert world.customer.post(f"/api/v1/orders/{o['id']}/repeat", json={}).status_code == 201   # без ограничений
    pend = world.coordinator.get("/api/v1/orders/repeat-requests").json()["items"]
    assert [x["id"] for x in pend][:1] == [rid] and pend[0]["order_number"] == o["number"]
    assert any("повторного вылета" in n["title"] for n in world.coordinator.get("/api/v1/me/notifications").json()["items"])
    # решает координатор этого заказа
    assert api("coordinator2").post(f"/api/v1/orders/repeat-requests/{rid}/decision", json={"approve": True}).status_code == 403
    assert world.coordinator.post(f"/api/v1/orders/repeat-requests/{rid}/decision",
                                  json={"approve": False}).status_code == 422              # отказ — с причиной
    d = world.coordinator.post(f"/api/v1/orders/repeat-requests/{rid}/decision", json={"approve": True, "comment": "Ок"})
    assert d.status_code == 200, d.text
    new_id = d.json()["new_order"]["id"]
    new = world.customer.get(f"/api/v1/orders/{new_id}").json()
    assert new["status"] == "awaiting_operator" and new["priority"] is True
    assert new["repeat_of"]["number"] == o["number"] and new["coordinator_id"] == world.coordinator.user["id"]
    assert new["route"]["destination"] == o["route"]["destination"]
    # в очереди операторов и у техника — первым
    queue = world.operator.get("/api/v1/orders/queue").json()["items"]
    assert queue[0]["id"] == new_id and queue[0]["priority"] is True and len(queue) >= 2
    fleet = world.technician.get("/api/v1/fleet-orders").json()["items"]
    assert fleet[0]["id"] == new_id
    # повторно решить нельзя; исходный заказ показывает историю запросов
    assert world.coordinator.post(f"/api/v1/orders/repeat-requests/{rid}/decision",
                                  json={"approve": True}).status_code == 409
    reqs = world.customer.get(f"/api/v1/orders/{o['id']}").json()["repeat_requests"]
    assert {x["status"] for x in reqs} == {"approved", "pending"}
    assert any("Повторный вылет согласован" in n["title"] for n in world.customer.get("/api/v1/me/notifications").json()["items"])


def test_repeat_not_for_drafts(world):
    o = world.create_order()
    assert world.customer.post(f"/api/v1/orders/{o['id']}/repeat", json={}).status_code == 409
