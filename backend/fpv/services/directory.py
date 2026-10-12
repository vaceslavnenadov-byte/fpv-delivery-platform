"""Провайдеры доменной службы каталогов.

Приложение не хранит доменные пароли: пароль используется только для проверки
(LDAP bind) и сразу отбрасывается. Провайдер возвращает идентификатор учётной
записи в каталоге, атрибуты профиля, группы и признак блокировки.

* ``LdapDirectoryProvider`` — Active Directory / Samba AD DC / FreeIPA по LDAPS
  или LDAP+StartTLS (атрибуты настраиваются).
* ``StaticDirectoryProvider`` — эмулятор каталога для разработки и тестов
  (JSON-файл с PBKDF2-хешами). В production запрещён конфигурацией.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import ssl
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from ..config import Settings

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class DirectoryError(Exception):
    pass


class InvalidCredentials(DirectoryError):
    pass


class AccountDisabled(DirectoryError):
    pass


class DirectoryUnavailable(DirectoryError):
    pass


@dataclass
class DirectoryIdentity:
    object_id: str
    username: str
    display_name: str
    email: Optional[str] = None
    upn: Optional[str] = None
    enabled: bool = True
    groups: List[str] = field(default_factory=list)   # DN групп


class DirectoryProvider:
    name = "abstract"

    def authenticate(self, username: str, password: str) -> DirectoryIdentity:
        raise NotImplementedError

    def lookup(self, username: str) -> Optional[DirectoryIdentity]:
        """Поиск учётной записи служебной учётной записью (без пароля пользователя).
        Возвращает None, если учётная запись удалена из каталога."""
        raise NotImplementedError

    def ping(self) -> bool:
        raise NotImplementedError


def normalize_username(username: str) -> str:
    username = (username or "").strip()
    if "\\" in username:                      # DOMAIN\user
        username = username.split("\\", 1)[1]
    if "@" in username:                       # user@domain
        username = username.split("@", 1)[0]
    if not USERNAME_RE.match(username):
        raise InvalidCredentials("Недопустимое имя пользователя")
    return username.lower()


# ---------------------------------------------------------------------------
# Эмулятор каталога (dev/test)
# ---------------------------------------------------------------------------

def pbkdf2_hash(password: str, salt: Optional[bytes] = None, iterations: int = 200_000) -> str:
    salt = salt or hashlib.sha256(password.encode() + b"fpv-static").digest()[:16]
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def _pbkdf2_verify(password: str, encoded: str) -> bool:
    try:
        algo, iterations, salt_b64, hash_b64 = encoded.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt_b64), int(iterations))
    return hmac.compare_digest(digest, base64.b64decode(hash_b64))


class StaticDirectoryProvider(DirectoryProvider):
    """Файл: {"available": true, "users": [{"username", "object_id", "display_name",
    "email", "password_pbkdf2", "groups": [DN...], "enabled": true}]}.
    Файл перечитывается при каждом обращении — так тесты и стенд могут
    блокировать учётные записи «в каталоге» на лету."""
    name = "static"

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DirectoryUnavailable(f"Каталог недоступен: {exc.__class__.__name__}") from exc
        if not data.get("available", True):
            raise DirectoryUnavailable("Служба каталогов недоступна")
        return data

    def _find(self, data: dict, username: str) -> Optional[dict]:
        for entry in data.get("users", []):
            if entry.get("username", "").lower() == username:
                return entry
        return None

    @staticmethod
    def _identity(entry: dict) -> DirectoryIdentity:
        return DirectoryIdentity(
            object_id=entry["object_id"], username=entry["username"].lower(),
            display_name=entry.get("display_name") or entry["username"], email=entry.get("email"),
            upn=entry.get("upn"), enabled=bool(entry.get("enabled", True)), groups=list(entry.get("groups", [])))

    def authenticate(self, username: str, password: str) -> DirectoryIdentity:
        username = normalize_username(username)
        if not password:
            raise InvalidCredentials("Пустой пароль")
        data = self._load()
        entry = self._find(data, username)
        if entry is None or not _pbkdf2_verify(password, entry.get("password_pbkdf2", "")):
            raise InvalidCredentials("Неверное имя пользователя или пароль")
        identity = self._identity(entry)
        if not identity.enabled:
            raise AccountDisabled("Учётная запись заблокирована в каталоге")
        return identity

    def lookup(self, username: str) -> Optional[DirectoryIdentity]:
        data = self._load()
        entry = self._find(data, username.lower())
        return self._identity(entry) if entry else None

    def ping(self) -> bool:
        self._load()
        return True


# ---------------------------------------------------------------------------
# LDAP (Active Directory / Samba AD / FreeIPA)
# ---------------------------------------------------------------------------

ConnectionFactory = Callable[[Optional[str], Optional[str]], object]


class LdapDirectoryProvider(DirectoryProvider):
    name = "ldap"

    def __init__(self, settings: Settings, connection_factory: Optional[ConnectionFactory] = None) -> None:
        self.s = settings
        self._factory = connection_factory or self._default_factory

    # -- подключение ------------------------------------------------------
    def _default_factory(self, user: Optional[str], password: Optional[str]):
        import ldap3
        from ldap3.core import exceptions as lexc

        tls = ldap3.Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLS_CLIENT,
                        ca_certs_file=self.s.ldap_ca_file or None)
        server = ldap3.Server(self.s.ldap_uri, use_ssl=self.s.ldap_uri.startswith("ldaps://"), tls=tls,
                              connect_timeout=self.s.ldap_timeout_s, get_info=ldap3.NONE)
        conn = ldap3.Connection(server, user=user, password=password, auto_bind=ldap3.AUTO_BIND_NONE,
                                receive_timeout=self.s.ldap_timeout_s, raise_exceptions=True, read_only=True)
        try:
            conn.open()
            if self.s.ldap_start_tls:
                conn.start_tls()
            conn.bind()
        except lexc.LDAPInvalidCredentialsResult as exc:
            raise InvalidCredentials("Неверное имя пользователя или пароль") from exc
        except lexc.LDAPBindError as exc:
            raise InvalidCredentials("Неверное имя пользователя или пароль") from exc
        except (lexc.LDAPSocketOpenError, lexc.LDAPSocketReceiveError, lexc.LDAPSocketSendError,
                lexc.LDAPSessionTerminatedByServerError, lexc.LDAPStartTLSError, OSError) as exc:
            raise DirectoryUnavailable("Служба каталогов недоступна") from exc
        return conn

    def _service_connection(self):
        if not self.s.ldap_bind_dn or not self.s.ldap_bind_password:
            raise DirectoryUnavailable("Не настроена служебная учётная запись каталога")
        try:
            return self._factory(self.s.ldap_bind_dn, self.s.ldap_bind_password)
        except InvalidCredentials as exc:
            raise DirectoryUnavailable("Ошибка входа служебной учётной записи каталога") from exc

    # -- преобразования -----------------------------------------------------
    @staticmethod
    def _first(entry, attr: str):
        try:
            values = entry[attr].values if hasattr(entry[attr], "values") else entry[attr]
        except (KeyError, IndexError, TypeError):
            return None
        if isinstance(values, (list, tuple)):
            return values[0] if values else None
        return values

    def _object_id(self, entry) -> str:
        attr = self.s.ldap_attr_object_id
        raw = None
        try:
            raw_values = entry.entry_raw_attributes.get(attr) if hasattr(entry, "entry_raw_attributes") else None
            raw = raw_values[0] if raw_values else None
        except (AttributeError, IndexError):
            raw = None
        if isinstance(raw, (bytes, bytearray)) and len(raw) == 16:
            return str(uuid.UUID(bytes_le=bytes(raw)))     # objectGUID в AD — little-endian
        value = self._first(entry, attr)
        if value is None:
            raise DirectoryError(f"У учётной записи нет атрибута {attr}")
        return str(value).strip("{}").lower()

    def _enabled(self, entry) -> bool:
        attr = self.s.ldap_attr_disabled
        value = self._first(entry, attr)
        if value is None:
            return True
        if attr.lower() == "useraccountcontrol":
            try:
                return not (int(value) & 0x2)
            except (TypeError, ValueError):
                return False
        # FreeIPA / 389-DS: nsAccountLock=TRUE означает блокировку
        return str(value).strip().lower() not in ("true", "1", "yes")

    def _groups(self, conn, entry) -> List[str]:
        if self.s.ldap_nested_groups:
            from ldap3.utils.conv import escape_filter_chars
            flt = "(member:1.2.840.113556.1.4.1941:={})".format(escape_filter_chars(entry.entry_dn))
            conn.search(self.s.ldap_base_dn, flt, attributes=["cn"])
            return [e.entry_dn for e in conn.entries]
        try:
            values = entry["memberOf"].values
        except (KeyError, AttributeError):
            values = []
        return [str(v) for v in values]

    def _search_user(self, conn, username: str):
        import ldap3
        from ldap3.utils.conv import escape_filter_chars

        flt = self.s.ldap_user_filter.format(username=escape_filter_chars(username))
        attrs = [self.s.ldap_attr_username, self.s.ldap_attr_object_id, self.s.ldap_attr_display_name,
                 self.s.ldap_attr_email, self.s.ldap_attr_upn, self.s.ldap_attr_disabled, "memberOf"]
        conn.search(self.s.ldap_base_dn, flt, search_scope=ldap3.SUBTREE, attributes=attrs, size_limit=2)
        entries = list(conn.entries)
        if len(entries) > 1:
            raise DirectoryError("Имя пользователя неоднозначно в каталоге")
        return entries[0] if entries else None

    def _to_identity(self, conn, entry, username: str) -> DirectoryIdentity:
        return DirectoryIdentity(
            object_id=self._object_id(entry),
            username=username,
            display_name=str(self._first(entry, self.s.ldap_attr_display_name) or username),
            email=(str(self._first(entry, self.s.ldap_attr_email)) if self._first(entry, self.s.ldap_attr_email) else None),
            upn=(str(self._first(entry, self.s.ldap_attr_upn)) if self._first(entry, self.s.ldap_attr_upn) else None),
            enabled=self._enabled(entry),
            groups=self._groups(conn, entry),
        )

    # -- публичный интерфейс --------------------------------------------------
    def authenticate(self, username: str, password: str) -> DirectoryIdentity:
        username = normalize_username(username)
        if not password:
            # Пустой пароль в LDAP означает анонимный (unauthenticated) bind — запрещаем.
            raise InvalidCredentials("Пустой пароль")
        svc = self._service_connection()
        try:
            entry = self._search_user(svc, username)
            if entry is None:
                raise InvalidCredentials("Неверное имя пользователя или пароль")
            identity = self._to_identity(svc, entry, username)
            user_conn = self._factory(entry.entry_dn, password)   # проверка пароля
            try:
                user_conn.unbind()
            except Exception:  # pragma: no cover - закрытие соединения не критично
                pass
        finally:
            try:
                svc.unbind()
            except Exception:  # pragma: no cover
                pass
        if not identity.enabled:
            raise AccountDisabled("Учётная запись заблокирована в каталоге")
        return identity

    def lookup(self, username: str) -> Optional[DirectoryIdentity]:
        svc = self._service_connection()
        try:
            entry = self._search_user(svc, username.lower())
            return self._to_identity(svc, entry, username.lower()) if entry is not None else None
        finally:
            try:
                svc.unbind()
            except Exception:  # pragma: no cover
                pass

    def ping(self) -> bool:
        conn = self._service_connection()
        try:
            conn.unbind()
        except Exception:  # pragma: no cover
            pass
        return True


def build_provider(settings: Settings) -> DirectoryProvider:
    if settings.directory_provider == "ldap":
        return LdapDirectoryProvider(settings)
    if not settings.static_directory_file:
        raise DirectoryError("Не задан FPV_STATIC_DIRECTORY_FILE для эмулятора каталога")
    return StaticDirectoryProvider(settings.static_directory_file)
