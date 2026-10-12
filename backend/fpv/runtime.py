"""Общие компоненты процесса (настройки, провайдер каталога, хранилище).

Инициализируются фабрикой приложения или воркером; в тестах подменяются.
"""
from __future__ import annotations

from typing import Optional

from .config import Settings
from .services.directory import DirectoryProvider
from .services.storage import ObjectStore


class Runtime:
    settings: Optional[Settings] = None
    directory: Optional[DirectoryProvider] = None
    store: Optional[ObjectStore] = None


runtime = Runtime()


def settings() -> Settings:
    if runtime.settings is None:
        raise RuntimeError("Настройки не инициализированы")
    return runtime.settings


def store() -> ObjectStore:
    if runtime.store is None:
        raise RuntimeError("Хранилище не инициализировано")
    return runtime.store


def directory() -> DirectoryProvider:
    if runtime.directory is None:
        raise RuntimeError("Провайдер каталога не инициализирован")
    return runtime.directory
