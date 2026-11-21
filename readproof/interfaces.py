"""Interfaces between modules (DESIGN.md §3). Implementations live in the modules named in each docstring.

Builders code against these Protocols; integration is `readproof.harness.Harness`, which wires one of each.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from .types import (
    FetchResult,
    GateDecision,
    PostRef,
    Profile,
    Record,
    WallVerdict,
)


class Log(Protocol):
    """readproof/log.py — append-only JSONL trajectory. Every gate decision, request, wall verdict,
    extraction, verification, model call and budget change is one line. Never rewrites a line."""

    run_id: str

    def emit(self, event: str, **fields: Any) -> str:  # returns the event id
        ...


class Store(Protocol):
    """readproof/evidence.py — content-addressed snapshots (by SHA-256) and Records (JSONL)."""

    def put(self, data: bytes, *, content_type: str = "", source_url: str = "") -> str:  # sha256
        ...

    def get(self, sha256: str) -> bytes | None:
        ...

    def add_records(self, records: Iterable[Record]) -> None:
        ...

    def records_for(self, post_key: str) -> list[Record]:
        ...


class Gate(Protocol):
    """readproof/policy.py — decides every request before it is sent."""

    profile: Profile

    def check(self, url: str, *, purpose: str = "read") -> GateDecision:
        ...


class Net(Protocol):
    """readproof/net.py — the only code allowed to open a network connection (the browser reader excepted,
    which still asks the Gate first and logs every request it sees).

    Rules pinned by tests/test_rules.py: declared User-Agent on every request; no Cookie header ever;
    no proxy (environment proxies ignored); public addresses only; at most profile.max_retries retries,
    same identity, after profile.backoff_s, and only after 429 / 5xx / transport errors — never after
    403, 412 or a challenge."""

    origin: str

    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        follow_redirects: bool = False,
        max_bytes: int = 50_000_000,
        purpose: str = "read",
    ) -> FetchResult:  # body is set; the caller stores it
        ...


class Budget(Protocol):
    """readproof/budget.py — priced tools (USD and seconds). Refuses work past a cap."""

    def charge(self, tool: str, *, usd: float = 0.0, seconds: float = 0.0, **meta: Any) -> None:
        ...

    def remaining_usd(self) -> float:
        ...


class PlatformSpec(Protocol):
    """readproof/platforms/<name>.py — everything platform-specific that needs no network."""

    name: str

    def match(self, url: str) -> bool:
        ...

    def parse(self, url: str) -> PostRef | None:
        """Identify the post and the link form from a URL alone. None if this is not a post URL."""
        ...

    def canonical(self, ref: PostRef) -> str:
        """The preferred URL to read (e.g. toutiao /group/<id> -> /article/<id>/), built without fetching."""
        ...

    def forms(self, ref: PostRef, **known: str) -> list[tuple[str, str]]:
        """Link forms users really paste for this post: [(form_name, url_or_share_text), ...].
        `known` may carry values only the platform's share button gives (short link, token)."""
        ...

    def embed_url(self, ref: PostRef) -> str | None:
        """The platform's official embed player for this post, if one exists."""
        ...

    def oembed_url(self, url: str) -> str | None:
        """The platform's official oEmbed endpoint for this URL, if one exists."""
        ...

    def judge(self, fetch: FetchResult, visible_text: str) -> WallVerdict | None:
        """Platform-specific wall signatures. None = no opinion (walls.py then applies generic rules)."""
        ...


@dataclass
class Context:
    """What a reader gets. One per Harness.read() call."""

    profile: Profile
    gate: Gate
    net: Net
    log: Log
    store: Store
    budget: Budget
    workdir: str  # scratch directory for media and frames (deleted media after extraction unless keep_media)
    keep_media: bool = False
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReaderOutput:
    """What a reader returns to the harness."""

    reader: str
    verdict: WallVerdict
    records: list[Record] = field(default_factory=list)
    fetches: list[FetchResult] = field(default_factory=list)
    gate: list[GateDecision] = field(default_factory=list)
    media: list[str] = field(default_factory=list)  # local paths of media kept for extraction
    meta: dict[str, Any] = field(default_factory=dict)  # e.g. {"duration_s": 31.2, "image_urls": [...]}


class Reader(Protocol):
    """readproof/readers/<name>.py"""

    name: str

    def applies(self, ref: PostRef, ctx: Context) -> bool:
        ...

    def read(self, ref: PostRef, ctx: Context) -> ReaderOutput:
        ...
