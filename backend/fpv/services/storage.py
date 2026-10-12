"""Хранилище объектов для видеоподтверждений (размещается в ЦОД).

* ``FilesystemObjectStore`` — каталог на выделенном томе / сетевом ресурсе ЦОД.
  Объекты записываются атомарно и переводятся в режим «только чтение».
* ``S3ObjectStore`` — S3-совместимое хранилище в ЦОД (например, MinIO) с
  опциональной блокировкой объектов (Object Lock, режим COMPLIANCE) до конца
  срока хранения, что исключает подмену и удаление даже администратором
  хранилища.

Ключи объектов формирует только сервер (без пользовательского ввода).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import BinaryIO, Iterator, Optional

from ..config import Settings

_KEY_RE = re.compile(r"^[A-Za-z0-9/_.-]{1,512}$")


class StorageError(RuntimeError):
    pass


def _check_key(key: str) -> str:
    if not _KEY_RE.match(key) or ".." in key or key.startswith("/"):
        raise StorageError("Недопустимый ключ объекта")
    return key


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class ObjectStore:
    def put_file(self, key: str, path: Path, content_type: str,
                 retain_until: Optional[dt.datetime] = None) -> None:
        raise NotImplementedError

    def open(self, key: str) -> Iterator[bytes]:
        raise NotImplementedError

    def sha256(self, key: str) -> Optional[str]:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def ping(self) -> dict:
        raise NotImplementedError


class FilesystemObjectStore(ObjectStore):
    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / _check_key(key)

    def put_file(self, key, path, content_type, retain_until=None):
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        if target.exists():
            raise StorageError("Объект с таким ключом уже существует (перезапись запрещена)")
        tmp = target.with_name(target.name + ".part")
        shutil.copyfile(path, tmp)
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        os.chmod(tmp, 0o440)
        os.replace(tmp, target)

    def open(self, key):
        path = self._path(key)
        if not path.is_file():
            raise StorageError("Объект не найден")

        def gen():
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(256 * 1024), b""):
                    yield chunk
        return gen()

    def sha256(self, key):
        path = self._path(key)
        return sha256_file(path) if path.is_file() else None

    def exists(self, key):
        return self._path(key).is_file()

    def delete(self, key):
        path = self._path(key)
        if path.exists():
            os.chmod(path, 0o640)
            path.unlink()

    def ping(self):
        self.root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(self.root)
        return {"backend": "filesystem", "free_bytes": usage.free, "total_bytes": usage.total}


class S3ObjectStore(ObjectStore):  # pragma: no cover - требует S3-совместимого сервиса
    def __init__(self, settings: Settings) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3", endpoint_url=settings.s3_endpoint or None,
            aws_access_key_id=settings.s3_access_key or None,
            aws_secret_access_key=settings.s3_secret_key or None,
            verify=settings.s3_ca_file or True,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}))

    def put_file(self, key, path, content_type, retain_until=None):
        extra = {"ContentType": content_type, "ChecksumAlgorithm": "SHA256",
                 "Metadata": {"sha256": sha256_file(path)}}
        if retain_until is not None:
            extra.update({"ObjectLockMode": "COMPLIANCE", "ObjectLockRetainUntilDate": retain_until})
        if self.exists(key):
            raise StorageError("Объект с таким ключом уже существует (перезапись запрещена)")
        self.client.upload_file(str(path), self.bucket, _check_key(key), ExtraArgs=extra)

    def open(self, key):
        obj = self.client.get_object(Bucket=self.bucket, Key=_check_key(key))
        return obj["Body"].iter_chunks(256 * 1024)

    def sha256(self, key):
        h = hashlib.sha256()
        try:
            for chunk in self.open(key):
                h.update(chunk)
        except self.client.exceptions.NoSuchKey:
            return None
        return h.hexdigest()

    def exists(self, key):
        try:
            self.client.head_object(Bucket=self.bucket, Key=_check_key(key))
            return True
        except Exception:
            return False

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=_check_key(key))

    def ping(self):
        self.client.head_bucket(Bucket=self.bucket)
        return {"backend": "s3", "bucket": self.bucket}


def build_store(settings: Settings) -> ObjectStore:
    if settings.object_store == "s3":
        return S3ObjectStore(settings)
    return FilesystemObjectStore(settings.object_store_root)
