"""Test doubles implementing the Protocols in interfaces.py. Offline, deterministic, shared by every test file."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .interfaces import Context
from .types import FetchResult, GateDecision, Profile, Record, sha256_bytes


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemoryLog:
    def __init__(self, run_id: str = "test") -> None:
        self.run_id = run_id
        self.events: list[dict[str, Any]] = []
        self._n = itertools.count(1)

    def emit(self, event: str, **fields: Any) -> str:
        eid = f"e_{next(self._n):06d}"
        self.events.append({"id": eid, "event": event, **fields})
        return eid

    def of(self, event: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["event"] == event]


class MemoryStore:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.records: list[Record] = []

    def put(self, data: bytes, *, content_type: str = "", source_url: str = "") -> str:
        h = sha256_bytes(data)
        self.blobs[h] = data
        return h

    def get(self, sha256: str) -> bytes | None:
        return self.blobs.get(sha256)

    def add_records(self, records: Iterable[Record]) -> None:
        self.records.extend(records)

    def records_for(self, post_key: str) -> list[Record]:
        return [r for r in self.records if r.post_key == post_key]


class NullBudget:
    def __init__(self) -> None:
        self.charges: list[dict[str, Any]] = []

    def charge(self, tool: str, *, usd: float = 0.0, seconds: float = 0.0, **meta: Any) -> None:
        self.charges.append({"tool": tool, "usd": usd, "seconds": seconds, **meta})

    def remaining_usd(self) -> float:
        return float("inf")


class AllowGate:
    """Allows everything and records what it was asked. For tests of code that is not the gate."""

    def __init__(self, profile: Profile, deny: Callable[[str], str | None] | None = None) -> None:
        self.profile = profile
        self.asked: list[str] = []
        self._deny = deny

    def check(self, url: str, *, purpose: str = "read") -> GateDecision:
        self.asked.append(url)
        reason = self._deny(url) if self._deny else None
        if reason:
            return GateDecision(url=url, allowed=False, profile=self.profile.name, reason=reason)
        return GateDecision(url=url, allowed=True, profile=self.profile.name, reason="allowed")


@dataclass
class Scripted:
    """One scripted response for FakeNet."""

    status: int = 200
    body: bytes | str = b""
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None  # simulate a transport error


class FakeNet:
    """Answers from a dict {url: Scripted | [Scripted, ...]} (a list is consumed in order). Unknown URL -> 404.

    Records every request so tests can assert what was (and was not) fetched."""

    def __init__(self, routes: dict[str, Scripted | list[Scripted]], *, origin: str = "test:offline", user_agent: str = "ReadProof/test") -> None:
        self.routes = {k: (list(v) if isinstance(v, list) else [v]) for k, v in routes.items()}
        self.origin = origin
        self.user_agent = user_agent
        self.requests: list[tuple[str, str]] = []

    def fetch(self, url: str, *, method: str = "GET", headers: dict[str, str] | None = None,
              follow_redirects: bool = False, max_bytes: int = 50_000_000, purpose: str = "read") -> FetchResult:
        self.requests.append((method, url))
        queue = self.routes.get(url)
        s = (queue.pop(0) if len(queue) > 1 else queue[0]) if queue else Scripted(status=404, body=b"not found")
        body = s.body.encode("utf-8") if isinstance(s.body, str) else s.body
        body = body[:max_bytes]
        return FetchResult(
            url=url, final_url=url, status=None if s.error else s.status, headers=dict(s.headers),
            bytes=len(body), sha256=sha256_bytes(body), fetched_at=now_iso(), elapsed_ms=1,
            origin=self.origin, user_agent=self.user_agent, method=method, error=s.error, body=body,
        )


def make_context(profile: Profile, net: Any, gate: Any | None = None, workdir: str = "/tmp/readproof-test", **options: Any) -> Context:
    return Context(
        profile=profile, gate=gate or AllowGate(profile), net=net, log=MemoryLog(), store=MemoryStore(),
        budget=NullBudget(), workdir=workdir, options=options,
    )
