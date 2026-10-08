"""Backup storage factory and a dedicated private Supabase Storage provider.

The default provider is ``local``. The Supabase provider targets a DEDICATED
PRIVATE bucket and is fail-closed: it refuses a missing configuration, a public
or shared bucket, and a truthy ``SAAS_BACKUP_SUPABASE_PUBLIC`` flag. The service
role key is only ever sent in request headers; it never appears in a URL, in an
exception message, or in an operator summary.
"""

from __future__ import annotations

import io
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import BinaryIO, Callable, Iterable, Iterator
from urllib.parse import urlsplit

from .backup_storage import BackupStorage, LocalPrivateBackupStorage

_KEY_PATTERN = re.compile(r"[a-f0-9]{32}\.dump")
_RESERVED_BUCKETS = frozenset({"product-images", "product_images", "public"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})

RequestBody = bytes | Iterable[bytes] | None
ResponseBody = bytes | BinaryIO
Transport = Callable[[str, str, dict, RequestBody], tuple[int, ResponseBody]]


def _default_transport(timeout_seconds: int) -> Transport:
    def transport(
        method: str, url: str, headers: dict, body: RequestBody,
    ) -> tuple[int, ResponseBody]:
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=timeout_seconds)
            if method == "GET" and response.status == 200:
                # The caller owns and closes the stream. Avoid buffering a large
                # database archive in application memory.
                return response.status, response
            with response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
        except urllib.error.URLError as error:
            raise RuntimeError("Supabase backup storage transport failed") from error
    return transport


def _validate_key(key: str) -> str:
    if not isinstance(key, str) or not _KEY_PATTERN.fullmatch(key):
        raise ValueError("Invalid backup storage key")
    return key


def _file_chunks(source: BinaryIO, size: int = 1024 * 1024) -> Iterator[bytes]:
    while True:
        block = source.read(size)
        if not block:
            return
        yield block


class SupabasePrivateBackupStorage:
    """Private, non-public backup bucket; never proxies a public product bucket."""

    def __init__(self, *, local: LocalPrivateBackupStorage, endpoint: str | None,
                 bucket: str | None, service_key: str | None,
                 timeout_seconds: int = 120, transport: Transport | None = None):
        if not endpoint or not bucket or not service_key:
            raise ValueError("Supabase backup storage is not fully configured")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Supabase backup endpoint must be a secure base URL")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,62}", bucket.strip()):
            raise ValueError("Supabase backup bucket name is invalid")
        if bucket.strip().casefold() in _RESERVED_BUCKETS:
            raise ValueError("Supabase backup bucket must be dedicated and private")
        if (os.getenv("SAAS_BACKUP_SUPABASE_PUBLIC", "") or "").strip().casefold() in _TRUTHY:
            raise ValueError("Supabase backup bucket must be private")
        if timeout_seconds <= 0:
            raise ValueError("Supabase backup storage timeout must be positive")
        self._local = local
        self._endpoint = endpoint.rstrip("/")
        self._bucket = bucket
        self._service_key = service_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport or _default_transport(timeout_seconds)

    def _url(self, key: str) -> str:
        return f"{self._endpoint}/storage/v1/object/{self._bucket}/{key}"

    def _headers(self, *, content_length: int | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self._service_key}",
            "apikey": self._service_key,
            "Content-Type": "application/octet-stream",
            "x-upsert": "false",
        }
        if content_length is not None:
            headers["Content-Length"] = str(content_length)
        return headers

    def staging(self) -> Iterator[Path]:
        return self._local.staging()

    def verify_private_archive(self, path: Path) -> None:
        self._local.verify_private_archive(path)

    def put(self, key: str, source: Path) -> None:
        _validate_key(key)
        self._local.verify_private_archive(source)
        if self.exists(key):
            raise FileExistsError("Backup key already exists")
        with source.open("rb") as stream:
            status, _ = self._transport(
                "POST",
                self._url(key),
                self._headers(content_length=source.stat().st_size),
                _file_chunks(stream),
            )
        if status not in (200, 201):
            raise RuntimeError("Supabase backup upload was rejected")

    def get(self, key: str) -> BinaryIO:
        _validate_key(key)
        status, body = self._transport("GET", self._url(key), self._headers(), None)
        if status != 200:
            closer = getattr(body, "close", None)
            if callable(closer):
                closer()
            raise RuntimeError("Supabase backup download failed")
        return body if hasattr(body, "read") else io.BytesIO(body)

    def delete(self, key: str) -> None:
        _validate_key(key)
        status, _ = self._transport("DELETE", self._url(key), self._headers(), None)
        if status not in (200, 204, 404):
            raise RuntimeError("Supabase backup deletion failed")

    def exists(self, key: str) -> bool:
        _validate_key(key)
        status, _ = self._transport("HEAD", self._url(key), self._headers(), None)
        if status == 200:
            return True
        if status == 404:
            return False
        raise RuntimeError("Supabase backup storage lookup failed")


def build_backup_storage() -> BackupStorage:
    """Build the configured private backup provider; fail closed when unset."""
    root = os.getenv("SAAS_BACKUP_PRIVATE_DIR")
    if not root:
        raise RuntimeError("Private backup storage is not configured")
    local = LocalPrivateBackupStorage(Path(root))
    provider = (os.getenv("SAAS_BACKUP_STORAGE_PROVIDER", "local") or "local").strip().casefold()
    if provider == "local":
        if (os.getenv("RENDER", "") or "").strip().casefold() in _TRUTHY:
            raise RuntimeError(
                "Local backup storage is ephemeral on Render; configure private remote storage"
            )
        return local
    if provider == "supabase":
        return SupabasePrivateBackupStorage(
            local=local,
            endpoint=os.getenv("SAAS_BACKUP_SUPABASE_URL"),
            bucket=os.getenv("SAAS_BACKUP_SUPABASE_BUCKET"),
            service_key=os.getenv("SAAS_BACKUP_SUPABASE_SERVICE_ROLE_KEY"),
            timeout_seconds=int(os.getenv("SAAS_BACKUP_SUPABASE_TIMEOUT_SECONDS", "120")),
        )
    raise RuntimeError("Unsupported backup storage provider")
