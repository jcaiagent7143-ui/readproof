"""PolicyGate (readproof/policy.py): order of checks, deny list, SSRF guard, robots and Content-Signal per
profile, robots status semantics, purposes, logging. Offline: every resolver and robots file is scripted."""

from __future__ import annotations

from dataclasses import fields

import pytest

from readproof.policy import DEFAULT_DENY, PolicyGate, address_problem, check_addresses, denied, parse_target
from readproof.profiles import P0, P1, P2
from readproof.robots import RobotsCache
from readproof.testing import FakeNet, MemoryLog, Scripted
from readproof.types import GateDecision

PUBLIC = "93.184.215.14"


def public(_host: str) -> list[str]:
    return [PUBLIC]


class Resolver:
    """Scripted DNS that records what it was asked."""

    def __init__(self, answers: dict[str, list[str]] | None = None, default: list[str] | None = None) -> None:
        self.answers = answers or {}
        self.default = [PUBLIC] if default is None else default
        self.asked: list[str] = []

    def __call__(self, host: str) -> list[str]:
        self.asked.append(host)
        if host in self.answers:
            return self.answers[host]
        if self.default == ["fail"]:
            raise OSError("nodename nor servname provided")
        return self.default


def gate_with(profile, robots_txt: str | None = None, status: int = 200, *, resolve=public, log=None,
              routes: dict | None = None) -> tuple[PolicyGate, FakeNet]:
    routes = dict(routes or {})
    if robots_txt is not None or status != 200:
        routes.setdefault("https://example.com/robots.txt", Scripted(status, robots_txt or ""))
    fake = FakeNet(routes)
    robots = RobotsCache(lambda u: fake.fetch(u, purpose="robots"))
    return PolicyGate(profile, robots=robots, resolve=resolve, log=log), fake


# --- order and URL checks ------------------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd", "javascript:alert(1)",
                                 "data:text/html,hi", "gopher://example.com/", "ftp://www.linkedin.com/in/x"])
def test_scheme_is_checked_first(url):
    d = PolicyGate(P1, resolve=public).check(url)
    assert (d.allowed, d.reason) == (False, "scheme")


@pytest.mark.parametrize("url", ["http://", "https:///path", "http://[::1/", "not a url at all"])
def test_unparseable_or_hostless_urls(url):
    d = PolicyGate(P1, resolve=public).check(url)
    assert not d.allowed and d.reason in ("invalid_url", "scheme")


def test_credentials_in_the_url_are_refused():
    d = PolicyGate(P1, resolve=public).check("https://user:secret@example.com/post/1")
    assert (d.allowed, d.reason) == (False, "credentials")


@pytest.mark.parametrize("profile", [P0, P1, P2])
@pytest.mark.parametrize("url", ["https://www.linkedin.com/posts/someone_activity-123",
                                 "https://linkedin.com/in/someone", "http://LINKEDIN.COM/feed",
                                 "https://uk.linkedin.com/company/x", "https://www.linkedin.com./posts/x",
                                 "https://a.b.linkedin.com:8443/x"])
def test_deny_list_refuses_under_every_profile_before_dns(profile, url):
    dns = Resolver()
    d = PolicyGate(profile, resolve=dns).check(url)
    assert (d.allowed, d.reason) == (False, "terms")
    assert dns.asked == []  # refused before any lookup


@pytest.mark.parametrize("host", ["notlinkedin.com", "linkedin.com.example.org", "linkedincom.net"])
def test_deny_list_matches_domains_not_substrings(host):
    assert denied(host, DEFAULT_DENY) is None
    assert PolicyGate(P1, resolve=public).check(f"https://{host}/x").allowed


def test_custom_deny_list():
    gate = PolicyGate(P1, deny={"example.org": "owner_request"}, resolve=public)
    assert gate.check("https://cdn.example.org/a").reason == "owner_request"
    assert gate.check("https://www.linkedin.com/x").allowed  # a custom list replaces the gate's default
    assert DEFAULT_DENY == {"linkedin.com": "terms"}


def test_parse_target():
    u, reason = parse_target("https://Example.COM:443/a/b?c=d#frag")
    assert reason is None and u.raw_host == b"example.com"
    assert parse_target("https://例子.测试/x")[0].raw_host.decode().startswith("xn--")


# --- SSRF guard ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("ip,problem", [
    ("127.0.0.1", "loopback"), ("127.8.9.10", "loopback"), ("::1", "loopback"),
    ("10.1.2.3", "private"), ("172.16.5.4", "private"), ("192.168.1.1", "private"), ("fc00::1", "private"),
    ("169.254.169.254", "link_local"), ("fe80::1", "link_local"),
    ("224.0.0.251", "multicast"), ("ff02::1", "multicast"),
    ("0.0.0.0", "unspecified"), ("::", "unspecified"),
    ("100.64.0.1", "cgnat"), ("100.127.255.254", "cgnat"),
    ("240.0.0.1", "private"), ("255.255.255.255", "private"),  # reserved ranges (Python files them as private)
    ("192.0.2.10", "private"), ("198.18.0.1", "private"),
    ("fec0::1", "site_local"),
    ("::ffff:127.0.0.1", "loopback"), ("::ffff:10.0.0.1", "private"),  # IPv4-mapped: judged as IPv4
    ("64:ff9b::7f00:1", "loopback"), ("64:ff9b::a00:1", "private"),  # NAT64 embedding a private address
    ("2001:db8::1", "private"), ("2002:7f00:1::1", "private"), ("::7f00:1", "reserved"),
    ("fe80::1%en0", "link_local"),
])
def test_address_problem_names_the_range(ip, problem):
    assert address_problem(ip) == problem


@pytest.mark.parametrize("ip", ["93.184.215.14", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111",
                                "::ffff:8.8.8.8", "64:ff9b::808:808"])
def test_public_addresses_pass(ip):
    assert address_problem(ip) is None


@pytest.mark.parametrize("addrs", [["10.0.0.1"], ["127.0.0.1"], ["169.254.169.254"], ["224.0.0.1"], ["0.0.0.0"],
                                   ["100.64.1.1"], ["240.1.2.3"], [PUBLIC, "192.168.0.10"], ["::1", PUBLIC]])
def test_any_private_address_refuses(addrs):
    log = MemoryLog()
    d = PolicyGate(P1, resolve=lambda h: addrs, log=log).check("https://looks-public.example/x")
    assert (d.allowed, d.reason) == (False, "private_address")
    ev = log.of("gate")[0]
    assert ev["addresses"] == addrs and ev["address_problems"]


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://[::1]:8080/admin", "http://169.254.169.254/latest/meta-data/",
                                 "http://[::ffff:127.0.0.1]/", "http://0.0.0.0:22/", "http://10.0.0.1/"])
def test_ip_literals_are_judged_without_dns(url):
    d = PolicyGate(P1).check(url)  # default resolver: literals never reach DNS
    assert (d.allowed, d.reason) == (False, "private_address")


def test_ip_literals_never_reach_the_resolver():
    dns = Resolver(default=[PUBLIC])  # a resolver that would call anything public
    gate = PolicyGate(P1, resolve=dns)
    for url in ("http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest"):
        assert gate.check(url).reason == "private_address"
    assert dns.asked == []
    assert check_addresses("[::1]", dns).reason == "private_address"


def test_resolver_failure_is_not_a_policy_refusal():
    log = MemoryLog()
    gate = PolicyGate(P1, resolve=Resolver(default=["fail"]), log=log)
    d = gate.check("https://nonexistent.example/x")
    assert d.allowed  # the fetch fails as a network error instead (HttpNet sends nothing without addresses)
    assert "resolve_error" in log.of("gate")[0]
    chk = check_addresses("nonexistent.example", Resolver(default=["fail"]))
    assert chk.reason is None and chk.addresses == [] and chk.error


# --- robots under each profile -------------------------------------------------------------------------------

ROBOTS = "User-agent: *\nDisallow: /private\nAllow: /private/ok\n"


def test_p0_robots_disallow_refuses_and_is_binding():
    gate, _ = gate_with(P0, ROBOTS)
    d = gate.check("https://example.com/private/1?x=2")
    assert (d.allowed, d.reason, d.robots_binding) == (False, "robots_disallow", True)
    assert (d.robots_group, d.matched_rule) == ("*", "Disallow: /private")
    assert d.robots_url == "https://example.com/robots.txt"
    d = gate.check("https://example.com/private/ok")
    assert (d.allowed, d.reason, d.robots_binding, d.matched_rule) == (True, "allowed", True, "Allow: /private/ok")
    d = gate.check("https://example.com/public")
    assert (d.allowed, d.robots_group, d.matched_rule) == (True, "*", None)


@pytest.mark.parametrize("profile", [P1, P2])
def test_p1_p2_robots_disallow_is_recorded_not_binding(profile):
    log = MemoryLog()
    gate, _ = gate_with(profile, ROBOTS, log=log)
    d = gate.check("https://example.com/private/1")
    assert (d.allowed, d.reason, d.robots_binding) == (True, "allowed", False)
    assert (d.robots_group, d.matched_rule) == ("*", "Disallow: /private")
    ev = log.of("gate")[-1]
    assert ev["robots_allowed"] is False and ev["robots_status"] == "ok" and ev["matched_rule"] == "Disallow: /private"


def test_our_named_group_wins_over_star():
    gate, _ = gate_with(P0, "User-agent: ReadProof\nAllow: /\n\nUser-agent: *\nDisallow: /\n")
    d = gate.check("https://example.com/post/1")
    assert (d.allowed, d.robots_group) == (True, "readproof")
    gate, _ = gate_with(P0, "User-agent: readproof\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    assert gate.check("https://example.com/post/1").reason == "robots_disallow"


def test_robots_is_fetched_once_per_origin():
    gate, fake = gate_with(P1, ROBOTS)
    for p in ("/a", "/b", "/private/c", "/d?e=f"):
        gate.check("https://example.com" + p)
    assert fake.requests == [("GET", "https://example.com/robots.txt")]


# --- Content-Signal ------------------------------------------------------------------------------------------

AI_INPUT_NO = "User-agent: *\nContent-Signal: search=yes, ai-input=no, ai-train=no\nAllow: /\n"


def test_p0_content_signal_ai_input_no_refuses():
    gate, _ = gate_with(P0, AI_INPUT_NO)
    d = gate.check("https://example.com/post/1")
    assert (d.allowed, d.reason) == (False, "content_signal")
    assert d.content_signal == {"search": "yes", "ai-input": "no", "ai-train": "no"}


@pytest.mark.parametrize("profile", [P1, P2])
def test_p1_p2_content_signal_is_logged(profile):
    log = MemoryLog()
    gate, _ = gate_with(profile, AI_INPUT_NO, log=log)
    d = gate.check("https://example.com/post/1")
    assert d.allowed and d.content_signal["ai-input"] == "no"
    assert log.of("gate")[-1]["content_signal"]["ai-input"] == "no"


def test_ai_input_yes_and_ai_train_no_do_not_refuse_p0():
    gate, _ = gate_with(P0, "User-agent: *\nContent-Signal: search=yes, ai-input=yes, ai-train=no\nAllow: /\n")
    d = gate.check("https://example.com/post/1")
    assert d.allowed and d.content_signal == {"search": "yes", "ai-input": "yes", "ai-train": "no"}


def test_robots_disallow_is_reported_before_content_signal():
    gate, _ = gate_with(P0, "User-agent: *\nContent-Signal: ai-input=no\nDisallow: /\n")
    assert gate.check("https://example.com/x").reason == "robots_disallow"


def test_content_usage_is_logged():
    log = MemoryLog()
    gate, _ = gate_with(P0, "User-agent: *\nContent-Usage: train-ai=n\nAllow: /\n", log=log)
    assert gate.check("https://example.com/x").allowed  # recorded, never binding
    assert log.of("gate")[-1]["content_usage"] == {"train-ai": "n"}


def test_no_signals_means_evaluated_and_empty():
    gate, _ = gate_with(P0, "User-agent: *\nAllow: /\n")
    assert gate.check("https://example.com/x").content_signal == {}


# --- robots fetch status (RFC 9309 §2.3.1) at the gate ---------------------------------------------------------

@pytest.mark.parametrize("status", [401, 403, 404, 410])
def test_unavailable_robots_allows_all_even_under_p0(status):
    gate, _ = gate_with(P0, None, status=status)
    d = gate.check("https://example.com/anything")
    assert d.allowed and d.robots_binding and d.matched_rule == "(robots.txt unavailable: allow all)"


@pytest.mark.parametrize("status", [429, 500, 503])
def test_unreachable_robots_disallows_all_under_p0(status):
    gate, _ = gate_with(P0, None, status=status)
    d = gate.check("https://example.com/anything")
    assert (d.allowed, d.reason) == (False, "robots_disallow")
    assert d.matched_rule == "(robots.txt unreachable: disallow all)"


def test_unreachable_robots_is_logged_under_p1():
    log = MemoryLog()
    gate, _ = gate_with(P1, None, status=503, log=log)
    d = gate.check("https://example.com/anything")
    assert d.allowed and not d.robots_binding
    assert log.of("gate")[-1]["robots_status"] == "unreachable"


def test_network_error_fetching_robots_is_unreachable():
    gate, _ = gate_with(P0, routes={"https://example.com/robots.txt": Scripted(error="ConnectError: reset")})
    assert gate.check("https://example.com/x").reason == "robots_disallow"


# --- purposes, missing robots, logging -----------------------------------------------------------------------

def test_only_the_robots_cache_s_own_requests_skip_robots():
    fake = FakeNet({"https://example.com/robots.txt": Scripted(200, "User-agent: *\nDisallow: /\n")})
    inner: list[GateDecision] = []

    def fetch(u):  # how make_stack wires it: the cache fetches through the gated net
        inner.append(gate.check(u, purpose="robots"))
        return fake.fetch(u, purpose="robots")

    gate = PolicyGate(P0, robots=RobotsCache(fetch), resolve=public)
    assert gate.check("https://example.com/post/1").reason == "robots_disallow"
    assert len(inner) == 1 and inner[0].allowed and inner[0].robots_url is None  # not evaluated: no recursion
    # A caller cannot opt out of rule 7 by naming its purpose "robots".
    d = gate.check("https://example.com/post/1", purpose="robots")
    assert (d.allowed, d.reason, d.robots_url) == (False, "robots_disallow", "https://example.com/robots.txt")
    assert gate.check("https://example.com/robots.txt", purpose="robots").allowed  # implicitly allowed anyway
    assert len(fake.requests) == 1


def test_purpose_robots_keeps_the_other_checks():
    gate, _ = gate_with(P0, "User-agent: *\nAllow: /\n")
    assert gate.check("https://www.linkedin.com/robots.txt", purpose="robots").reason == "terms"
    gate = PolicyGate(P0, resolve=lambda h: ["127.0.0.1"])
    assert gate.check("https://example.com/robots.txt", purpose="robots").reason == "private_address"


def test_p0_without_a_robots_cache_fails_closed():
    d = PolicyGate(P0, resolve=public).check("https://example.com/x")
    assert (d.allowed, d.reason) == (False, "profile")
    log = MemoryLog()
    d = PolicyGate(P1, resolve=public, log=log).check("https://example.com/x")
    assert d.allowed and log.of("gate")[0]["robots_status"] == "not_configured"


def test_profile_purposes():
    assert PolicyGate(P2, resolve=public).check("https://example.com/x", purpose="browser").reason == "profile"
    assert PolicyGate(P2, resolve=public).check("https://example.com/v.mp4", purpose="media").reason == "profile"
    assert PolicyGate(P2, resolve=public).check("https://example.com/embed/1", purpose="embed").reason == "profile"
    gate, _ = gate_with(P1, "User-agent: *\nAllow: /\n")
    assert gate.check("https://example.com/x", purpose="browser").allowed
    assert gate.check("https://example.com/v.mp4", purpose="media").allowed
    assert gate.check("https://example.com/embed/1", purpose="embed").allowed


def test_every_check_is_logged_with_the_full_decision():
    log = MemoryLog()
    gate, _ = gate_with(P0, ROBOTS, log=log)
    urls = ["https://example.com/private/1", "https://example.com/ok", "ftp://example.com/",
            "https://www.linkedin.com/x", "http://127.0.0.1/"]
    decisions = [gate.check(u) for u in urls]
    events = log.of("gate")
    assert len(events) == len(urls)
    for d, ev in zip(decisions, events):
        for f in fields(GateDecision):
            assert ev[f.name] == getattr(d, f.name)
        assert ev["purpose"] == "read"
    assert [e["reason"] for e in events] == ["robots_disallow", "allowed", "scheme", "terms", "private_address"]
