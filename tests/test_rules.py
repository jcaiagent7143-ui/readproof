"""One test per non-negotiable rule in DESIGN.md §1. Each drives the code that enforces the rule; none relies on
a convention. Offline: requests go to httpx.MockTransport, DNS answers are scripted."""

from __future__ import annotations

import ast
import dataclasses
import importlib
import re
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

import readproof
import readproof.net as netmod
import readproof.policy as policymod
from readproof import USER_AGENT
from readproof.net import HttpNet, IdentityError, make_stack
from readproof.policy import PolicyGate
from readproof.profiles import ALL, P0, P1, P2
from readproof.testing import AllowGate, MemoryLog, now_iso
from readproof.types import FetchResult, sha256_bytes

PUBLIC = "93.184.215.14"
REPO = Path(__file__).resolve().parents[1]


def public(_host: str) -> list[str]:
    return [PUBLIC]


class Server:
    """MockTransport with scripted responses per URL (lists are consumed; the last item repeats)."""

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
        status, body, headers = item
        return httpx.Response(status, headers=headers, content=body.encode() if isinstance(body, str) else body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.seen]


ALLOW_ALL = (200, "User-agent: *\nAllow: /\n", {"Set-Cookie": "rb=1; Path=/"})


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(netmod, "_sleep", calls.append)
    return calls


# --- 1 ------------------------------------------------------------------------------------------------------

def test_rule1_declared_identity():
    assert USER_AGENT == f"ReadProof/{readproof.__version__} (+https://github.com/jcaiagent7143-ui/readproof)"
    for marker in ("Mozilla", "Chrome", "Safari", "AppleWebKit", "Gecko", "iPhone", "Android", "Windows NT"):
        assert marker not in USER_AGENT  # never a phone or desktop browser string
    assert {p.user_agent for p in ALL} == {USER_AGENT}

    s = Server({"https://example.com/robots.txt": ALLOW_ALL,
                "https://example.com/a": (302, "", {"Location": "/b"}),
                "https://example.com/b": [(503, "", {}), (200, "ok", {})]})
    _, net = make_stack(P1, transport=s.transport, resolve=public)
    assert net.fetch("https://example.com/a", follow_redirects=True).status == 200
    assert len(s.seen) == 4  # robots.txt, a, b, b again
    assert all(r.headers.get_list("user-agent") == [USER_AGENT] for r in s.seen)

    with pytest.raises(ValueError):
        net.fetch("https://example.com/a", headers={"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)"})
    with pytest.raises(IdentityError):  # the hook stops anything else, whoever built the request
        net._on_request(httpx.Request("GET", "https://example.com/", headers={"User-Agent": "python-httpx/0.28"}))
    assert len(s.seen) == 4


# --- 2 ------------------------------------------------------------------------------------------------------

def test_rule2_no_cookies():
    every = {"Set-Cookie": "sid=s3cr3t; Domain=example.com; Path=/; Max-Age=86400"}
    s = Server({"https://example.com/robots.txt": ALLOW_ALL,
                "https://example.com/1": (200, "one", every),
                "https://example.com/2": (302, "", {**every, "Location": "/3"}),
                "https://example.com/3": (200, "three", every)})
    _, net = make_stack(P1, transport=s.transport, resolve=public)
    net.fetch("https://example.com/1")
    net.fetch("https://example.com/2", follow_redirects=True)
    net.fetch("https://example.com/1")
    assert len(s.seen) == 5
    assert all("cookie" not in r.headers and "cookie2" not in r.headers for r in s.seen)
    assert len(net._client.cookies) == 0  # keeps no jar

    with pytest.raises(ValueError):
        net.fetch("https://example.com/1", headers={"Cookie": "sid=s3cr3t"})
    with pytest.raises(IdentityError):
        net._on_request(httpx.Request("GET", "https://example.com/", headers={"User-Agent": USER_AGENT,
                                                                             "Cookie": "sid=s3cr3t"}))
    res = net.fetch("https://user:password@example.com/1")  # credentials in a URL are a session too
    assert res.error == "gate_refused:credentials" and len(s.seen) == 5


# --- 3 ------------------------------------------------------------------------------------------------------

SIGNATURE_TERMS = ("x-bogus", "x_bogus", "a_bogus", "abogus", "x-s-common", "xs_common", "w_rid", "mstoken",
                   "ttwid generation", "device_register", "x-gorgon", "x-argus", "x-ladon",
                   "xbogus", "xscommon")  # the last two: camel-case spellings (XBogus, xsCommon)
_PIECES = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "runs", "store", "media",
             "build", "dist", ".mypy_cache", ".ruff_cache"}
SCRIPT_SUFFIXES = {".js", ".mjs", ".cjs", ".ts"}


def _words(s: str) -> list[str]:
    return [w.lower() for part in re.split(r"[^A-Za-z0-9]+", s) for w in _PIECES.findall(part)]


_TERMS = [(t, _words(t), re.sub(r"[^a-z0-9]", "", t)) for t in SIGNATURE_TERMS]


def _hits(s: str) -> list[str]:
    """Signature names in an identifier or literal, word-aware: "show_rid" is not "w_rid", "getXBogus" is."""
    ws = _words(s)
    found = []
    for term, tw, joined in _TERMS:
        n = len(tw)
        if any(ws[i:i + n] == tw for i in range(len(ws) - n + 1)):
            found.append(term)
            continue
        for i in range(len(ws)):  # the same name with its separators dropped ("msToken", "xbogus")
            acc = ""
            for w in ws[i:]:
                acc += w
                if acc == joined:
                    found.append(term)
                if len(acc) >= len(joined):
                    break
            if found and found[-1] == term:
                break
    return found


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def scan_python(source: str) -> list[tuple[int, str, str]]:
    """(line, term, text) for every definition, call, name, attribute, import and string literal (docstrings
    excepted) that names a signature. A file that does not parse is scanned as plain text."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return scan_text(source)
    skip = _docstring_ids(tree)
    out = []
    for node in ast.walk(tree):
        texts: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            texts.append(node.name)
        elif isinstance(node, ast.arg):
            texts.append(node.arg)
        elif isinstance(node, ast.Name):
            texts.append(node.id)
        elif isinstance(node, ast.Attribute):
            texts.append(node.attr)
        elif isinstance(node, ast.alias):
            texts += [node.name, node.asname or ""]
        elif isinstance(node, ast.ImportFrom):
            texts.append(node.module or "")
        elif isinstance(node, ast.keyword):
            texts.append(node.arg or "")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            texts += list(node.names)
        elif isinstance(node, ast.Constant) and id(node) not in skip:
            if isinstance(node.value, str):
                texts.append(node.value)
            elif isinstance(node.value, bytes):
                texts.append(node.value.decode("latin-1"))
        for t in texts:
            for term in _hits(t):
                out.append((getattr(node, "lineno", 0), term, t[:80]))
    return out


def scan_text(source: str) -> list[tuple[int, str, str]]:
    return [(n, term, line.strip()[:80]) for n, line in enumerate(source.splitlines(), 1) for term in _hits(line)]


def _repository_files() -> list[Path]:
    files = []
    for path in REPO.rglob("*"):
        rel = path.relative_to(REPO)
        if not path.is_file() or rel.parts[0] == "tests" or SKIP_DIRS.intersection(rel.parts[:-1]):
            continue
        if path.suffix == ".py" or path.suffix in SCRIPT_SUFFIXES:
            files.append(path)
    return files


def test_rule3_no_signature_code():
    # The scanner itself must work, or an empty result would mean nothing.
    must_flag = ["def gen_x_bogus(url): pass", "params['X-Bogus'] = sign(q)", "from abogus import ABogus",
                 "headers = {'x-argus': a, 'X-Gorgon': g, 'X-Ladon': l}", "msToken = make()",
                 "def device_register(): pass", "XBogus().sign(u)", "url += '&w_rid=' + h", "class XsCommon: pass",
                 "x = b'x-s-common'", "def a_bogus(p): pass", "q = f'{u}&mstoken={t}'"]
    for src in must_flag:
        assert scan_python(src), src
    never_flag = ['"""Douyin\'s own page computes X-Bogus in the browser; we never do."""\ndef read(): pass',
                  "show_rid = 1\nnew_rid = 2", "ttwid = 'cookie the platform sets'", "# x-bogus\nx = 1",
                  "def f():\n    '''a_bogus is computed by the player, not here'''\n    return 1"]
    for src in never_flag:
        assert not scan_python(src), src
    assert scan_text("function getXBogus(u) { return sign(u) }") and not scan_text("const showRid = 1")

    files = _repository_files()
    assert any(p.relative_to(REPO).parts[0] == "readproof" for p in files)
    hits = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        found = scan_python(text) if path.suffix == ".py" else scan_text(text)
        hits += [f"{path.relative_to(REPO)}:{line}: {term!r} in {snippet!r}" for line, term, snippet in found]
    assert not hits, "platform signature code must not exist (DESIGN.md §1 rule 3):\n" + "\n".join(hits)


# --- 4 ------------------------------------------------------------------------------------------------------

def test_rule4_no_identity_change_after_refusal(monkeypatch, sleeps):
    for code in (401, 403, 412):  # a refusal is final
        s = Server({"https://example.com/p": (code, "no", {})})
        res = HttpNet(P1, transport=s.transport, resolve=public).fetch("https://example.com/p")
        assert (res.status, res.attempt, len(s.seen)) == (code, 1, 1)
    challenge = (200, "<title>Just a moment...</title><script src='/cdn-cgi/challenge-platform/x'></script>", {})
    s = Server({"https://example.com/c": challenge})
    assert HttpNet(P1, transport=s.transport, resolve=public).fetch("https://example.com/c").attempt == 1
    assert len(s.seen) == 1 and sleeps == []

    for failure in ((429, "", {}), (503, "", {}), httpx.ConnectError("reset")):  # one retry, same identity
        sleeps.clear()
        s = Server({"https://example.com/p": failure})
        net = HttpNet(P1, transport=s.transport, resolve=public)
        res = net.fetch("https://example.com/p")
        assert (res.attempt, len(s.seen)) == (2, 2)
        assert sleeps == [P1.backoff_s]
        assert s.seen[0].headers == s.seen[1].headers  # same identity, nothing added
    assert P0.max_retries == P1.max_retries == P2.max_retries == 1

    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(k, "http://proxy.invalid:3128")
    net = HttpNet(P1, resolve=public)  # real transport: no proxy may be picked up from the environment
    assert net._client.trust_env is False and net._client._mounts == {}
    assert isinstance(net._client._transport, netmod.GuardedTransport)
    net.close()
    with pytest.raises(ValueError):
        HttpNet(P1, transport=Server({}).transport, resolve=public).fetch(
            "https://example.com/p", headers={"X-Forwarded-For": "203.0.113.7"})


# --- 5 ------------------------------------------------------------------------------------------------------

def test_rule5_public_addresses_only():
    dns = {"metadata.example": ["169.254.169.254"], "lan.example": ["192.168.1.20"], "cgnat.example": ["100.64.3.4"],
           "mixed.example": [PUBLIC, "10.0.0.8"], "mcast.example": ["224.0.0.251"], "v6.example": ["fd00::5"]}
    resolve = lambda h: dns.get(h, [PUBLIC])  # noqa: E731
    routes = {"https://example.com/robots.txt": ALLOW_ALL}
    for host in dns:
        routes[f"https://example.com/to-{host}"] = (302, "", {"Location": f"http://{host}/x"})
    s = Server(routes)
    log = MemoryLog()
    gate, net = make_stack(P1, transport=s.transport, resolve=resolve, log=log)
    for host in dns:  # as the first URL ...
        assert net.fetch(f"http://{host}/x").error == "gate_refused:private_address"
        assert gate.check(f"http://{host}/x").reason == "private_address"
        res = net.fetch(f"https://example.com/to-{host}", follow_redirects=True)  # ... and as a redirect hop
        assert (res.status, res.error) == (302, "gate_refused:private_address")
    for literal in ("http://127.0.0.1/", "http://[::1]/", "http://0.0.0.0/", "http://[::ffff:10.0.0.1]/"):
        assert net.fetch(literal).error == "gate_refused:private_address"
    assert {httpx.URL(u).host for u in s.urls()} == {"example.com"}  # no request reached a private address

    # Without a gate HttpNet still checks every hop, and its own transport re-checks at connect time.
    s = Server({"https://example.com/r": (302, "", {"Location": "http://lan.example/"})})
    assert HttpNet(P1, gate=AllowGate(P1), transport=s.transport, resolve=resolve).fetch(
        "https://example.com/r", follow_redirects=True).error == "gate_refused:private_address"
    answers = iter([[PUBLIC], ["127.0.0.1"]])
    rebinding = HttpNet(P1, resolve=lambda h: next(answers))
    assert rebinding.fetch("http://rebind.example/").error == "gate_refused:private_address"
    rebinding.close()


# --- 6 ------------------------------------------------------------------------------------------------------

CANARY = "RP-WALL-CANARY-5c1e9"


def _wall_fetch(kind: str):
    """A stand-in for HttpNet.fetch that answers every read with one kind of wall (robots.txt allows all)."""

    def fetch(self, url, *, method="GET", headers=None, follow_redirects=False, max_bytes=50_000_000,
              purpose="read"):
        hdrs = {"content-type": "text/html; charset=utf-8"}
        final, status = url, 200
        if purpose == "robots":
            body, hdrs = b"User-agent: *\nAllow: /\n", {"content-type": "text/plain"}
        elif kind == "challenge":
            status = 403
            body = ("<html><head><title>Just a moment...</title></head><body>"
                    "<script src='/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1'></script>"
                    f"<p>{CANARY} checking your browser</p></body></html>").encode()
        elif kind == "login":
            login = "https://passport.weibo.com/visitor/visitor?entry=miniblog&url=" + quote(url, safe="")
            if follow_redirects:
                final = login
                body = f"<html><head><title>Sina Visitor System</title></head><body>{CANARY}</body></html>".encode()
            else:
                status, hdrs, body = 302, {"location": login}, b""
        else:  # an app shell: a site title and nothing visible
            body = ("<html><head><title>抖音</title></head><body><div id='root'></div>"
                    f"<script>window.__x='{CANARY}'</script></body></html>").encode()
        body = body[:max_bytes]
        return FetchResult(url=url, final_url=final, status=status, headers=hdrs, bytes=len(body),
                           sha256=sha256_bytes(body), fetched_at=now_iso(), elapsed_ms=1, origin=self.origin,
                           user_agent=self.profile.user_agent, method=method,
                           redirects=[final] if final != url else [], body=body)

    return fetch


def test_rule6_wall_never_reaches_model(tmp_path, monkeypatch):
    try:
        harness = importlib.import_module("readproof.harness")
    except ModuleNotFoundError as e:
        if e.name != "readproof.harness":
            raise
        pytest.skip("readproof/harness.py is not written yet; rule 6 is enforced there "
                    "(Harness.read returns Records only when a wall verdict says served)")
    monkeypatch.setattr(policymod, "default_resolve", public)  # no DNS
    profile = dataclasses.replace(P1, allow_browser=False, allow_embed_media=False, backoff_s=0.0)
    cases = [("challenge", "https://www.bilibili.com/video/BV1xx411c7mD"),
             ("login", "https://weibo.com/1234567890/AbCdEfGhI"),
             ("shell", "https://www.douyin.com/video/7300000000000000000")]
    for kind, url in cases:
        monkeypatch.setattr(HttpNet, "fetch", _wall_fetch(kind))
        root = tmp_path / kind
        result = harness.Harness(profile, root=str(root), origin="test:offline").read(url)
        assert not result.served, (kind, result.wall, result.wall_signal)
        assert result.wall is not None
        assert result.records == []
        for path in root.rglob("records.jsonl"):
            assert CANARY not in path.read_text(encoding="utf-8")


# --- 7 ------------------------------------------------------------------------------------------------------

def test_rule7_robots_and_content_signal_evaluated():
    robots = (200, "User-agent: *\nContent-Signal: search=yes, ai-input=no, ai-train=no\nDisallow: /private\n", {})
    for profile in (P0, P1, P2):
        s = Server({"https://example.com/robots.txt": robots, "https://example.com/public": (200, "pub", {}),
                    "https://example.com/private/1": (200, "priv", {})})
        log = MemoryLog()
        _, net = make_stack(profile, transport=s.transport, resolve=public, log=log)
        r_public = net.fetch("https://example.com/public")
        r_private = net.fetch("https://example.com/private/1")

        decisions = [e for e in log.of("gate") if e["purpose"] != "robots"]
        assert [e["url"] for e in decisions] == ["https://example.com/public", "https://example.com/private/1"]
        for e in decisions:  # evaluated and logged every time
            assert e["robots_url"] == "https://example.com/robots.txt" and e["robots_status"] == "ok"
            assert e["content_signal"] == {"search": "yes", "ai-input": "no", "ai-train": "no"}
            assert e["robots_binding"] is profile.obey_robots
        assert decisions[1]["matched_rule"] == "Disallow: /private" and decisions[1]["robots_allowed"] is False
        for f in (e for e in log.events if e["event"] == "fetch" and e["purpose"] != "robots"):
            before = log.events[:log.events.index(f)]
            assert any(g["event"] == "gate" and g["url"] == f["final_url"] for g in before)

        if profile is P0:  # binding
            assert r_public.error == "gate_refused:content_signal"
            assert r_private.error == "gate_refused:robots_disallow"
            assert s.urls() == ["https://example.com/robots.txt"]
        else:  # recorded, not binding
            assert (r_public.status, r_private.status) == (200, 200)
            assert s.urls() == ["https://example.com/robots.txt", "https://example.com/public",
                                "https://example.com/private/1"]


# --- 8 ------------------------------------------------------------------------------------------------------

def test_rule8_linkedin_denied():
    urls = ["https://www.linkedin.com/posts/someone_activity-7000000000000000000-abcd",
            "https://linkedin.com/feed/update/urn:li:activity:7000000000000000000/",
            "http://uk.LinkedIn.com/in/someone", "https://www.linkedin.com./company/x"]
    for profile in (P0, P1, P2):
        asked: list[str] = []
        s = Server({})
        log = MemoryLog()
        gate, net = make_stack(profile, transport=s.transport, resolve=lambda h: asked.append(h) or [PUBLIC], log=log)
        for url in urls:
            d = gate.check(url)
            assert (d.allowed, d.reason, d.profile) == (False, "terms", profile.name)
            assert net.fetch(url).error == "gate_refused:terms"
        assert s.seen == [] and asked == []  # no request, no robots.txt, not even a DNS lookup

        # As a redirect hop, and with the gate's own deny list emptied: HttpNet still refuses.
        s = Server({"https://example.com/robots.txt": ALLOW_ALL,
                    "https://example.com/go": (302, "", {"Location": urls[0]})})
        _, net = make_stack(profile, transport=s.transport, resolve=public, deny={})
        res = net.fetch("https://example.com/go", follow_redirects=True)
        assert res.error == "gate_refused:terms" and all("linkedin" not in u for u in s.urls())
        assert HttpNet(profile, gate=PolicyGate(profile, deny={}, resolve=public), transport=s.transport,
                       resolve=public).fetch(urls[1]).error == "gate_refused:terms"
