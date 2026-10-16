"""Предварительное рассмотрение претензий координатором заказа."""
from __future__ import annotations

from test_scoring import _deliver, _review


def _dispute(world, fake_clock, *, after_review=False):
    o = _deliver(world, fake_clock, receipt=False)
    if after_review:
        world.customer.post(f"/api/v1/orders/{o['id']}/receipt", json={"received": True})
        _review(world, o)
    r = world.customer.post(f"/api/v1/orders/{o['id']}/dispute", json={"description": "Упаковка повреждена"})
    assert r.status_code == 201
    assert world.status(o["id"]) == "disputed"
    return o, r.json()["complaint_id"]


def _titles(client):
    return [n["title"] for n in client.get("/api/v1/me/notifications").json()["items"]]


def test_coordinator_upholds_and_escalates_to_admin(world, fake_clock):
    o, cid = _dispute(world, fake_clock)
    url = f"/api/v1/orders/{o['id']}/dispute/review"
    assert world.coordinator.get(f"/api/v1/orders/{o['id']}").json()["coordinator_id"] == world.coordinator.user["id"]
    r = world.coordinator.post(url, json={"upheld": True, "comment": "По видео груз повреждён при посадке"})
    assert r.status_code == 200, r.text
    assert r.json() == {"complaint_id": cid, "status": "open", "coordinator_verdict": "upheld"}
    assert world.status(o["id"]) == "disputed"                       # решение — за администратором
    assert any("координатор удовлетворил претензию" in t for t in _titles(world.admin))
    assert any("передана администратору" in t for t in _titles(world.customer))
    # повторно рассмотреть нельзя
    assert world.coordinator.post(url, json={"upheld": False, "comment": "Передумал, отклоняю"}).status_code == 409
    # карточка заказа и список администратора показывают решение координатора
    d = world.customer.get(f"/api/v1/orders/{o['id']}").json()["dispute"]
    assert d["coordinator_verdict"] == "upheld" and d["coordinator"]["username"] == "coordinator"
    items = world.admin.get("/api/v1/admin/complaints?status=open").json()["items"]
    assert items[0]["id"] == cid and items[0]["coordinator_verdict"] == "upheld"
    # администратор принимает окончательное решение
    r = world.admin.post(f"/api/v1/admin/complaints/{cid}/resolve",
                         json={"upheld": True, "resolution": "Согласен с координатором"})
    assert r.status_code == 200
    assert world.status(o["id"]) == "admin_review"
    audit = world.admin.get("/api/v1/admin/audit?action=complaint.coordinator_review").json()["items"]
    assert len(audit) == 1 and audit[0]["details"]["upheld"] is True


def test_coordinator_dismisses_after_completion(world, fake_clock):
    o, cid = _dispute(world, fake_clock, after_review=True)
    r = world.coordinator.post(f"/api/v1/orders/{o['id']}/dispute/review",
                               json={"upheld": False, "comment": "Видео подтверждает целую упаковку"})
    assert r.status_code == 200 and r.json()["status"] == "dismissed"
    assert world.status(o["id"]) == "completed"
    s = world.operator.get(f"/api/v1/orders/{o['id']}/score").json()
    assert s["total_with_adjustments"] == s["final_points"]          # баллы не тронуты
    assert any("претензия отклонена координатором" in t for t in _titles(world.customer))
    # администратору решать больше нечего
    r = world.admin.post(f"/api/v1/admin/complaints/{cid}/resolve", json={"upheld": True, "resolution": "Повтор"})
    assert r.status_code == 409


def test_only_assigned_coordinator_can_review(world, fake_clock, api):
    o, _ = _dispute(world, fake_clock)
    url = f"/api/v1/orders/{o['id']}/dispute/review"
    body = {"upheld": True, "comment": "Претензия обоснована"}
    assert api("coordinator2").post(url, json=body).status_code == 404      # не его заказ
    assert world.customer.post(url, json=body).status_code == 403
    assert world.operator.post(url, json=body).status_code == 403
    assert world.coordinator.post(url, json={"upheld": True, "comment": "x"}).status_code == 422


def test_review_requires_disputed_status(world, fake_clock):
    o = _deliver(world, fake_clock)
    r = world.coordinator.post(f"/api/v1/orders/{o['id']}/dispute/review",
                               json={"upheld": False, "comment": "Нечего рассматривать"})
    assert r.status_code == 409
