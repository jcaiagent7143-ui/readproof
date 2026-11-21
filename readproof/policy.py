"""PolicyGate: every request is decided here before it is sent (DESIGN.md §1 rules 5, 7, 8).

Order, and why: the URL must be http(s) ("scheme") — nothing else can be fetched politely; the deny list
("terms") refuses LinkedIn under every profile, before any DNS lookup; the SSRF guard ("private_address")
resolves the host and refuses if ANY address is not public, so a name pointing at both a public and a
private address cannot slip through; robots.txt and Content-Signal are evaluated for every request and
recorded, binding only under P0. The robots cache's own requests (purpose="robots" AND
RobotsCache.fetching(url)) skip robots, which keeps the gate from recursing; any other request that calls
itself "robots" is evaluated like a read, so no caller can opt out of rule 7.

Purposes with a profile check: "browser" needs profile.allow_browser; "media", "embed" (the official
player) and "embed_media" need profile.allow_embed_media (refused with reason "profile").

Reasons beyond types.GateDecision's list: "terms" (deny list), "invalid_url" (unparseable or no host),
"credentials" (user:password@ in the URL; httpx would send it as Basic auth).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import httpx

from .interfaces import Log
from .robots import OK, TOKEN, UNAVAILABLE, RobotsCache, product_token
from .types import GateDecision, Profile

DEFAULT_DENY: dict[str, str] = {"linkedin.com": "terms"}  # matches the host and every subdomain

BROWSER_PURPOSES = frozenset({"browser"})  # needs profile.allow_browser
MEDIA_PURPOSES = frozenset({"media", "embed", "embed_media"})  # needs profile.allow_embed_media

_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def default_resolve(host: str) -> list[str]:
    """Every address the host resolves to, in resolver order (IP literals are returned as they are)."""
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        return [host]
    except ValueError:
        pass
    out: list[str] = []
    for *_, sockaddr in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
        ip = str(sockaddr[0])
        if ip not in out:
            out.append(ip)
    return out


def address_problem(ip: str) -> str | None:
    """Why an address is not a public one ("loopback", "cgnat", …), or None if it is public.

    Checked explicitly rather than trusting `is_global` alone: Python counts multicast and fec0::/10 as
    global, does not call CGNAT private, and marks NAT64 (64:ff9b::/96) reserved even when it embeds a public
    IPv4 address, so an IPv4-mapped or NAT64 address is judged by the IPv4 address inside it."""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return "unparseable"
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return address_problem(str(addr.ipv4_mapped))
        if addr in _NAT64:
            return address_problem(str(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)))
        if addr.is_site_local:
            return "site_local"
    if addr.is_unspecified:
        return "unspecified"
    if addr.is_loopback:
        return "loopback"
    if addr.is_link_local:
        return "link_local"
    if addr.is_multicast:
        return "multicast"
    if isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT:
        return "cgnat"
    if addr.is_private:
        return "private"
    if addr.is_reserved:
        return "reserved"
    if not addr.is_global:
        return "not_global"
    return None


@dataclass
class AddressCheck:
    reason: str | None  # "private_address" or None
    addresses: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # e.g. ["127.0.0.1 loopback"]
    error: str | None = None  # resolver failure: not a refusal, the request fails as a network error


def check_addresses(host: str, resolve: Callable[[str], list[str]]) -> AddressCheck:
    """Resolve and judge every address. Refuses if ANY is not public. A resolver failure is not a policy
    refusal (the read is a network failure); HttpNet then sends nothing, so the guard still fails closed.
    An IP literal is its own address: it never reaches a resolver, injected or not."""
    literal = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        ipaddress.ip_address(literal.split("%", 1)[0])
        addrs = [literal]
    except ValueError:
        try:
            addrs = [str(a) for a in resolve(host)]
        except (OSError, UnicodeError, ValueError) as e:
            return AddressCheck(None, [], [], f"{type(e).__name__}: {e}")
    if not addrs:
        return AddressCheck(None, [], [], "no addresses")
    problems = [f"{a} {p}" for a in addrs if (p := address_problem(a))]
    return AddressCheck("private_address" if problems else None, addrs, problems)


def parse_target(url: str) -> tuple[httpx.URL | None, str | None]:
    """(parsed URL, None) or (None, refusal reason). Parsed with httpx so the gate sees exactly what the
    client will send; two different URL parsers would be an SSRF hole."""
    try:
        u = httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError):
        return None, "invalid_url"
    if u.scheme not in ("http", "https"):
        return None, "scheme"
    if not u.raw_host:
        return None, "invalid_url"
    if u.userinfo:
        return None, "credentials"
    return u, None


def host_of(u: httpx.URL) -> str:
    return u.raw_host.decode("ascii").lower()


def denied(host: str, deny: dict[str, str]) -> str | None:
    """The deny-list reason for a host (exact domain or any subdomain; case and a trailing dot ignored)."""
    h = host.lower().rstrip(".")
    for domain, reason in deny.items():
        d = domain.lower().strip(".")
        if h == d or h.endswith("." + d):
            return reason
    return None


class PolicyGate:
    """Decides every request before it is sent. See the module docstring for the order of checks."""

    def __init__(self, profile: Profile, robots: RobotsCache | None = None, deny: dict[str, str] = DEFAULT_DENY,
                 resolve: Callable[[str], list[str]] | None = None, log: Log | None = None) -> None:
        self.profile = profile
        self.robots = robots
        self.deny = dict(deny)
        self.resolve = resolve or default_resolve  # HttpNet reuses this resolver for its own per-hop check
        self.log = log
        self.token = product_token(profile.user_agent) or TOKEN

    def check(self, url: str, *, purpose: str = "read") -> GateDecision:
        decision, extra = self._decide(url, purpose)
        if self.log is not None:
            self.log.emit("gate", **asdict(decision), purpose=purpose, **extra)
        return decision

    def _robots_fetch(self, url: str, u: httpx.URL) -> bool:
        """purpose="robots" skips robots evaluation only for the robots cache's own requests; anything else
        that calls itself "robots" is evaluated like a read."""
        fetching = getattr(self.robots, "fetching", None)
        if callable(fetching):
            return bool(fetching(url))
        return self.robots is not None and u.path == "/robots.txt"

    def _refuse(self, url: str, reason: str, **kw: Any) -> GateDecision:
        return GateDecision(url=url, allowed=False, profile=self.profile.name, reason=reason, **kw)

    def _decide(self, url: str, purpose: str) -> tuple[GateDecision, dict[str, Any]]:
        extra: dict[str, Any] = {}
        u, bad = parse_target(url)
        if u is None:
            return self._refuse(url, bad or "invalid_url"), extra
        host = host_of(u)
        reason = denied(host, self.deny)
        if reason:
            return self._refuse(url, reason), extra

        chk = check_addresses(host, self.resolve)
        extra["addresses"] = chk.addresses
        if chk.problems:
            extra["address_problems"] = chk.problems
        if chk.error:
            extra["resolve_error"] = chk.error
        if chk.reason:
            return self._refuse(url, chk.reason), extra

        if (purpose in BROWSER_PURPOSES and not self.profile.allow_browser) or (
                purpose in MEDIA_PURPOSES and not self.profile.allow_embed_media):
            return self._refuse(url, "profile", matched_rule=f"(profile {self.profile.name} forbids {purpose})"), extra

        if purpose == "robots" and self._robots_fetch(url, u):
            return GateDecision(url=url, allowed=True, profile=self.profile.name, reason="allowed"), extra

        if self.robots is None:
            # Rule 7 cannot be honoured without a robots cache: P0 fails closed, P1/P2 record the gap.
            extra["robots_status"] = "not_configured"
            if self.profile.obey_robots:
                return self._refuse(url, "profile", matched_rule="(robots.txt not configured)"), extra
            return GateDecision(url=url, allowed=True, profile=self.profile.name, reason="allowed"), extra

        robots_url = RobotsCache.robots_url(url)
        txt, status = self.robots.for_url(url)
        path = u.raw_path.decode("ascii")
        signals: dict[str, str] = {}
        if status == OK and txt is not None:
            robots_ok, group, rule = txt.decide(self.token, path)
            signals = txt.signals_for(self.token, path)
            usage = txt.signals_for(self.token, path, kind="content-usage")
            if usage:
                extra["content_usage"] = usage
        elif status == UNAVAILABLE:
            robots_ok, group, rule = True, None, "(robots.txt unavailable: allow all)"
        else:
            robots_ok, group, rule = False, None, "(robots.txt unreachable: disallow all)"
        extra["robots_status"] = status
        extra["robots_allowed"] = robots_ok

        decision = GateDecision(url=url, allowed=True, profile=self.profile.name, reason="allowed",
                                robots_url=robots_url, robots_group=group, matched_rule=rule,
                                robots_binding=self.profile.obey_robots, content_signal=signals)
        if not robots_ok and self.profile.obey_robots:
            decision.allowed, decision.reason = False, "robots_disallow"
        elif signals.get("ai-input") == "no" and self.profile.obey_content_signal:
            decision.allowed, decision.reason = False, "content_signal"
        return decision, extra
