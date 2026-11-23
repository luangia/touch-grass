"""Reproducible downloads: streaming, checksums, manifests, and caching.

Every stored file ``<name>`` has a sibling ``<name>.manifest.json`` recording where it
came from. A file is reused (not re-downloaded) only when its manifest exists and its
SHA-256 still matches, so a corrupted or hand-edited file is never trusted silently.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

USER_AGENT = "touchgrass-pipeline/0.1 (open-source park access research)"
_CHUNK = 1 << 20  # 1 MiB


@dataclass(frozen=True)
class Manifest:
    """Provenance for one stored raw file."""

    source_id: str
    url: str
    retrieved_at: str
    stored_file: str
    stored_bytes: int
    stored_sha256: str
    attribution: str
    params: dict[str, Any] = field(default_factory=dict)
    source_bytes: int | None = None  # bytes received (differs from stored when filtered)
    source_sha256: str | None = None
    filter: str | None = None  # human-readable description of any line filter applied
    etag: str | None = None
    last_modified: str | None = None
    vintage: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n", "utf-8")

    @classmethod
    def read(cls, path: Path) -> Manifest:
        return cls(**json.loads(path.read_text("utf-8")))


def manifest_path(stored: Path) -> Path:
    return stored.with_name(stored.name + ".manifest.json")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def cached_manifest(stored: Path) -> Manifest | None:
    """Return the manifest if ``stored`` exists and still matches it, else None."""
    mpath = manifest_path(stored)
    if not (stored.is_file() and mpath.is_file()):
        return None
    manifest = Manifest.read(mpath)
    if sha256_file(stored) != manifest.stored_sha256:
        return None
    return manifest


def now_utc() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def make_client(timeout_s: float = 120.0) -> httpx.Client:
    """HTTP client with redirects, a descriptive User-Agent, and connect retries."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=httpx.Timeout(timeout_s, connect=30.0),
        transport=httpx.HTTPTransport(retries=3),
    )


def _atomic_writer(dest: Path) -> tuple[Path, Callable[[], None]]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")

    def commit() -> None:
        os.replace(tmp, dest)  # atomic on the same filesystem

    return tmp, commit


def download(
    client: httpx.Client,
    url: str,
    dest: Path,
    *,
    source_id: str,
    attribution: str,
    vintage: str | None = None,
    params: dict[str, Any] | None = None,
    line_filter: Callable[[bytes], bool] | None = None,
    filter_description: str | None = None,
    refresh: bool = False,
) -> tuple[Manifest, bool]:
    """Stream ``url`` to ``dest``. Returns (manifest, downloaded?).

    With ``line_filter``, the response is treated as text lines: the first line (header) is
    always kept, later lines only if ``line_filter(line)`` is true. The checksum of the
    *full* response is still recorded as ``source_sha256``, so provenance stays verifiable.
    """
    if not refresh and (cached := cached_manifest(dest)) is not None:
        return cached, False

    tmp, commit = _atomic_writer(dest)
    source_hash = hashlib.sha256()
    stored_hash = hashlib.sha256()
    source_bytes = 0
    stored_bytes = 0

    with client.stream("GET", url, params=params) as response:
        response.raise_for_status()
        with tmp.open("wb") as out:

            def emit(data: bytes) -> None:
                nonlocal stored_bytes
                out.write(data)
                stored_hash.update(data)
                stored_bytes += len(data)

            if line_filter is None:
                for chunk in response.iter_bytes(_CHUNK):
                    source_hash.update(chunk)
                    source_bytes += len(chunk)
                    emit(chunk)
            else:
                for i, line in enumerate(_iter_lines(response.iter_bytes(_CHUNK), source_hash)):
                    source_bytes += len(line)
                    if i == 0 or line_filter(line):
                        emit(line)

        manifest = Manifest(
            source_id=source_id,
            url=str(response.url),
            params=params or {},
            retrieved_at=now_utc(),
            stored_file=dest.name,
            stored_bytes=stored_bytes,
            stored_sha256=stored_hash.hexdigest(),
            source_bytes=source_bytes,
            source_sha256=source_hash.hexdigest(),
            filter=filter_description,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            attribution=attribution,
            vintage=vintage,
        )
    commit()
    manifest.write(manifest_path(dest))
    return manifest, True


def _iter_lines(chunks: Iterator[bytes], hasher: Any) -> Iterator[bytes]:
    """Split a byte stream into lines (keeping b'\\n'), hashing the raw bytes as they pass."""
    buffer = b""
    for chunk in chunks:
        hasher.update(chunk)
        buffer += chunk
        *lines, buffer = buffer.split(b"\n")
        for line in lines:
            yield line + b"\n"
    if buffer:
        yield buffer


def write_json_with_manifest(payload: Any, dest: Path, manifest_fields: dict[str, Any]) -> Manifest:
    """Store a JSON document we assembled ourselves (e.g. paged API results) with provenance."""
    tmp, commit = _atomic_writer(dest)
    data = (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
    tmp.write_bytes(data)
    commit()
    digest = hashlib.sha256(data).hexdigest()
    manifest = Manifest(
        stored_file=dest.name,
        stored_bytes=len(data),
        stored_sha256=digest,
        retrieved_at=now_utc(),
        **manifest_fields,
    )
    manifest.write(manifest_path(dest))
    return manifest
