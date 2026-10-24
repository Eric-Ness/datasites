"""Object storage: R2 (S3 API) in CI, a local folder with the same layout for development and tests."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Protocol

from .contracts import repo_root


class PreconditionFailed(Exception):
    """A conditional write lost a race: someone else changed the object first."""


class Store(Protocol):
    def get(self, key: str) -> tuple[bytes, str] | None:
        """Returns (body, etag), or None if the key does not exist."""
        ...

    def exists(self, key: str) -> bool: ...

    def put(
        self, key: str, body: bytes, content_type: str, *, if_match: str | None = None, if_none_match: bool = False
    ) -> str:
        """Writes the object and returns its new etag. Raises PreconditionFailed if a condition fails."""
        ...

    def list(self, prefix: str) -> list[str]: ...


def get_json(store: Store, key: str) -> tuple[Any, str | None]:
    found = store.get(key)
    if found is None:
        return None, None
    body, etag = found
    return json.loads(body), etag


def dumps(value: Any) -> bytes:
    """Deterministic JSON: same value, same bytes."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode()


def put_json(store: Store, key: str, value: Any, **conditions: Any) -> str:
    return store.put(key, dumps(value), "application/json", **conditions)


class LocalStore:
    """A folder that mirrors the bucket. Etags are MD5 hex, like R2's for single-part uploads."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.writes = 0
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        if key.startswith("/") or ".." in key.split("/"):
            raise ValueError(f"bad key {key!r}")
        return self.root / key

    def get(self, key: str) -> tuple[bytes, str] | None:
        path = self._path(key)
        if not path.is_file():
            return None
        body = path.read_bytes()
        return body, hashlib.md5(body).hexdigest()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def put(self, key, body, content_type, *, if_match=None, if_none_match=False) -> str:
        current = self.get(key)
        if if_none_match and current is not None:
            raise PreconditionFailed(f"{key} already exists")
        if if_match is not None and (current is None or current[1] != if_match):
            raise PreconditionFailed(f"{key} changed since it was read")
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
        tmp.write_bytes(body)
        os.replace(tmp, path)
        with self._lock:
            self.writes += 1
        return hashlib.md5(body).hexdigest()

    def list(self, prefix: str) -> list[str]:
        base = self.root
        if not base.is_dir():
            return []
        keys = (p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file() and not p.name.endswith(".tmp"))
        return sorted(k for k in keys if k.startswith(prefix))


class R2Store:
    """Cloudflare R2 through its S3-compatible API. Conditional PutObject makes manifest flips atomic."""

    def __init__(self, endpoint: str, bucket: str, access_key_id: str, secret_access_key: str):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.writes = 0
        self._lock = threading.Lock()
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            # Only send checksums when an operation requires them; R2 rejects some of boto3's defaults.
            config=Config(request_checksum_calculation="when_required", response_checksum_validation="when_required"),
        )

    def get(self, key: str) -> tuple[bytes, str] | None:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.NoSuchKey:
            return None
        return obj["Body"].read(), obj["ETag"].strip('"')

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def put(self, key, body, content_type, *, if_match=None, if_none_match=False) -> str:
        from botocore.exceptions import ClientError

        args: dict[str, Any] = {"Bucket": self.bucket, "Key": key, "Body": body, "ContentType": content_type}
        if if_match is not None:
            args["IfMatch"] = f'"{if_match}"'
        if if_none_match:
            args["IfNoneMatch"] = "*"
        try:
            result = self.client.put_object(**args)
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") in ("PreconditionFailed", "412"):
                raise PreconditionFailed(f"{key}: {err}") from err
            raise
        with self._lock:
            self.writes += 1
        return result["ETag"].strip('"')

    def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return keys


TARGETS = ("local", "staging", "production")


def open_store(target: str) -> Store:
    """local -> <repo>/.data/r2; staging/production -> R2 using R2_* environment variables."""
    if target == "local":
        return LocalStore(repo_root() / ".data" / "r2")
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r}; expected one of {TARGETS}")
    missing = [v for v in ("R2_ENDPOINT", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY") if not os.environ.get(v)]
    if missing:
        raise RuntimeError(f"target {target!r} needs these environment variables: {', '.join(missing)}")
    return R2Store(
        os.environ["R2_ENDPOINT"],
        os.environ["R2_BUCKET"],
        os.environ["R2_ACCESS_KEY_ID"],
        os.environ["R2_SECRET_ACCESS_KEY"],
    )
