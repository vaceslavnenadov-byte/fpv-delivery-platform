"""Журнал аудита: полнота, неизменяемость, обнаружение подмены."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from conftest import PG, DB_NAME
from fpv import db as dbmod


def test_admin_actions_are_audited(world):
    actions = {a["action"] for a in world.admin.get("/api/v1/admin/audit?limit=500").json()["items"]}
    assert {"auth.login", "operator.decision", "drone.approve", "drone.inspection", "drone.key_issued",
            "user.provisioned", "user.roles_synced"} <= actions
    entry = world.admin.get("/api/v1/admin/audit?action=drone.approve").json()["items"][0]
    assert entry["actor"] == "admin" and entry["details"]["before"] == "pending_approval"
    assert entry["details"]["after"] == "active" and entry["occurred_at"]


def test_audit_chain_verifies(world):
    world.placed_order()
    r = world.admin.post("/api/v1/admin/audit/verify")
    assert r.json()["ok"] is True and r.json()["checked"] > 10


def test_journals_are_append_only(world):
    o = world.placed_order()
    with dbmod.get_engine().connect() as c:
        for sql in ("UPDATE audit_logs SET action='x'", "DELETE FROM audit_logs", "TRUNCATE audit_logs",
                    "UPDATE order_status_history SET reason='x'", "DELETE FROM order_status_history",
                    "DELETE FROM users"):
            with pytest.raises(DBAPIError):
                c.execute(text(sql))
            c.rollback()


def test_tampering_detected_by_hash_chain(world):
    world.placed_order()
    # Злоумышленник с правами суперпользователя отключает триггер и правит запись.
    import os
    su_url = os.environ.get("FPV_TEST_PG_SUPERUSER", PG.replace("fpv:fpv@", "postgres@"))
    su = create_engine(f"{su_url}/{DB_NAME}", future=True)
    try:
        su.connect().close()
    except DBAPIError:
        pytest.skip("нет доступа суперпользователя PostgreSQL (FPV_TEST_PG_SUPERUSER)")
    with su.begin() as c:
        c.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER audit_logs_immutable"))
        c.execute(text("UPDATE audit_logs SET details = '{\"forged\": true}' "
                       "WHERE id = (SELECT min(id) + 3 FROM audit_logs)"))
        c.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER audit_logs_immutable"))
    su.dispose()
    r = world.admin.post("/api/v1/admin/audit/verify").json()
    assert r["ok"] is False and r["broken_at"] is not None


def test_secrets_are_scrubbed_from_audit(world):
    from fpv.services import audit
    with dbmod.session_scope() as s:
        audit.record(s, audit.Actor.system(), "test.secret", details={"password": "p", "nested": {"token": "t"}})
    entry = world.admin.get("/api/v1/admin/audit?action=test.secret").json()["items"][0]
    assert entry["details"] == {"password": "***", "nested": {"token": "***"}}


def test_denied_access_is_audited(api, app):
    from conftest import Api
    Api(app).login("customer", password="bad")
    Api(app).login("norole")
    admin = api("admin")
    denied = admin.get("/api/v1/admin/audit?action=auth.login").json()["items"]
    reasons = {e["details"].get("reason") for e in denied if e["outcome"] == "denied"}
    assert {"invalid_credentials", "no_application_roles"} <= reasons


def test_user_deletion_keeps_history(world):
    o = world.placed_order()
    uid = world.customer.user["id"]
    r = world.admin.post(f"/api/v1/admin/users/{uid}/status", json={"status": "deleted", "reason": "Запрос субъекта"})
    assert r.status_code == 200
    detail = world.admin.get(f"/api/v1/orders/{o['id']}").json()
    assert detail["customer"]["display_name"] == "Удалённый пользователь"
    assert len(world.admin.get(f"/api/v1/orders/{o['id']}/history").json()["items"]) == 4
