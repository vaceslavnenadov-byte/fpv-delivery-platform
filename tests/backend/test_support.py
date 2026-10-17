"""Техподдержка: обращения, переписка, вложения, статусы, участники."""
from __future__ import annotations

import struct

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 4, 4) + b"\x08\x06\x00\x00\x00" + b"\x00" * 32
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def test_ticket_flow(world, api):
    meta = world.customer.get("/api/v1/support/meta").json()
    assert list(meta["reasons"].values()) == ["По заказу", "По аккаунту", "По группе", "По работе сервиса",
                                              "Предложения", "Другое"]
    o = world.create_order()
    r = world.customer.post("/api/v1/support/tickets", json={"reason": "order", "subject": "Не вижу трансляцию",
                                                             "body": "Чёрный экран", "order_id": o["id"]})
    assert r.status_code == 201 and r.json()["status_label"] == "Новое"
    tid = r.json()["id"]
    # фото и видео; прочее не принимается
    up = f"/api/v1/support/tickets/{tid}/files"
    assert world.customer.client.post(f"{up}?filename=screen.png&comment=Скриншот", content=PNG,
                                      headers=world.customer._headers()).status_code == 201
    assert world.customer.client.post(f"{up}?filename=v.mp4", content=MP4, headers=world.customer._headers()).status_code == 201
    assert world.customer.client.post(f"{up}?filename=x.html", content=b"<html>", headers=world.customer._headers()).status_code == 422
    # администратор видит в «Обращениях в ТП», с фильтрами
    adm = world.admin.get("/api/v1/support/admin/tickets?status=new&reason=order").json()
    assert [t["id"] for t in adm["items"]] == [tid] and adm["items"][0]["author"]["username"] == "customer"
    assert world.admin.get("/api/v1/support/admin/tickets?q=трансляц").json()["items"][0]["id"] == tid
    assert world.customer.get("/api/v1/support/admin/tickets").status_code == 403
    assert any("Обращение в ТП" in n["title"] for n in world.admin.get("/api/v1/me/notifications").json()["items"])
    d = world.admin.get(f"/api/v1/support/tickets/{tid}").json()
    att = [a for m in d["messages"] for a in m["attachments"]]
    assert {a["content_type"] for a in att} == {"image/png", "video/mp4"}
    img = world.customer.get(att[0]["url"])
    assert img.status_code == 200 and img.content.startswith(b"\x89PNG")
    # посторонний не видит обращение и вложения
    tech = world.technician
    assert tech.get(f"/api/v1/support/tickets/{tid}").status_code == 404
    assert tech.get(att[0]["url"]).status_code == 404
    # переписка, статус, добавление участника
    assert world.admin.post(f"/api/v1/support/tickets/{tid}/messages", json={"body": "Проверяем"}).status_code == 201
    assert world.customer.post(f"/api/v1/support/tickets/{tid}/status", json={"status": "done"}).status_code == 403
    assert world.admin.post(f"/api/v1/support/tickets/{tid}/status", json={"status": "in_progress"}).status_code == 200
    found = world.admin.get("/api/v1/support/users?q=technician").json()["items"]
    tid_user = next(u["id"] for u in found if u["username"] == "technician")
    assert world.admin.post(f"/api/v1/support/tickets/{tid}/participants", json={"user_id": tid_user}).status_code == 200
    t = tech.get(f"/api/v1/support/tickets/{tid}").json()
    assert t["status_label"] == "В работе" and {p["username"] for p in t["participants"]} >= {"customer", "admin", "technician"}
    assert tech.post(f"/api/v1/support/tickets/{tid}/messages", json={"body": "Дрон исправен"}).status_code == 201
    mine = {x["id"]: x for x in world.customer.get("/api/v1/support/tickets").json()["items"]}
    assert mine[tid]["status"] == "in_progress" and mine[tid]["unread"] is True
    assert [x["id"] for x in tech.get("/api/v1/support/tickets").json()["items"]] == [tid]
    assert world.admin.post(f"/api/v1/support/tickets/{tid}/status", json={"status": "done"}).json()["status_label"] == "Выполнено"
    assert any("Выполнено" in n["title"] for n in world.customer.get("/api/v1/me/notifications").json()["items"])


def test_suspended_user_can_still_write_support(world):
    world.admin.post(f"/api/v1/people/{world.customer.user['id']}/status", json={"status": "suspended", "reason": "проверка"})
    r = world.customer.post("/api/v1/support/tickets", json={"reason": "account", "subject": "Почему приостановлен?"})
    assert r.status_code == 201
