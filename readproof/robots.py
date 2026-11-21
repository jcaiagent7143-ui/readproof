"""RFC 9309 robots.txt: parser, matcher, Content-Signal / Content-Usage records, and a per-origin cache.

Why it is written this way:
- Group selection, longest match and allow-on-tie follow RFC 9309 §2.2 exactly, because under P0 this file
  decides whether a request is sent at all. Where the RFC leaves room, we follow Google's open-source parser
  (github.com/google/robotstxt), the de facto reference that site owners test against.
- The matcher walks a set of positions instead of building a regex, so a hostile file full of `*` cannot make
  matching slow (O(len(pattern) * len(path))).
- Content-Signal (Cloudflare, contentsignals.org) and Content-Usage (IETF aipref) are "other records" under
  RFC 9309 §2.2.4: they are recorded per group and globally and never interfere with Allow/Disallow parsing.
- Fetch status semantics are RFC 9309 §2.3.1: 2xx parse; 4xx (except 429) "unavailable" = allow all;
  5xx, 429 or a network error "unreachable" = disallow all; up to five redirects for the file itself.
"""

from __future__ import annotations

import re
import string
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from .types import FetchResult

TOKEN = "readproof"  # our product token; groups are matched on it, case-insensitively
MAX_BYTES = 500 * 1024  # RFC 9309 §2.5: parse at least the first 500 KiB
MAX_REDIRECTS = 5  # RFC 9309 §2.3.1.2
UNREACHABLE_TTL_S = 600  # a transient 5xx must not disallow a host for a whole day
MAX_NESTED_LOADS = 4  # robots lookups that may be in progress at once (one is normal; more means a cycle)

OK, UNAVAILABLE, UNREACHABLE = "ok", "unavailable", "unreachable"
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")
_RESERVED = frozenset(":/?#[]@!$&'()*+,;=")
_HEX = frozenset("0123456789abcdefABCDEF")
_TOKEN_RE = re.compile(r"[A-Za-z_-]+")

# Keys, including the misspellings Google accepts (RFC 9309 §2.2.4 lets crawlers be lenient). Accepting a
# misspelled Disallow can only make P0 stricter.
_KEYS = {
    "user-agent": "user-agent", "useragent": "user-agent", "user agent": "user-agent", "user_agent": "user-agent",
    "allow": "allow",
    "disallow": "disallow", "dissallow": "disallow", "dissalow": "disallow", "disalow": "disallow",
    "diasllow": "disallow", "disallaw": "disallow",
    "sitemap": "sitemap", "site-map": "sitemap",
    "content-signal": "content-signal", "content-signals": "content-signal",
    "content-usage": "content-usage",
}


def product_token(user_agent: str) -> str:
    """The product token of a User-Agent value, lower-cased: "ReadProof/0.1.0 (+https://…)" -> "readproof".

    RFC 9309 §2.2.1: a token is letters, "_" and "-"; matching is case-insensitive."""
    m = _TOKEN_RE.match((user_agent or "").strip())
    return m.group(0).lower() if m else ""


def normalize(s: str, *, pattern: bool) -> str:
    """Bring a URL path or a rule pattern to the form RFC 9309 §2.2.2 compares.

    Percent-encoded unreserved characters are decoded (%62 -> b); other escapes are kept with upper-case hex;
    non-ASCII is UTF-8 percent-encoded. In a URL a literal "*" or "$" becomes %2A / %24, so the RFC's
    "file-with-a-%2A.html" pattern matches "file-with-a-*.html". In a pattern "*" is the wildcard and a final
    "$" the end anchor; a "$" anywhere else is literal."""
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "%" and i + 2 < n and s[i + 1] in _HEX and s[i + 2] in _HEX:
            octet = int(s[i + 1 : i + 3], 16)
            ch = chr(octet)
            out.append(ch if octet < 128 and ch in _UNRESERVED else "%" + s[i + 1 : i + 3].upper())
            i += 3
            continue
        if ord(c) >= 128:
            out.extend(f"%{b:02X}" for b in c.encode("utf-8", "surrogatepass"))
        elif c == "*":
            out.append("*" if pattern else "%2A")
        elif c == "$":
            out.append("$" if pattern and i == n - 1 else "%24")
        elif c == "%":
            out.append("%25")  # a bare "%" that starts no escape
        elif c in _UNRESERVED or c in _RESERVED:
            out.append(c)
        else:
            out.append(f"%{ord(c):02X}")  # space, quotes, control characters
        i += 1
    return "".join(out)


def matches(path: str, pattern: str) -> bool:
    """Does a normalized pattern match a normalized path? Prefix match; "*" = any run; final "$" = end.

    Port of Google robotstxt's RobotsMatchStrategy::Matches: `pos` holds every path offset that can follow
    the pattern prefix seen so far, so there is no backtracking."""
    pathlen = len(path)
    pos = [0]
    last = len(pattern) - 1
    for k, p in enumerate(pattern):
        if p == "$" and k == last:
            return pos[-1] == pathlen
        if p == "*":
            pos = list(range(pos[0], pathlen + 1))
        else:
            pos = [i + 1 for i in pos if i < pathlen and path[i] == p]
            if not pos:
                return False
    return True


@dataclass(frozen=True)
class Rule:
    allow: bool
    pattern: str  # as written in the file (trimmed)
    norm: str  # normalized for matching; "" for an empty rule, which matches nothing
    line: int

    @property
    def text(self) -> str:
        return f"{'Allow' if self.allow else 'Disallow'}: {self.pattern}"


@dataclass(frozen=True)
class Signal:
    """One Content-Signal or Content-Usage line: `[path] key=value, key=value`."""

    kind: str  # "content-signal" | "content-usage"
    values: dict[str, str]  # keys and values lower-cased, e.g. {"ai-input": "no"}
    path: str | None  # optional path scope, as written
    norm: str | None
    line: int
    raw: str


@dataclass
class Group:
    agents: list[str]  # product tokens, lower-cased; "*" for the global group; "" matches nothing
    rules: list[Rule] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    line: int = 0


def _agent(value: str) -> str:
    v = value.strip()
    if v.startswith("*") and (len(v) == 1 or v[1].isspace()):
        return "*"
    return product_token(v)


def _split_line(raw: str) -> tuple[str, str] | None:
    line = raw.split("#", 1)[0].strip()
    if not line:
        return None
    if ":" in line:
        key, value = line.split(":", 1)
    else:
        parts = line.split()  # "Disallow /x": Google accepts a missing colon when there are exactly two words
        if len(parts) != 2:
            return None
        key, value = parts
    return " ".join(key.strip().lower().split()), value.strip()


def _signal(kind: str, value: str, line: int) -> Signal | None:
    text = re.sub(r"\s*=\s*", "=", value.strip())
    path: str | None = None
    values: dict[str, str] = {}
    for tok in re.split(r"[,\s]+", text):
        if not tok:
            continue
        if "=" in tok:
            k, v = tok.split("=", 1)
            k = k.strip().lower()
            if k:
                values[k] = v.strip().strip('"').lower()
        elif tok.startswith("/") and path is None and not values:
            path = tok
    if not values:
        return None
    return Signal(kind, values, path, normalize(path, pattern=True) if path else None, line, value)


def _path_of(path_and_query: str) -> str:
    p = (path_and_query or "").strip()
    if p.startswith(("http://", "https://")):
        try:
            p = httpx.URL(p).raw_path.decode("ascii")
        except (httpx.InvalidURL, UnicodeDecodeError):
            pass
    p = p.split("#", 1)[0]
    return p if p.startswith("/") else "/" + p


class RobotsTxt:
    """A parsed robots.txt. Use `decide()` for Allow/Disallow and `signals_for()` for Content-Signal."""

    def __init__(self, groups: list[Group], global_signals: list[Signal], sitemaps: list[str]) -> None:
        self.groups = groups
        self.global_signals = global_signals
        self.sitemaps = sitemaps
        # What applies to an agent the file does not name (global lines plus the "*" group), unscoped lines only.
        self.content_signal: dict[str, str] = self.signals_for("*")
        self.content_usage: dict[str, str] = self.signals_for("*", kind="content-usage")

    # -- parsing ---------------------------------------------------------------------------------------------

    @classmethod
    def parse(cls, text: str) -> "RobotsTxt":
        """Parse robots.txt text. Never raises: an unparseable line is ignored (RFC 9309 §2.2.4)."""
        if text.startswith("﻿"):
            text = text[1:]
        groups: list[Group] = []
        global_signals: list[Signal] = []
        sitemaps: list[str] = []
        current: Group | None = None
        seen_rule = False
        for n, raw in enumerate(text.splitlines(), 1):
            kv = _split_line(raw)
            if kv is None:
                continue
            kind = _KEYS.get(kv[0])
            value = kv[1]
            if kind == "user-agent":
                # Consecutive user-agent lines share one group; a user-agent line after a rule starts a new one.
                if current is None or seen_rule:
                    current = Group(agents=[], line=n)
                    groups.append(current)
                    seen_rule = False
                current.agents.append(_agent(value))
            elif kind in ("allow", "disallow"):
                if current is None:
                    continue  # a rule before any user-agent line belongs to no group (RFC 9309 §2.1)
                seen_rule = True
                pat = value if not value or value.startswith(("/", "*")) else "/" + value
                current.rules.append(Rule(kind == "allow", value, normalize(pat, pattern=True) if pat else "", n))
            elif kind == "sitemap":
                if value:
                    sitemaps.append(value)
            elif kind in ("content-signal", "content-usage"):
                sig = _signal(kind, value, n)
                if sig is not None:
                    (current.signals if current is not None else global_signals).append(sig)
        return cls(groups, global_signals, sitemaps)

    @classmethod
    def parse_bytes(cls, data: bytes) -> "RobotsTxt":
        """Parse the first 500 KiB of a robots.txt body (RFC 9309 §2.5), decoded as UTF-8."""
        return cls.parse((data or b"")[:MAX_BYTES].decode("utf-8", errors="replace"))

    # -- decisions -------------------------------------------------------------------------------------------

    def groups_for(self, user_agent_token: str) -> tuple[str | None, list[Group]]:
        """The groups that apply to an agent and the name they matched under ("readproof", "*" or None).

        Every group naming the token is combined (RFC 9309 §2.2.1); only if none does, every "*" group."""
        token = product_token(user_agent_token)
        if token:
            named = [g for g in self.groups if token in g.agents]
            if named:
                return token, named
        star = [g for g in self.groups if "*" in g.agents]
        if star:
            return "*", star
        return None, []

    def decide(self, user_agent_token: str, path_and_query: str) -> tuple[bool, str | None, str | None]:
        """(allowed, group, rule) for one request. group is "readproof", "*" or None; rule is e.g. "Disallow: /".

        The longest matching pattern wins; Allow wins a tie; no matching rule (or no group) allows."""
        name, groups = self.groups_for(user_agent_token)
        if not groups:
            return True, None, None
        path = _path_of(path_and_query)
        if path.split("?", 1)[0] == "/robots.txt":
            return True, name, "implicit allow: /robots.txt"  # RFC 9309 §2.2.2
        target = normalize(path, pattern=False)
        best: tuple[tuple[int, bool], Rule] | None = None
        for g in groups:
            for r in g.rules:
                if r.norm and matches(target, r.norm):
                    key = (len(r.norm), r.allow)  # longer first; on equal length True (allow) beats False
                    if best is None or key > best[0]:
                        best = (key, r)
        if best is None:
            return True, name, None
        return best[1].allow, name, best[1].text

    def signals_for(self, user_agent_token: str = "*", path_and_query: str | None = None, *,
                    kind: str = "content-signal") -> dict[str, str]:
        """Merged Content-Signal (or Content-Usage) values for an agent: global lines first, then the agent's
        groups (the same groups `decide` uses). Path-scoped lines apply only where their path matches, and a
        longer path overrides a shorter one, like Allow/Disallow."""
        _, groups = self.groups_for(user_agent_token)
        entries = [s for s in self.global_signals if s.kind == kind]
        entries += [s for g in groups for s in g.signals if s.kind == kind]
        target = normalize(_path_of(path_and_query), pattern=False) if path_and_query is not None else None
        applicable: list[tuple[int, int, Signal]] = []
        for order, s in enumerate(entries):
            if s.norm is None:
                applicable.append((0, order, s))
            elif target is not None and matches(target, s.norm):
                applicable.append((len(s.norm), order, s))
        out: dict[str, str] = {}
        for _, _, s in sorted(applicable, key=lambda t: (t[0], t[1])):
            out.update(s.values)
        return out

    @property
    def global_content_signal(self) -> dict[str, str]:
        """Content-Signal lines that precede every group (unscoped)."""
        out: dict[str, str] = {}
        for s in self.global_signals:
            if s.kind == "content-signal" and s.norm is None:
                out.update(s.values)
        return out

    @property
    def group_content_signal(self) -> dict[str, dict[str, str]]:
        """Unscoped Content-Signal values recorded inside groups, per agent token."""
        out: dict[str, dict[str, str]] = {}
        for g in self.groups:
            vals: dict[str, str] = {}
            for s in g.signals:
                if s.kind == "content-signal" and s.norm is None:
                    vals.update(s.values)
            if vals:
                for a in g.agents:
                    out.setdefault(a, {}).update(vals)
        return out


def _origin(url: str) -> tuple[tuple[str, str, int], str]:
    u = httpx.URL(url)
    if u.scheme not in ("http", "https") or not u.raw_host:
        raise ValueError(f"robots.txt applies to http(s) URLs with a host, not {url!r}")
    host = u.raw_host.decode("ascii").lower()
    default = 443 if u.scheme == "https" else 80
    port = u.port or default
    hostpart = f"[{host}]" if ":" in host else host
    robots_url = f"{u.scheme}://{hostpart}{'' if port == default else f':{port}'}/robots.txt"
    return (u.scheme, host, port), robots_url


def _header(headers: dict[str, str], name: str) -> str | None:
    for k, v in headers.items():
        if k.lower() == name:
            return v
    return None


class RobotsCache:
    """robots.txt per scheme+host+port, cached for ttl_s (RFC 9309 §2.4: at most 24 h).

    `fetch` is called with the robots.txt URL only. Best wired not to follow redirects (HttpNet's default,
    as net.make_stack does): the cache then follows up to five, each through `fetch`, so every hop is gated
    and logged. Redirects a fetch follows by itself count toward the same five. An "unreachable" result is
    cached for at most `unreachable_ttl_s` so a transient 503 does not shut a host for a day."""

    def __init__(self, fetch: Callable[[str], FetchResult], ttl_s: int = 86400, *,
                 unreachable_ttl_s: int = UNREACHABLE_TTL_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.fetch = fetch
        self.ttl_s = ttl_s
        self.unreachable_ttl_s = unreachable_ttl_s
        self._clock = clock
        self._entries: dict[tuple[str, str, int], tuple[float, RobotsTxt | None, str]] = {}
        self._inflight: set[str] = set()  # robots.txt URLs (and their redirects) being fetched right now
        self._loading: set[tuple[str, str, int]] = set()

    @staticmethod
    def robots_url(url: str) -> str:
        """https://Host:443/a?b -> https://host/robots.txt"""
        return _origin(url)[1]

    def fetching(self, url: str) -> bool:
        """Is `url` a request this cache is making right now? Only those skip the gate's robots evaluation,
        so no caller can opt out of rule 7 by calling its request purpose="robots"."""
        return url in self._inflight

    def for_url(self, url: str) -> tuple[RobotsTxt | None, str]:
        """(parsed file or None, "ok" | "unavailable" | "unreachable") for the origin of `url`."""
        key, robots_url = _origin(url)
        now = self._clock()
        hit = self._entries.get(key)
        if hit is not None and hit[0] > now:
            return hit[1], hit[2]
        if key in self._loading or len(self._loading) >= MAX_NESTED_LOADS:
            return None, UNREACHABLE  # a robots.txt whose fetch needs itself (a redirect cycle across hosts)
        self._loading.add(key)
        try:
            robots, status = self._load(robots_url)
        finally:
            self._loading.discard(key)
        ttl = self.ttl_s if status != UNREACHABLE else min(self.ttl_s, self.unreachable_ttl_s)
        self._entries[key] = (now + ttl, robots, status)
        return robots, status

    def _load(self, robots_url: str) -> tuple[RobotsTxt | None, str]:
        url = robots_url
        followed = 0  # redirects so far, including any the fetch followed by itself
        while True:
            self._inflight.add(url)
            try:
                res = self.fetch(url)
            except (httpx.HTTPError, OSError, TimeoutError):
                return None, UNREACHABLE
            finally:
                self._inflight.discard(url)
            followed += len(res.redirects or [])
            status = res.status
            if status is None or (res.error or "").startswith("gate_refused:"):
                return None, UNREACHABLE  # network error, or our own gate refused a hop: the file is unknown
            if 200 <= status < 300:
                return RobotsTxt.parse_bytes(res.body or b""), OK
            if status in REDIRECT_STATUSES:
                location = _header(res.headers, "location")
                if not location or followed >= MAX_REDIRECTS or (res.error or "").startswith("too_many_redirects"):
                    return None, UNAVAILABLE  # RFC 9309 §2.3.1.2: may be treated as unavailable
                try:
                    url = str(httpx.URL(res.final_url or url).join(location))
                except httpx.InvalidURL:
                    return None, UNAVAILABLE
                followed += 1
                continue
            if status == 429 or status >= 500:
                return None, UNREACHABLE
            return None, UNAVAILABLE  # 4xx (and anything else that is not a server error)
