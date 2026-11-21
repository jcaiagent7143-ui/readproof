"""Content-addressed evidence store (DESIGN.md §3): every fetched body under its SHA-256, Records in JSONL.

Why: a Record must name the exact bytes it was extracted from, and a quote must be checkable against them
later. Blobs are written once (deduplicated, atomically via rename) and verified against their hash on
read, so a changed byte is an error, not silent evidence drift. records.jsonl is append-only; a Record is
identified by its content-derived record_id, so a re-read that extracts the same text adds nothing.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Any, Iterable

from .types import Channel, Record, sha256_bytes

_SHA = re.compile(r"[0-9a-f]{64}")
_FIELDS = ("record_id", "post_key", "channel", "locator", "text", "source_sha256", "extractor", "fetched_at")


class CorruptBlob(Exception):
    """A stored blob no longer hashes to its name."""


def record_from_json(d: dict[str, Any]) -> Record:
    """Inverse of Record.to_json(). Unknown keys are ignored so older readers can load newer files."""
    return Record(**{k: (Channel(d[k]) if k == "channel" else d[k]) for k in _FIELDS})


def _write_atomic(path: str, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class FileStore:
    """Implements interfaces.Store on a directory:

    <root>/blobs/<sha[:2]>/<sha>        the bytes
    <root>/blobs/<sha[:2]>/<sha>.json   {"sha256", "bytes", "content_type", "source_url", "first_seen"}
    <root>/records.jsonl                one Record.to_json() per line
    """

    def __init__(self, root: str) -> None:
        self.root = root
        self.records_path = os.path.join(root, "records.jsonl")
        os.makedirs(os.path.join(root, "blobs"), exist_ok=True)
        self._by_post: dict[str, list[Record]] | None = None  # loaded on first use
        self._ids: set[str] = set()

    # -- blobs ---------------------------------------------------------------------------------------------

    def blob_path(self, sha256: str) -> str:
        if not _SHA.fullmatch(sha256 or ""):
            raise ValueError(f"not a sha256 hex digest: {sha256!r}")
        return os.path.join(self.root, "blobs", sha256[:2], sha256)

    def put(self, data: bytes, *, content_type: str = "", source_url: str = "") -> str:
        """Store bytes once; return their SHA-256. The sidecar keeps what was known when first seen."""
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("FileStore.put takes bytes")
        data = bytes(data)
        sha = sha256_bytes(data)
        path = self.blob_path(sha)
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _write_atomic(path, data)
        meta = path + ".json"
        if not os.path.exists(meta):
            first_seen = datetime.now(timezone.utc).isoformat(timespec="seconds")
            doc = {"sha256": sha, "bytes": len(data), "content_type": content_type, "source_url": source_url,
                   "first_seen": first_seen}
            _write_atomic(meta, json.dumps(doc, ensure_ascii=False).encode("utf-8"))
        return sha

    def get(self, sha256: str) -> bytes | None:
        """The bytes for a hash, or None if absent (or not a hash). Raises CorruptBlob if they changed."""
        try:
            path = self.blob_path(sha256)
        except ValueError:
            return None
        if not os.path.exists(path):
            return None
        with open(path, "rb") as fh:
            data = fh.read()
        if sha256_bytes(data) != sha256:
            raise CorruptBlob(f"{path} no longer matches its hash")
        return data

    def meta(self, sha256: str) -> dict[str, Any] | None:
        try:
            path = self.blob_path(sha256) + ".json"
        except ValueError:
            return None
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    # -- records -------------------------------------------------------------------------------------------

    def _load(self) -> dict[str, list[Record]]:
        if self._by_post is not None:
            return self._by_post
        by_post: dict[str, list[Record]] = {}
        if os.path.exists(self.records_path):
            with open(self.records_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        rec = record_from_json(json.loads(line))
                    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                        if line.endswith("\n"):
                            raise ValueError(f"{self.records_path}: corrupt record line") from None
                        break  # a crash cut the last line
                    if rec.record_id not in self._ids:
                        self._ids.add(rec.record_id)
                        by_post.setdefault(rec.post_key, []).append(rec)
        self._by_post = by_post
        return by_post

    def add_records(self, records: Iterable[Record]) -> None:
        """Append Records not already stored (by record_id)."""
        by_post = self._load()
        new = []
        for r in records:
            if r.record_id in self._ids:
                continue
            self._ids.add(r.record_id)
            by_post.setdefault(r.post_key, []).append(r)
            new.append(r)
        if not new:
            return
        with open(self.records_path, "a", encoding="utf-8") as fh:
            for r in new:
                fh.write(json.dumps(r.to_json(), ensure_ascii=False) + "\n")
            fh.flush()

    def records_for(self, post_key: str) -> list[Record]:
        return list(self._load().get(post_key, []))
