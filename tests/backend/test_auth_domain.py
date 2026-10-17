"""Доменная аутентификация, сопоставление групп, отзыв доступа, сессии."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import text

from conftest import PASSWORD, Api
from fpv import db as dbmod
from fpv.config import Settings
from fpv.services.directory import (AccountDisabled, DirectoryUnavailable, InvalidCredentials,
                                    LdapDirectoryProvider, normalize_username)


def _scalar(sql, **params):
    with dbmod.get_engine().connect() as c:
        return c.execute(text(sql), params).scalar()


def test_domain_login_maps_groups_to_roles(api):
    a = api("operator")
    me = a.get("/api/v1/auth/me").json()
    assert me["roles"] == ["operator"]
    assert me["groups"] == ["FPV-Operators"]
    # До подтверждения администратором права оператора на заказы не действуют.
    assert "order.accept" not in me["permissions"]
    assert a.get("/api/v1/orders/queue").status_code == 403


def test_login_variants_of_username(api, app):
    for name in ("CUSTOMER", "FPV\\customer", "customer@fpv.local"):
        a = Api(app)
        assert a.login(name).status_code == 200, name


def test_invalid_password_rejected_and_audited(api, app):
    a = Api(app)
    r = a.login("customer", password="wrong")
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"
    assert _scalar("SELECT count(*) FROM audit_logs WHERE action='auth.login' AND outcome='denied'") == 1


def test_domain_password_never_stored(api, app):
    Api(app).login("customer", password="Wrong-Secret-123")
    api("customer")
    with dbmod.get_engine().connect() as c:
        tables = [r[0] for r in c.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))]
        for t in tables:
            dump = c.execute(text(f"SELECT coalesce(string_agg(t::text, ' '), '') FROM {t} t")).scalar()
            assert PASSWORD not in dump, t
            assert "Wrong-Secret-123" not in dump, t
        # хеш пароля есть только у учётных записей приложения; у доменных — никогда
        leaked = c.execute(text("SELECT count(*) FROM users WHERE auth_source = 'domain' "
                                "AND (password_hash IS NOT NULL OR password_changed_at IS NOT NULL)")).scalar()
    assert leaked == 0


def test_directory_unavailable_returns_503(app, directory):
    directory.set_available(False)
    r = Api(app).login("customer")
    assert r.status_code == 503 and r.json()["error"]["code"] == "directory_unavailable"
    assert _scalar("SELECT details->>'reason' FROM audit_logs WHERE action='auth.login' ORDER BY id DESC LIMIT 1") \
        == "directory_unavailable"


def test_existing_session_survives_short_directory_outage(api, directory):
    a = api("customer")
    directory.set_available(False)
    assert a.get("/api/v1/auth/me").status_code == 200


def test_disabled_domain_account_cannot_login(app, directory):
    directory.set_enabled("customer", False)
    r = Api(app).login("customer")
    assert r.status_code == 401 and r.json()["error"]["code"] == "account_disabled"


def test_access_revoked_after_domain_block(api, directory, fake_clock):
    a = api("customer")
    assert a.get("/api/v1/auth/me").status_code == 200
    directory.set_enabled("customer", False)
    fake_clock.advance(seconds=301)   # интервал повторной проверки каталога
    r = a.get("/api/v1/auth/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "access_revoked"
    assert _scalar("SELECT count(*) FROM sessions WHERE revoke_reason='domain_account_disabled'") == 1
    assert _scalar("SELECT directory_enabled FROM users WHERE domain_username='customer'") is False
    # Сессия остаётся отозванной даже после разблокировки в каталоге — нужен новый вход.
    directory.set_enabled("customer", True)
    assert a.get("/api/v1/auth/me").status_code == 401


def test_directory_sync_job_revokes_deleted_accounts(api, directory):
    a = api("customer")
    admin = api("admin")
    directory.remove("customer")
    r = admin.post("/api/v1/admin/directory/sync")
    assert r.status_code == 200 and r.json()["disabled"] == 1
    assert a.get("/api/v1/auth/me").status_code == 401


def test_group_change_updates_roles(api, directory, fake_clock):
    a = api("customer")
    directory.set_groups("customer", ["FPV-Customers", "FPV-Technicians"])
    fake_clock.advance(seconds=301)
    me = a.get("/api/v1/auth/me").json()
    assert me["roles"] == ["customer", "technician"]
    assert _scalar("SELECT count(*) FROM audit_logs WHERE action='user.roles_synced'") >= 2


def test_user_without_application_role_denied(app):
    r = Api(app).login("norole")
    assert r.status_code == 403 and r.json()["error"]["code"] == "no_roles"


def test_admin_disable_user_revokes_sessions(api):
    c = api("customer")
    admin = api("admin")
    uid = c.user["id"]
    r = admin.post(f"/api/v1/admin/users/{uid}/status", json={"status": "disabled", "reason": "Проверка"})
    assert r.status_code == 200
    assert c.get("/api/v1/auth/me").status_code == 401
    assert Api(c.client.app).login("customer").status_code == 401


def test_csrf_required_for_mutations(api):
    a = api("customer")
    r = a.client.post("/api/v1/me/notifications/read")    # без заголовка X-CSRF-Token
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    assert a.post("/api/v1/me/notifications/read").status_code == 200


def test_logout_revokes_session(api):
    a = api("customer")
    assert a.post("/api/v1/auth/logout").status_code == 200
    a.client.cookies.set("fpv_session", "forged")
    assert a.get("/api/v1/auth/me").status_code == 401


def test_session_idle_timeout(api, fake_clock):
    a = api("customer")
    fake_clock.advance(hours=5)
    assert a.get("/api/v1/auth/me").json()["error"]["code"] == "session_expired"


def test_login_rate_limit(app):
    from fpv.api import deps
    deps.init_limiters(3, 1000)
    a = Api(app)
    codes = [a.login("customer", password="bad").status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]


# --- доступ к устройствам по таблице прав ---

def test_device_access_policy(api, app):
    admin = api("admin")
    r = admin.post("/api/v1/admin/devices", json={"code": "WS-OPS-01", "kind": "workstation",
                                                  "hostname": "ws-ops-01.fpv.local", "domain_joined": True})
    dev = r.json()["id"]
    assert admin.post(f"/api/v1/admin/devices/{dev}/policies", json={"role": "operator"}).status_code == 201
    assert admin.post(f"/api/v1/admin/devices/{dev}/policies",
                      json={"domain_group": "FPV-Coordinators"}).status_code == 201
    assert Api(app).login("operator", device_code="WS-OPS-01").status_code == 200
    assert Api(app).login("coordinator", device_code="WS-OPS-01").status_code == 200
    r = Api(app).login("customer", device_code="WS-OPS-01")
    assert r.status_code == 403 and r.json()["error"]["code"] == "device_denied"
    assert Api(app).login("operator", device_code="UNKNOWN-01").status_code == 403


# --- LDAP-провайдер (Active Directory) на эмуляторе ldap3 ---

def _mock_ad():
    import ldap3
    server = ldap3.Server("fake-ad")
    base = "DC=fpv,DC=local"
    entries = {
        "CN=svc-fpv,OU=Service,DC=fpv,DC=local": {"objectClass": ["user"], "sAMAccountName": "svc-fpv",
                                                 "userPassword": "svc-secret"},
        "CN=Ivan Petrov,OU=Users,DC=fpv,DC=local": {
            "objectClass": ["user"], "sAMAccountName": "ipetrov", "displayName": "Иван Петров",
            "mail": "ipetrov@fpv.local", "userPrincipalName": "ipetrov@fpv.local",
            "objectGUID": "{2b7c1f9e-3d4a-4c8b-9a1e-5f6d7e8c9b0a}",
            "userAccountControl": "512", "userPassword": "Ivan-Pass-1",
            "memberOf": ["CN=FPV-Operators,OU=FPV,OU=Groups,DC=fpv,DC=local"]},
        "CN=Blocked User,OU=Users,DC=fpv,DC=local": {
            "objectClass": ["user"], "sAMAccountName": "blocked", "displayName": "Blocked",
            "objectGUID": "{11111111-2222-3333-4444-555555555555}", "userAccountControl": "514",
            "userPassword": "Blocked-1", "memberOf": []},
    }

    def factory(user, password):
        conn = ldap3.Connection(server, user=user, password=password, client_strategy=ldap3.MOCK_SYNC)
        for dn, attrs in entries.items():
            conn.strategy.add_entry(dn, attrs)
        if not conn.bind():
            raise InvalidCredentials("bad")
        return conn

    settings = Settings(environment="test", directory_provider="ldap", ldap_base_dn=base,
                        ldap_bind_dn="CN=svc-fpv,OU=Service,DC=fpv,DC=local", ldap_bind_password="svc-secret",
                        ldap_nested_groups=False)
    return LdapDirectoryProvider(settings, connection_factory=factory)


def test_ldap_provider_authenticates_ad_user():
    provider = _mock_ad()
    ident = provider.authenticate("FPV\\ipetrov", "Ivan-Pass-1")
    assert ident.username == "ipetrov"
    assert ident.object_id == "2b7c1f9e-3d4a-4c8b-9a1e-5f6d7e8c9b0a"
    assert ident.groups == ["CN=FPV-Operators,OU=FPV,OU=Groups,DC=fpv,DC=local"]
    assert ident.enabled


def test_ldap_provider_rejects_wrong_password_and_disabled():
    provider = _mock_ad()
    with pytest.raises(InvalidCredentials):
        provider.authenticate("ipetrov", "wrong")
    with pytest.raises(InvalidCredentials):
        provider.authenticate("ipetrov", "")          # запрет анонимного bind
    with pytest.raises(AccountDisabled):
        provider.authenticate("blocked", "Blocked-1")
    assert provider.lookup("nobody") is None
    assert provider.lookup("blocked").enabled is False


def test_ldap_injection_in_username_rejected():
    provider = _mock_ad()
    for bad in ("*)(sAMAccountName=*", "ipetrov)(|(a=*", "a b"):
        with pytest.raises(InvalidCredentials):
            provider.authenticate(bad, "x")
    with pytest.raises(InvalidCredentials):
        normalize_username("")


def test_ldap_unreachable_server_is_unavailable():
    settings = Settings(environment="test", directory_provider="ldap", ldap_uri="ldap://127.0.0.1:1",
                        ldap_bind_dn="CN=svc", ldap_bind_password="x", ldap_timeout_s=1)
    with pytest.raises(DirectoryUnavailable):
        LdapDirectoryProvider(settings).authenticate("ipetrov", "x")


def test_production_requires_domain_auth_and_tls():
    from fpv.config import ConfigError
    with pytest.raises(ConfigError):
        Settings(environment="production", directory_provider="static").validate()
    with pytest.raises(ConfigError):
        Settings(environment="production", directory_provider="ldap", ldap_uri="ldap://dc1").validate()
    Settings(environment="production", directory_provider="ldap", ldap_uri="ldaps://dc1").validate()
