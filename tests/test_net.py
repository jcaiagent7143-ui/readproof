"""HttpNet (readproof/net.py) against httpx.MockTransport: identity, cookies, retries, proxies, redirects,
SSRF on every hop, max_bytes, logging. Offline: no test opens a socket."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime

import httpcore
import httpx
import pytest

import readproof.net as netmod
from readproof import USER_AGENT
from readproof.net import AddressRefused, GuardedTransport, HttpNet, IdentityError, _GuardedBackend, make_stack
from readproof.profiles import P0, P1
from readproof.testing import AllowGate, MemoryLog

PUBLIC = "93.184.215.14"


def public(_host: str) -> list[str]:
    return [PUBLIC]


def R(status: int = 200, body: bytes | str = b"", headers: dict | None = None):
    return ("resp", status, body.encode() if isinstance(body, str) else body, headers or {})


class Server:
    """Scripted responses per URL (a list is consumed in order; the last item repeats). Records requests."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = {k: (list(v) if isinstance(v, list) else [v]) for k, v in routes.items()}
        self.seen: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        queue = self.routes.get(str(request.url))
        if not queue:
            return httpx.Response(404, text="not found")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        _, status, body, headers = item
        return httpx.Response(status, headers=headers, content=body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.seen]


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(netmod, "_sleep", calls.append)
    return calls


def net_for(server: Server, profile=P1, **kw) -> HttpNet:
    kw.setdefault("resolve", public)
    return HttpNet(profile, transport=server.transport, **kw)


# --- rule 1: declared identity -------------------------------------------------------------------------------

def test_every_request_carries_exactly_the_declared_user_agent():
    s = Server({"https://example.com/a": R(302, headers={"Location": "/b"}),
                "https://example.com/b": [R(503), R(200, "ok")]})
    res = net_for(s).fetch("https://example.com/a", follow_redirects=True)
    assert res.status == 200 and len(s.seen) == 3
    for req in s.seen:
        assert req.headers.get_list("user-agent") == [USER_AGENT]
    assert res.user_agent == USER_AGENT


@pytest.mark.parametrize("name", ["User-Agent", "user-agent", "USER-AGENT", "Cookie", "cookie2", "Host",
                                  "X-Forwarded-For", "Forwarded", "X-Real-IP", "True-Client-IP", "Via",
                                  "Sec-CH-UA", "sec-ch-ua-platform", "X-Requested-With"])
def test_identity_and_address_headers_cannot_be_set_by_callers(name):
    s = Server({"https://example.com/": R(200)})
    with pytest.raises(ValueError):
        net_for(s).fetch("https://example.com/", headers={name: "x"})
    assert s.seen == []


def test_other_caller_headers_are_sent():
    s = Server({"https://example.com/": R(200)})
    net_for(s).fetch("https://example.com/", headers={"Accept-Language": "zh-CN", "Referer": "https://example.com/"})
    assert s.seen[0].headers["accept-language"] == "zh-CN"


def test_the_request_hook_rejects_anything_else():
    net = net_for(Server({}))
    with pytest.raises(IdentityError):
        net._on_request(httpx.Request("GET", "https://example.com/", headers={"User-Agent": USER_AGENT, "Cookie": "a=b"}))
    with pytest.raises(IdentityError):
        net._on_request(httpx.Request("GET", "https://example.com/", headers={"User-Agent": "Mozilla/5.0 (iPhone)"}))
    with pytest.raises(IdentityError):
        net._on_request(httpx.Request("GET", "https://example.com/", headers=[("User-Agent", USER_AGENT),
                                                                              ("User-Agent", "curl/8")]))


# --- rule 2: no cookies --------------------------------------------------------------------------------------

def test_no_cookie_is_sent_even_after_the_server_sets_one():
    set_cookie = {"Set-Cookie": "sid=abc123; Domain=example.com; Path=/; Max-Age=3600"}
    s = Server({"https://example.com/1": R(200, "one", set_cookie), "https://example.com/2": R(200, "two", set_cookie),
                "https://example.com/3": R(200, "three")})
    net = net_for(s)
    for i in (1, 2, 3):
        res = net.fetch(f"https://example.com/{i}")
        assert res.status == 200
        assert len(net._client.cookies) == 0
    assert all("cookie" not in r.headers for r in s.seen)
    assert "set-cookie" in net_for(s).fetch("https://example.com/1").headers  # kept as evidence, never obeyed


def test_no_cookie_across_a_redirect_that_sets_one():
    s = Server({"https://example.com/a": R(302, headers={"Location": "/b", "Set-Cookie": "s=1; Path=/"}),
                "https://example.com/b": R(200, "ok")})
    net_for(s).fetch("https://example.com/a", follow_redirects=True)
    assert len(s.seen) == 2 and "cookie" not in s.seen[1].headers


def test_a_cookie_placed_in_the_jar_is_dropped_before_sending():
    s = Server({"https://example.com/": R(200)})
    net = net_for(s)
    net._client.cookies.set("sid", "planted", domain="example.com")
    net.fetch("https://example.com/")
    assert "cookie" not in s.seen[0].headers and len(net._client.cookies) == 0


# --- rule 4: retries -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("code", [400, 401, 403, 404, 410, 412, 451])
def test_no_retry_after_a_refusal(code, sleeps):
    s = Server({"https://example.com/p": R(code, "no")})
    res = net_for(s).fetch("https://example.com/p")
    assert (res.status, res.attempt, len(s.seen), sleeps) == (code, 1, 1, [])


def test_no_retry_after_a_200_challenge_page(sleeps):
    s = Server({"https://example.com/p": R(200, "<title>验证码</title><div id=captcha></div>")})
    res = net_for(s).fetch("https://example.com/p")
    assert (res.attempt, len(s.seen), sleeps) == (1, 1, [])


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_exactly_one_retry_after_429_or_5xx(code, sleeps):
    s = Server({"https://example.com/p": R(code, "busy")})
    net = net_for(s)
    res = net.fetch("https://example.com/p")
    assert (res.status, res.attempt, len(s.seen)) == (code, 2, 2)
    assert sleeps == [P1.backoff_s]
    assert [r.headers["user-agent"] for r in s.seen] == [USER_AGENT, USER_AGENT]


def test_the_retry_may_succeed():
    s = Server({"https://example.com/p": [R(503), R(200, "fine")]})
    res = net_for(s).fetch("https://example.com/p")
    assert (res.status, res.attempt, res.body) == (200, 2, b"fine")


@pytest.mark.parametrize("exc", [httpx.ConnectError("connection refused"), httpx.ReadTimeout("timed out"),
                                 httpx.RemoteProtocolError("peer closed")])
def test_exactly_one_retry_after_a_transport_error(exc, sleeps):
    s = Server({"https://example.com/p": exc})
    res = net_for(s).fetch("https://example.com/p")
    assert res.status is None and res.error.startswith(type(exc).__name__)
    assert (res.attempt, len(s.seen), sleeps) == (2, 2, [P1.backoff_s])
    assert res.body == b"" and res.bytes == 0


def test_retry_after_is_respected_or_ends_the_retry(sleeps):
    s = Server({"https://example.com/p": R(429, headers={"Retry-After": "5"})})
    net_for(s).fetch("https://example.com/p")
    assert sleeps == [5.0] and len(s.seen) == 2
    s = Server({"https://example.com/p": R(503, headers={"Retry-After": "3600"})})
    res = net_for(s).fetch("https://example.com/p")
    assert len(s.seen) == 1 and res.attempt == 1  # asked to wait an hour: no retry at all
    sleeps.clear()
    s = Server({"https://example.com/p": R(503, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})})
    net_for(s).fetch("https://example.com/p")
    assert sleeps == [P1.backoff_s]


def test_max_retries_zero_means_no_retry():
    s = Server({"https://example.com/p": R(503)})
    res = net_for(s, profile=replace(P1, max_retries=0)).fetch("https://example.com/p")
    assert (res.attempt, len(s.seen)) == (1, 1)


def test_the_retry_budget_is_per_call_across_hops():
    s = Server({"https://example.com/a": [R(503), R(302, headers={"Location": "/b"})], "https://example.com/b": R(503)})
    res = net_for(s).fetch("https://example.com/a", follow_redirects=True)
    assert s.urls() == ["https://example.com/a", "https://example.com/a", "https://example.com/b"]
    assert (res.status, res.final_url, res.attempt) == (503, "https://example.com/b", 1)


def test_retries_use_the_same_client_and_transport():
    s = Server({"https://example.com/p": [R(500), R(200)]})
    net = net_for(s)
    client, transport = net._client, net._client._transport
    net.fetch("https://example.com/p")
    assert net._client is client and net._client._transport is transport and len(s.seen) == 2


# --- rule 4: proxies -----------------------------------------------------------------------------------------

PROXY_ENV = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]


def test_environment_proxies_are_ignored(monkeypatch):
    for k in PROXY_ENV:
        monkeypatch.setenv(k, "http://proxy.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "socks5://proxy.invalid:1080")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    net = HttpNet(P1, resolve=public)  # the real transport, built while the proxy variables are set
    client = net._client
    assert client.trust_env is False
    assert client._mounts == {}
    t = client._transport_for_url(httpx.URL("https://example.com/"))
    assert isinstance(t, GuardedTransport)
    assert type(t._pool) is httpcore.ConnectionPool  # not HTTPProxy / SOCKSProxy
    s = Server({"https://example.com/": R(200, "direct")})
    assert net_for(s).fetch("https://example.com/").body == b"direct"  # served by the transport itself
    net.close()


# --- redirects: every hop gated ------------------------------------------------------------------------------

CHAIN = {"https://example.com/start": R(301, headers={"Location": "https://example.com/hop1"}),
         "https://example.com/hop1": R(302, headers={"Location": "/hop2"}),
         "https://example.com/hop2": R(200, "end")}


def test_each_redirect_hop_is_passed_to_the_gate_first():
    s = Server(dict(CHAIN))
    gate = AllowGate(P1)
    res = net_for(s, gate=gate).fetch("https://example.com/start", follow_redirects=True)
    assert gate.asked == ["https://example.com/start", "https://example.com/hop1", "https://example.com/hop2"]
    assert (res.status, res.final_url, res.body) == (200, "https://example.com/hop2", b"end")
    assert res.redirects == ["https://example.com/hop1", "https://example.com/hop2"]
    assert res.url == "https://example.com/start"


def test_a_refused_hop_stops_the_chain():
    s = Server(dict(CHAIN))
    gate = AllowGate(P1, deny=lambda u: "robots_disallow" if u.endswith("/hop2") else None)
    log = MemoryLog()
    res = net_for(s, gate=gate, log=log).fetch("https://example.com/start", follow_redirects=True)
    assert "https://example.com/hop2" not in s.urls()
    assert res.error == "gate_refused:robots_disallow"
    assert (res.status, res.final_url, res.redirects) == (302, "https://example.com/hop1", ["https://example.com/hop1"])
    assert res.headers["location"] == "/hop2"
    assert log.of("fetch_refused")[0]["url"] == "https://example.com/hop2"


def test_a_refused_first_url_sends_nothing():
    s = Server(dict(CHAIN))
    res = net_for(s, gate=AllowGate(P1, deny=lambda u: "terms")).fetch("https://example.com/start")
    assert (res.status, res.error, res.attempt, res.bytes) == (None, "gate_refused:terms", 0, 0)
    assert s.seen == []


def test_without_follow_redirects_the_3xx_is_returned():
    s = Server(dict(CHAIN))
    res = net_for(s).fetch("https://example.com/start")
    assert (res.status, res.headers["location"], res.redirects, len(s.seen)) == (
        301, "https://example.com/hop1", [], 1)


def test_redirect_methods_and_relative_locations():
    s = Server({"https://example.com/a/b": R(303, headers={"Location": "../c?x=1"}),
                "https://example.com/c?x=1": R(200)})
    res = net_for(s).fetch("https://example.com/a/b", method="POST", follow_redirects=True)
    assert [r.method for r in s.seen] == ["POST", "GET"] and res.method == "GET"
    s = Server({"https://example.com/h": R(302, headers={"Location": "/i"}), "https://example.com/i": R(200)})
    net_for(s).fetch("https://example.com/h", method="HEAD", follow_redirects=True)
    assert [r.method for r in s.seen] == ["HEAD", "HEAD"]
    s = Server({"https://example.com/p": R(307, headers={"Location": "/q"}), "https://example.com/q": R(200)})
    net_for(s).fetch("https://example.com/p", method="POST", follow_redirects=True)
    assert [r.method for r in s.seen] == ["POST", "POST"]


def test_at_most_eight_hops():
    s = Server({"https://example.com/loop": R(302, headers={"Location": "/loop"})})
    res = net_for(s).fetch("https://example.com/loop", follow_redirects=True)
    assert len(s.seen) == 9 and res.error == "too_many_redirects:8" and res.status == 302


def test_authorization_is_dropped_on_a_cross_origin_hop():
    s = Server({"https://api.example.com/a": R(302, headers={"Location": "/b"}),
                "https://api.example.com/b": R(302, headers={"Location": "https://other.example/c"}),
                "https://other.example/c": R(200)})
    net_for(s).fetch("https://api.example.com/a", headers={"Authorization": "Bearer t"}, follow_redirects=True)
    assert [("authorization" in r.headers) for r in s.seen] == [True, True, False]


# --- rule 5: SSRF on every hop -------------------------------------------------------------------------------

@pytest.mark.parametrize("addr", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "::1", "100.64.0.9", "224.0.0.1"])
def test_a_private_first_url_sends_nothing(addr):
    s = Server({"https://internal.example/": R(200, "secret")})
    res = net_for(s, resolve=lambda h: [addr]).fetch("https://internal.example/")
    assert (res.status, res.error, res.attempt) == (None, "gate_refused:private_address", 0)
    assert s.seen == []


@pytest.mark.parametrize("target", ["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8080/admin",
                                    "http://[::1]/", "http://internal.example/"])
def test_a_private_redirect_hop_is_refused(target):
    dns = {"internal.example": ["10.1.2.3"]}
    s = Server({"https://example.com/r": R(302, headers={"Location": target}), target: R(200, "secret")})
    res = net_for(s, resolve=lambda h: dns.get(h, [PUBLIC])).fetch("https://example.com/r", follow_redirects=True)
    assert s.urls() == ["https://example.com/r"]
    assert (res.status, res.error) == (302, "gate_refused:private_address")


def test_a_private_hop_is_refused_by_the_wired_gate_too():
    dns = {"internal.example": ["192.168.0.2"]}
    s = Server({"https://example.com/robots.txt": R(200, "User-agent: *\nAllow: /\n"),
                "https://example.com/r": R(302, headers={"Location": "http://internal.example/x"})})
    log = MemoryLog()
    _, net = make_stack(P1, transport=s.transport, resolve=lambda h: dns.get(h, [PUBLIC]), log=log)
    res = net.fetch("https://example.com/r", follow_redirects=True)
    assert res.error == "gate_refused:private_address"
    assert [e["reason"] for e in log.of("gate") if e["url"] == "http://internal.example/x"] == ["private_address"]


def test_dns_failure_sends_nothing_and_counts_as_a_transport_error(sleeps):
    def broken(_h):
        raise OSError("nodename nor servname provided")
    s = Server({"https://gone.example/": R(200)})
    res = net_for(s, resolve=broken).fetch("https://gone.example/")
    assert s.seen == [] and res.status is None and res.error.startswith("ConnectError: cannot resolve")
    assert res.attempt == 2 and sleeps == [P1.backoff_s]


def test_the_connect_time_guard_defeats_dns_rebinding():
    answers = iter([[PUBLIC], ["127.0.0.1"]])  # public when checked, loopback when connecting
    log = MemoryLog()
    net = HttpNet(P1, resolve=lambda h: next(answers), log=log)  # the real guarded transport
    res = net.fetch("http://rebind.example/")
    assert (res.status, res.error) == (None, "gate_refused:private_address")
    assert log.of("fetch_refused")[0]["by"] == "connect"
    net.close()


def test_guarded_backend_refuses_before_connecting():
    with pytest.raises(AddressRefused):
        _GuardedBackend(lambda h: ["10.0.0.1"]).connect_tcp("example.com", 443)
    with pytest.raises(AddressRefused):
        _GuardedBackend(public).connect_tcp("127.0.0.1", 80)  # an IP literal is judged as itself

    def broken(_h):
        raise OSError("no such host")
    with pytest.raises(httpcore.ConnectError):
        _GuardedBackend(broken).connect_tcp("example.com", 443)


# --- rule 8 and URL checks without a gate --------------------------------------------------------------------

def test_linkedin_is_refused_without_any_gate():
    s = Server({"https://www.linkedin.com/feed/": R(200), "https://example.com/r": R(302, headers={
        "Location": "https://www.linkedin.com/feed/"})})
    net = net_for(s)
    assert net.fetch("https://www.linkedin.com/feed/").error == "gate_refused:terms"
    res = net.fetch("https://example.com/r", follow_redirects=True)
    assert res.error == "gate_refused:terms" and s.urls() == ["https://example.com/r"]


@pytest.mark.parametrize("url,reason", [("ftp://example.com/x", "scheme"), ("file:///etc/hosts", "scheme"),
                                        ("https://u:p@example.com/", "credentials"), ("http://", "invalid_url")])
def test_unfetchable_urls(url, reason):
    s = Server({})
    assert net_for(s).fetch(url).error == f"gate_refused:{reason}" and s.seen == []


# --- body, result fields, log ---------------------------------------------------------------------------------

def test_max_bytes_stops_the_body():
    body = bytes(range(256)) * 40  # 10,240 bytes
    s = Server({"https://example.com/big": R(200, body)})
    log = MemoryLog()
    net = net_for(s, log=log)
    res = net.fetch("https://example.com/big", max_bytes=1000)
    assert res.bytes == 1000 and res.body == body[:1000]
    assert res.sha256 == hashlib.sha256(body[:1000]).hexdigest()
    assert log.of("fetch")[-1]["truncated"] is True
    res = net.fetch("https://example.com/big", max_bytes=len(body))
    assert res.body == body and log.of("fetch")[-1]["truncated"] is False
    assert net.fetch("https://example.com/big", max_bytes=0).body == b""


def test_fetch_result_fields():
    s = Server({"https://example.com/p?q=1": R(200, "hello", {"Content-Type": "text/html; charset=utf-8"})})
    res = net_for(s, origin="residential:MY").fetch("https://example.com/p?q=1")
    assert (res.url, res.final_url, res.status, res.method, res.error, res.attempt) == (
        "https://example.com/p?q=1", "https://example.com/p?q=1", 200, "GET", None, 1)
    assert res.headers["content-type"].startswith("text/html")
    assert (res.bytes, res.sha256) == (5, hashlib.sha256(b"hello").hexdigest())
    assert res.origin == "residential:MY" and res.user_agent == USER_AGENT
    assert isinstance(res.elapsed_ms, int) and res.elapsed_ms >= 0
    assert datetime.fromisoformat(res.fetched_at).tzinfo is not None
    assert "body" not in res.to_log()


def test_every_attempt_is_logged_with_the_headers_sent():
    s = Server({"https://example.com/a": [R(503), R(302, headers={"Location": "/b"})], "https://example.com/b": R(200)})
    log = MemoryLog()
    net_for(s, log=log).fetch("https://example.com/a", headers={"Authorization": "Bearer secret"},
                              follow_redirects=True)
    events = log.of("fetch")
    assert len(events) == len(s.seen) == 3
    assert [(e["final_url"], e["attempt"], e["hop"]) for e in events] == [
        ("https://example.com/a", 1, 0), ("https://example.com/a", 2, 0), ("https://example.com/b", 1, 1)]
    for e in events:
        assert e["request_headers"]["user-agent"] == USER_AGENT
        assert "cookie" not in e["request_headers"]
        assert e["request_headers"]["authorization"] == "<redacted>"
        assert e["origin"] and e["sha256"] and "elapsed_ms" in e and "request_ms" in e


@pytest.mark.parametrize("fetch_follows", [False, True])
def test_a_robots_redirect_loop_is_bounded_however_the_cache_is_wired(fetch_follows):
    from readproof.robots import UNAVAILABLE, RobotsCache
    s = Server({"https://example.com/robots.txt": R(302, headers={"Location": "/loop"}),
                "https://example.com/loop": R(302, headers={"Location": "/loop"})})
    net = net_for(s)
    cache = RobotsCache(lambda u: net.fetch(u, purpose="robots", follow_redirects=fetch_follows))
    assert cache.for_url("https://example.com/x") == (None, UNAVAILABLE)
    assert len(s.seen) <= 1 + netmod.MAX_HOPS  # never the product of two redirect limits


def test_a_refused_hop_while_fetching_robots_is_unreachable():
    from readproof.robots import UNREACHABLE, RobotsCache
    s = Server({"https://example.com/robots.txt": R(301, headers={"Location": "http://10.0.0.1/robots.txt"})})
    net = net_for(s, resolve=lambda h: [PUBLIC])
    for follows in (False, True):
        cache = RobotsCache(lambda u: net.fetch(u, purpose="robots", follow_redirects=follows))
        assert cache.for_url("https://example.com/x") == (None, UNREACHABLE)


def test_make_stack_fetches_robots_through_the_same_identity():
    s = Server({"https://example.com/robots.txt": R(200, "User-agent: *\nDisallow: /no\n"),
                "https://example.com/yes": R(200, "y")})
    log = MemoryLog()
    gate, net = make_stack(P0, transport=s.transport, resolve=public, log=log)
    assert net.fetch("https://example.com/yes").status == 200
    assert net.fetch("https://example.com/no/1").error == "gate_refused:robots_disallow"
    assert s.urls() == ["https://example.com/robots.txt", "https://example.com/yes"]
    assert all(r.headers["user-agent"] == USER_AGENT and "cookie" not in r.headers for r in s.seen)
    assert [e["purpose"] for e in log.of("fetch")] == ["robots", "read"]
    assert net.gate is gate and gate.robots is not None
