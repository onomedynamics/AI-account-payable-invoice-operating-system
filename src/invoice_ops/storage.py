"""Object storage abstraction.

Everything that touches a raw document, a rendered page image, or an export
artifact goes through :class:`Storage` -- never a filesystem path or a boto3
client directly. The local backend is for dev; the S3 backend targets MinIO
or real S3 and is byte-for-byte the same interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config as BotoConfig

from invoice_ops.config import Settings, get_settings

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client


class Storage(ABC):
    @abstractmethod
    def ensure_ready(self) -> None:
        """Create the bucket / directory if missing; raise if unreachable."""

    @abstractmethod
    def put_object(self, key: str, data: bytes, content_type: str) -> None: ...

    @abstractmethod
    def get_object(self, key: str) -> bytes: ...

    @abstractmethod
    def presigned_url(self, key: str, expires_in: int = 3600) -> str: ...


class LocalStorage(Storage):
    """Filesystem-backed store rooted at a single directory."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _path(self, key: str) -> Path:
        candidate = (self._root / key).resolve()
        if not candidate.is_relative_to(self._root):
            raise ValueError(f"key escapes storage root: {key!r}")
        return candidate

    def ensure_ready(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)

    def put_object(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get_object(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return self._path(key).as_uri()


class S3Storage(Storage):
    """MinIO / S3-backed store."""

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._client: S3Client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=BotoConfig(signature_version="s3v4"),
        )

    def ensure_ready(self) -> None:
        existing = {b["Name"] for b in self._client.list_buckets().get("Buckets", [])}
        if self._bucket not in existing:
            self._client.create_bucket(Bucket=self._bucket)

    def put_object(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    def get_object(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return response["Body"].read()

    def presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )


def get_storage(settings: Settings | None = None) -> Storage:
    settings = settings or get_settings()
    if settings.storage_backend == "s3":
        return S3Storage(settings)
    return LocalStorage(settings.storage_local_dir)
