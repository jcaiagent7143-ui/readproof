"""Append-only JSONL trajectory (DESIGN.md §3): one event per line, never rewritten.

Why: the log is the evidence that the rules held — every gate decision, request (with the headers we sent),
wall verdict, extraction and budget change. A line is flushed as soon as it is written so a crash loses at
most the line being written; close() fsyncs. Raw bytes never enter the log: they are replaced by their
SHA-256, and the bytes themselves live in the evidence store under that hash.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import itertools
import json
import math
import os
import secrets
import threading
from datetime import datetime, timezone
from typing import Any, Iterator

_RESERVED = ("id", "ts", "run_id", "event")
_MAX_DEPTH = 32


def new_run_id() -> str:
    """Sortable and unique: 20261121T080304Z-3fa9c1."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)


def _clean(value: Any, depth: int = 0) -> Any:
    """Make a value strict-JSON safe: bytes -> "sha256:<hex>", dataclasses -> dicts, enums -> values,
    sets -> sorted lists, non-finite floats -> strings, anything else unknown -> str()."""
    if depth > _MAX_DEPTH:
        return str(value)
    if value is None or isinstance(value, (bool, int, str)):
        return value.value if isinstance(value, enum.Enum) else value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "sha256:" + hashlib.sha256(bytes(value)).hexdigest()
    if isinstance(value, enum.Enum):
        return _clean(value.value, depth + 1)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _clean(getattr(value, f.name), depth + 1) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): _clean(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v, depth + 1) for v in value]
    if isinstance(value, (set, frozenset)):
        items = [_clean(v, depth + 1) for v in value]
        try:
            return sorted(items)
        except TypeError:
            return sorted(items, key=str)
    return str(value)


def _complete_lines(path: str) -> tuple[int, bool]:
    """(number of newline-terminated lines, whether the file ends mid-line)."""
    if not os.path.exists(path):
        return 0, False
    n = 0
    last = b""
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            n += chunk.count(b"\n")
            last = chunk[-1:]
    return n, last not in (b"", b"\n")


class JsonlLog:
    """Implements interfaces.Log. One writer per file (ids continue from the lines already there)."""

    def __init__(self, path: str, run_id: str | None = None) -> None:
        self.path = path
        self.run_id = run_id or new_run_id()
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        lines, partial = _complete_lines(path)
        self._counter = itertools.count(lines + 1)
        self._lock = threading.Lock()
        self._fh = open(path, "a", encoding="utf-8")
        if partial:
            # A crash cut the last line. Never rewrite it: end it, and say so in the log, so read_log can
            # skip exactly that line and nothing else.
            self._fh.write("\n")
            self._fh.flush()
            next(self._counter)  # the cut line is a line
            self.emit("log_resumed", cut_line=lines + 1)

    def emit(self, event: str, **fields: Any) -> str:
        """Append one event and return its id ("e_000001"). Reserved keys in fields get a trailing "_"."""
        with self._lock:
            if self._fh.closed:
                self._fh = open(self.path, "a", encoding="utf-8")
            eid = f"e_{next(self._counter):06d}"
            record: dict[str, Any] = {
                "id": eid,
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "run_id": self.run_id,
                "event": str(event),
            }
            for k, v in fields.items():
                record[f"{k}_" if k in _RESERVED else k] = _clean(v)
            self._fh.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            self._fh.flush()
            return eid

    def close(self) -> None:
        with self._lock:
            if not self._fh.closed:
                self._fh.flush()
                os.fsync(self._fh.fileno())
                self._fh.close()

    def __enter__(self) -> "JsonlLog":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def read_log(path: str) -> Iterator[dict]:
    """Replay a log in order. A line cut by a crash is skipped when it is the last line of the file or the
    "log_resumed" event right after it names it; any other corrupt line raises ValueError."""
    bad: int | None = None  # line number of an undecodable line not yet explained
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if bad is not None:
                    raise ValueError(f"{path}:{bad}: corrupt log line") from None
                bad = n
                continue
            if bad is not None:
                if not (event.get("event") == "log_resumed" and event.get("cut_line") == bad):
                    raise ValueError(f"{path}:{bad}: corrupt log line")
                bad = None
            yield event
    # A corrupt line at the very end is a write the crash interrupted; nothing follows it to explain.


def open_run(root: str = "runs", run_id: str | None = None) -> JsonlLog:
    """A new run directory <root>/<run_id>/ with its trajectory.jsonl."""
    rid = run_id or new_run_id()
    return JsonlLog(os.path.join(root, rid, "trajectory.jsonl"), run_id=rid)
