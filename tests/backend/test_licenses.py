"""Квалификация операторов: лицензии на типы БПЛА и допуск к заказам на дроны этих типов."""
from __future__ import annotations

from test_consignees_approval import PNG

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _model_drone(world):
    """Тип БПЛА из справочника и открытый для заказов дрон этого типа."""
    m = world.admin.post("/api/v1/catalog/drone-models", json={
        "model": "Курьер-К", "manufacturer": "АэроТех", "link_type": "4G", "construction": "Квадрокоптер",
        "engine_type": "Электрический", "max_range_m": 20000, "max_payload_g": 2500}).json()
    r = world.technician.post("/api/v1/drones", json={"serial_number": "SN-LIC", "registration_number": "RU-LIC",
                                                       "manufacturer": "xx", "model": "y", "max_payload_g": 100,
                                                       "max_range_m": 1000, "fleet_id": world.fleet_id,
                                                       "drone_model_id": m["id"]})
    d = r.json()["id"]
    world.admin.post(f"/api/v1/drones/{d}/decision", json={"action": "approve"})
    world.technician.post(f"/api/v1/drones/{d}/inspections",
                          json={"result": "pass", "set_status": "serviceable", "battery_pct": 95, "comment": "ok"})
    world.technician.post(f"/api/v1/drones/{d}/key")
    assert world.technician.post(f"/api/v1/drones/{d}/publish", json={"published": True}).status_code == 200
    return m, d


def _submit(op, model_id, number="Л-001", scan=PNG):
    return op.post(f"/api/v1/me/licenses?drone_model_id={model_id}&license_number={number}&issued_on=2026-01-15"
                   f"&issued_by=Росавиация&filename=lic.png", content=scan)


def _queue_ids(world):
    return [x["id"] for x in world.operator.get("/api/v1/orders/queue").json()["items"]]


def test_license_flow_and_order_admission(world, api):
    m, drone = _model_drone(world)
    o = world.placed_order()                                  # обычный дрон без типа — допуск не нужен
    typed = world.create_order(drone_id=drone)
    world.customer.post(f"/api/v1/orders/{typed['id']}/submit", json={})
    world.customer.post(f"/api/v1/orders/{typed['id']}/confirm", json={})
    world.coordinator_approve(typed["id"])
    assert o["id"] in _queue_ids(world) and typed["id"] not in _queue_ids(world)
    r = world.operator.post(f"/api/v1/orders/{typed['id']}/accept", json={})
    assert r.status_code in (403, 404)
    # заявка: скан обязателен и только изображение/PDF
    assert _submit(world.operator, m["id"], scan=b"").status_code == 422
    assert _submit(world.operator, m["id"], scan=b"plain text").status_code == 422
    r = _submit(world.operator, m["id"])
    assert r.status_code == 201, r.text
    lic = r.json()
    assert lic["status"] == "pending" and lic["status_label"] == "На согласовании" and lic["model"] == "Курьер-К"
    assert lic["link_type"] == "4G" and lic["issued_by"] == "Росавиация"
    assert _submit(world.operator, m["id"], number="Л-002").status_code == 409          # уже есть заявка
    assert world.operator.get(lic["scan_url"]).content == PNG
    rec = api("recruiter")
    assert any(x["id"] == lic["id"] for x in rec.get("/api/v1/licenses?status=pending").json()["items"])
    assert world.customer.get("/api/v1/licenses").status_code == 403
    assert api("operator2").get(f"/api/v1/licenses/{lic['id']}").status_code == 404
    # «Исправить» — с комментарием; оператор правит и отправляет снова (скан прежний)
    assert rec.post(f"/api/v1/licenses/{lic['id']}/decision", json={"status": "fix"}).status_code == 422
    r = rec.post(f"/api/v1/licenses/{lic['id']}/decision", json={"status": "fix", "comment": "Нечитаемый скан"})
    assert r.json()["status"] == "fix" and typed["id"] not in _queue_ids(world)
    r = world.operator.put(f"/api/v1/me/licenses/{lic['id']}?license_number=Л-001А&issued_on=2026-01-15&issued_by=Росавиация",
                           content=b"")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending" and r.json()["license_number"] == "Л-001А"
    # согласование — заказы на этот тип видны и принимаются
    assert world.admin.post(f"/api/v1/licenses/{lic['id']}/decision", json={"status": "approved"}).json()["status"] == "approved"
    assert typed["id"] in _queue_ids(world)
    # отклонение после согласования — снова не видно
    assert rec.post(f"/api/v1/licenses/{lic['id']}/decision", json={"status": "rejected"}).json()["status_label"] == \
        "Отклонена/Аннулирована"
    assert typed["id"] not in _queue_ids(world)
    assert world.operator.post(f"/api/v1/orders/{typed['id']}/accept", json={}).status_code in (403, 404)
    # после отклонения можно подать новую заявку (PDF)
    r = _submit(world.operator, m["id"], number="Л-777", scan=PDF)
    assert r.status_code == 201 and r.json()["scan_type"] == "application/pdf"
    assert r.json()["scan_url"] != lic["scan_url"] and world.operator.get(r.json()["scan_url"]).content == PDF
    world.admin.post(f"/api/v1/licenses/{r.json()['id']}/decision", json={"status": "approved"})
    assert world.operator.post(f"/api/v1/orders/{typed['id']}/accept", json={}).status_code == 200
    hist = [h["action"] for h in world.admin.get(f"/api/v1/people/{world.operator.user['id']}/history").json()["items"]]
    assert "operator.license_submitted" in hist and "operator.license_decided" in hist


def test_operator_lists_models_and_own_licenses(world):
    m, _ = _model_drone(world)
    assert any(x["id"] == m["id"] for x in world.operator.get("/api/v1/catalog/drone-models").json()["items"])
    _submit(world.operator, m["id"])
    mine = world.operator.get("/api/v1/me/licenses").json()["items"]
    assert len(mine) == 1 and mine[0]["full_name"]
    assert world.customer.get("/api/v1/me/licenses").status_code == 403
