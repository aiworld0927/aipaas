"""解析产物对象存储：MinIO 为主，本地/内存实现用于开发与测试。

产物键包含版本（``parse/{tenant}/{documentId}/v{n}/...``），并写 ``latest.json`` 指针。
"""

from __future__ import annotations

import json
import os
import re
import threading
from io import BytesIO
from pathlib import Path
from typing import Protocol


class ArtifactStore(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/json") -> str: ...

    def get_bytes(self, key: str) -> bytes | None: ...

    def exists(self, key: str) -> bool: ...


class InMemoryArtifactStore:
    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self._lock = threading.RLock()

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/json") -> str:
        with self._lock:
            self._objects[key] = data
        return key

    def get_bytes(self, key: str) -> bytes | None:
        with self._lock:
            return self._objects.get(key)

    def exists(self, key: str) -> bool:
        with self._lock:
            return key in self._objects


class LocalArtifactStore:
    """本地目录实现，原子写；用于无 MinIO 的开发环境。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/json") -> str:
        target = self._path(key)
        with self._lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name + ".tmp")
            temp.write_bytes(data)
            os.replace(temp, target)
        return key

    def get_bytes(self, key: str) -> bytes | None:
        target = self._path(key)
        if not target.exists():
            return None
        return target.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def _path(self, key: str) -> Path:
        cleaned = re.sub(r"[^A-Za-z0-9._/\-]", "_", key).lstrip("/")
        target = (self.root / cleaned).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            raise ValueError(f"非法对象键：{key}")
        return target


class MinioArtifactStore:
    """MinIO 实现；客户端惰性创建，避免 import 阶段依赖网络。"""

    def __init__(
        self,
        bucket: str,
        *,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        secure: bool | None = None,
    ) -> None:
        self.bucket = bucket
        self.endpoint = endpoint or os.getenv("AIPAAS_MINIO_ENDPOINT", "localhost:9000")
        self.access_key = access_key or os.getenv("AIPAAS_MINIO_ACCESS_KEY", "minioadmin")
        self.secret_key = secret_key or os.getenv("AIPAAS_MINIO_SECRET_KEY", "minioadmin")
        self.secure = secure if secure is not None else os.getenv("AIPAAS_MINIO_SECURE", "false").lower() == "true"
        self._client = None
        self._lock = threading.RLock()

    def _ensure_client(self):
        with self._lock:
            if self._client is None:
                from minio import Minio

                client = Minio(self.endpoint, access_key=self.access_key, secret_key=self.secret_key,
                               secure=self.secure)
                if not client.bucket_exists(self.bucket):
                    client.make_bucket(self.bucket)
                self._client = client
        return self._client

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/json") -> str:
        client = self._ensure_client()
        client.put_object(self.bucket, key, BytesIO(data), length=len(data), content_type=content_type)
        return key

    def get_bytes(self, key: str) -> bytes | None:
        from minio.error import S3Error

        client = self._ensure_client()
        try:
            response = client.get_object(self.bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                return None
            raise

    def exists(self, key: str) -> bool:
        from minio.error import S3Error

        client = self._ensure_client()
        try:
            client.stat_object(self.bucket, key)
            return True
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                return False
            raise


def json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
