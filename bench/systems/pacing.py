"""Politeness, enforced in code for every system the benchmark runs from one process.

Why one scheduler for all systems: the study runs from the owner's home connection, so what a platform sees is the
sum of every system's requests. The rule is at least GAP_S seconds between any two requests this process sends to
one registrable domain, whichever system sends them:

- plain HTTP (naive_fetch, reader_service, and the harness's HttpNet) goes through PacedTransport, which sees every
  request on the wire: each redirect hop, each retry and each robots.txt fetch;
- a browser top-level load (browser_text, and the harness's browser and embed readers, which ask the gate with
  purpose "browser" before they navigate) is paced once per navigation, with a hold that covers the time Chromium
  takes to start before the request actually leaves. Sub-resources a page loads are the page's own and not paced.

The scheduler reserves slots instead of sleeping under its lock, so concurrent workers never block each other on
different domains. It also counts requests per platform (the per-run cap) and per read (bound with ``bind``).
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from contextlib import contextmanager
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

import httpx

GAP_S = 5.0
BROWSER_HOLD_S = 3.0
JINA_GAP_S = 4.0  # 15 requests per minute
# Second-level public suffixes that occur in our data or next to it; enough for a registrable-domain split
# without shipping the Public Suffix List.
_MULTI_LABEL_SUFFIXES = frozenset({
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn", "com.hk", "com.tw", "com.sg", "com.my", "com.au",
    "co.uk", "co.jp", "co.kr", "co.nz", "com.br", "com.mx",
})


def registrable_domain(host_or_url: str) -> str:
    """eTLD+1 for the hosts this study touches (``m.weibo.cn`` -> ``weibo.cn``, ``a.b.com.cn`` -> ``b.com.cn``)."""
    host = host_or_url or ""
    if "://" in host:
        try:
            host = urlsplit(host).hostname or ""
        except ValueError:
            host = ""
    host = host.strip().strip("[]").rstrip(".").lower()
    if not host or ":" in host or host.replace(".", "").isdigit():
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if ".".join(labels[-2:]) in _MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


class DomainScheduler:
    """Slots per registrable domain, request counts per platform and per read. Thread-safe."""

    def __init__(self, gap_s: float = GAP_S, *, gaps: dict[str, float] | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self.gap_s = float(gap_s)
        self.gaps = dict(gaps or {})
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next: dict[str, float] = {}
        self.platform_requests: Counter[str] = Counter()
        self.domain_requests: Counter[str] = Counter()
        self.waited_s = 0.0
        self._local = threading.local()

    # -- binding: which platform and which read the current thread is working for ---------------------------

    @contextmanager
    def bind(self, platform: str | None) -> Iterator[dict[str, Any]]:
        """Attribute the requests this thread sends inside the block to ``platform``; yields the read's tally."""
        prev = getattr(self._local, "frame", None)
        frame = {"platform": platform, "requests": 0, "domains": Counter()}
        self._local.frame = frame
        try:
            yield frame
        finally:
            self._local.frame = prev

    def _frame(self) -> dict[str, Any] | None:
        return getattr(self._local, "frame", None)

    # -- slots ---------------------------------------------------------------------------------------------

    def reserve(self, host_or_url: str, *, hold_s: float = 0.0, count: bool = True) -> float:
        """Wait for this domain's next slot (>= gap after the previous request's slot + hold); return the wait."""
        slot, now = self.assign(host_or_url, hold_s=hold_s, count=count)
        wait = max(0.0, slot - now)
        if wait > 0:
            self._sleep(wait)
            with self._lock:
                self.waited_s += wait
        return wait

    def assign(self, host_or_url: str, *, hold_s: float = 0.0, count: bool = True) -> tuple[float, float]:
        """Reserve the domain's next slot under the lock; returns (slot, now). Slots of one domain are always at
        least gap (+ the previous hold) apart, whichever threads take them."""
        domain = registrable_domain(host_or_url)
        gap = self.gaps.get(domain, self.gap_s)
        with self._lock:
            now = self._clock()
            slot = max(now, self._next.get(domain, float("-inf")))
            self._next[domain] = slot + gap + max(0.0, hold_s)
            if count:
                self._count_locked(domain, 1)
        return slot, now

    def count(self, host_or_url: str, n: int = 1) -> None:
        """Count requests that were not paced one by one (redirect hops inside one browser navigation)."""
        if n <= 0:
            return
        with self._lock:
            self._count_locked(registrable_domain(host_or_url), n)

    def _count_locked(self, domain: str, n: int) -> None:
        frame = self._frame()
        platform = frame.get("platform") if frame else None
        self.domain_requests[domain] += n
        if platform:
            self.platform_requests[platform] += n
        if frame is not None:
            frame["requests"] += n
            frame["domains"][domain] += n

    def touch(self, domains: Any, *, gap_s: float | None = None) -> None:
        """After a read ends: nothing else goes to these domains for another gap (browser timing is not exact)."""
        with self._lock:
            now = self._clock()
            for d in domains:
                d = registrable_domain(d)
                g = self.gaps.get(d, self.gap_s) if gap_s is None else gap_s
                self._next[d] = max(self._next.get(d, float("-inf")), now + g)

    def requests_for(self, platform: str) -> int:
        with self._lock:
            return self.platform_requests.get(platform, 0)


class PacedTransport(httpx.BaseTransport):
    """An httpx transport that takes a scheduler slot before every request it sends (hops and retries included)."""

    def __init__(self, inner: httpx.BaseTransport, scheduler: DomainScheduler, *, count: bool = True) -> None:
        self.inner = inner
        self.scheduler = scheduler
        self.count_requests = count

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.scheduler.reserve(request.url.host or "", count=self.count_requests)
        return self.inner.handle_request(request)

    def close(self) -> None:
        self.inner.close()


class PacedGate:
    """Wraps a readproof PolicyGate: an allowed browser navigation (purpose "browser" / "embed_media") takes a slot.

    HTTP purposes are not paced here: HttpNet asks the gate before each hop and the request itself is paced by
    PacedTransport, so pacing both would double every wait. Everything else is the wrapped gate's, unchanged."""

    PACED = frozenset({"browser", "embed_media"})

    def __init__(self, gate: Any, scheduler: DomainScheduler, *, hold_s: float = BROWSER_HOLD_S) -> None:
        self._gate = gate
        self.scheduler = scheduler
        self.hold_s = hold_s
        self.profile = gate.profile
        self.paced = 0

    def check(self, url: str, *, purpose: str = "read") -> Any:
        decision = self._gate.check(url, purpose=purpose)
        if decision.allowed and purpose in self.PACED:
            self.paced += 1
            self.scheduler.reserve(url, hold_s=self.hold_s)
        return decision

    def __getattr__(self, name: str) -> Any:  # resolve, deny, robots, token, log ...
        return getattr(self._gate, name)


class Breaker:
    """Pauses a (platform, system) pair after ``threshold`` consecutive refusals of the kinds in TRIP."""

    TRIP = frozenset({"challenge", "rate_limited", "login"})

    def __init__(self, threshold: int = 6) -> None:
        self.threshold = threshold
        self.consecutive: Counter[tuple[str, str]] = Counter()
        self.opened: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def record(self, platform: str, system: str, outcome: str | None) -> bool:
        """outcome: a wall type value, or None when the read was served. Returns True if the pair just opened."""
        key = (platform, system)
        with self._lock:
            if key in self.opened:
                return False
            if outcome in self.TRIP:
                self.consecutive[key] += 1
                if self.consecutive[key] >= self.threshold:
                    self.opened[key] = outcome or ""
                    return True
            else:
                self.consecutive[key] = 0
            return False

    def is_open(self, platform: str, system: str) -> bool:
        with self._lock:
            return (platform, system) in self.opened


class PlatformWatch:
    """Stops a platform that answers every system with a challenge (the owner's connection must not be pushed)."""

    def __init__(self, window: int = 12, min_systems: int = 2) -> None:
        self.window = window
        self.min_systems = min_systems
        self.recent: dict[str, list[tuple[str, str | None]]] = {}
        self.stopped: dict[str, str] = {}
        self._lock = threading.Lock()

    def record(self, platform: str, system: str, outcome: str | None, sent_requests: int) -> bool:
        """Only reads that sent requests count. Returns True if the platform was just stopped."""
        if sent_requests <= 0:
            return False
        with self._lock:
            if platform in self.stopped:
                return False
            seq = self.recent.setdefault(platform, [])
            seq.append((system, outcome))
            del seq[:-self.window]
            if len(seq) == self.window and all(o in ("challenge", "rate_limited") for _, o in seq) \
                    and len({s for s, _ in seq}) >= self.min_systems:
                self.stopped[platform] = (f"last {self.window} reads across {len({s for s, _ in seq})} systems were all "
                                          "challenge/rate_limited")
                return True
            return False

    def is_stopped(self, platform: str) -> bool:
        with self._lock:
            return platform in self.stopped
