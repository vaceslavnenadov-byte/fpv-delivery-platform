"""Генерация и хеширование секретных токенов (сессии, CSRF, ключи дронов).

В базе хранится только SHA-256 от токена: утечка БД не даёт рабочих токенов.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def tokens_equal(token: str, expected_hash: bytes) -> bool:
    return hmac.compare_digest(hash_token(token), expected_hash)


def new_drone_key() -> str:
    # Префикс помогает опознать ключ в случае утечки (например, при сканировании репозиториев).
    return "fpvd_" + secrets.token_urlsafe(32)
