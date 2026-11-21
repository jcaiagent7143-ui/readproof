"""HttpNet: the only code that opens a network connection (DESIGN.md §1 rules 1, 2, 4, 5, 7, 8).

Each rule is enforced here in code, not left to callers:
- Rule 1, declared identity: the client's only User-Agent is profile.user_agent; a caller-supplied User-Agent
  (or any other identity or address header, see FORBIDDEN_HEADERS) is a ValueError; a request hook raises
  IdentityError if anything else is about to leave.
- Rule 2, no cookies: the cookie jar refuses every cookie and is cleared after every response; the request
  hook raises if a Cookie header is present. A URL with user:password@ is refused ("credentials").
- Rule 4, no identity or address change: trust_env=False (environment proxies, SSL_CERT_* and .netrc are
  ignored), no proxy is ever configured, at most profile.max_retries retries per fetch() call on the same
  client, after profile.backoff_s, and only after 429, 5xx or a transport error — never after 401/403/412.
- Rule 5, public addresses only: every hop is resolved and refused if any address is not public; the
  default transport also re-checks at connect time and connects to the checked address itself, so DNS cannot
  change between the check and the connection.
- Rules 7 and 8: with a gate, every request — the first URL and every redirect hop — is passed to
  gate.check() before it is sent; the LinkedIn deny list applies even without a gate.

Error strings (FetchResult.error): "gate_refused:<reason>" (nothing sent for that URL), "<HttpxError>: …"
for transport failures (status None), "too_many_redirects:8", "invalid_redirect". attempt=0 means nothing
was sent at all.
"""

from __future__ import annotations

import email.utils
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any, Callable

import httpcore
import httpx

from . import policy
from .interfaces import Gate, Log
from .robots import MAX_BYTES as ROBOTS_MAX_BYTES
from .robots import RobotsCache
from .types import FetchResult, Profile, sha256_bytes

MAX_HOPS = 8
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
NO_RETRY_STATUSES = frozenset({401, 403, 412})  # refusals: retrying them is changing nothing but the count
MAX_RETRY_AFTER_S = 60.0  # a server asking for a longer wait gets no retry at all

# Headers that carry identity or address. Only HttpNet sets identity; nobody forwards for anybody.
FORBIDDEN_HEADERS = frozenset({
    "user-agent", "cookie", "cookie2", "host", "forwarded", "via", "x-forwarded-for", "x-forwarded-host",
    "x-real-ip", "true-client-ip", "client-ip", "x-client-ip", "x-originating-ip", "cf-connecting-ip",
    "x-requested-with",
})
FORBIDDEN_PREFIXES = ("sec-ch-ua",)  # client hints claim a browser brand and platform
REDACTED_HEADERS = frozenset({"authorization", "proxy-authorization", "x-api-key", "api-key"})

_sleep: Callable[[float], None] = time.sleep  # tests replace this to observe the back-off


class IdentityError(RuntimeError):
    """A request was about to leave with an identity other than the declared one. Always a bug."""


class AddressRefused(Exception):
    """Raised by the connect-time guard. Deliberately not an httpx/httpcore error, so it is never retried."""


class _RefuseAllCookies(DefaultCookiePolicy):
    def set_ok(self, cookie: Any, request: Any) -> bool:
        return False

    def return_ok(self, cookie: Any, request: Any) -> bool:
        return False


class _GuardedBackend(httpcore.SyncBackend):
    """Resolves, judges and connects to the judged address itself (defeats DNS rebinding)."""

    def __init__(self, resolve: Callable[[str], list[str]]) -> None:
        self._resolve = resolve

    def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address: str | None = None,
                    socket_options: Any = None) -> httpcore.NetworkStream:
        chk = policy.check_addresses(host, self._resolve)
        if chk.reason:
            raise AddressRefused(f"{host}: {'; '.join(chk.problems)}")
        if not chk.addresses:
            raise httpcore.ConnectError(f"cannot resolve {host}: {chk.error}")
        last: Exception | None = None
        for ip in chk.addresses:
            try:
                return super().connect_tcp(ip, port, timeout=timeout, local_address=local_address,
                                           socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                last = e
        assert last is not None
        raise last


class GuardedTransport(httpx.HTTPTransport):
    """httpx's transport with the connect-time address guard, no proxy, no environment, no internal retries."""

    def __init__(self, resolve: Callable[[str], list[str]]) -> None:
        super().__init__(trust_env=False, retries=0)
        self._pool.close()
        limits = httpx.Limits()
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=True, http2=False, retries=0,
            network_backend=_GuardedBackend(resolve),
        )


def check_caller_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """Caller headers minus nothing: identity and address headers are refused outright (ValueError)."""
    out: dict[str, str] = {}
    for k, v in (headers or {}).items():
        lk = str(k).strip().lower()
        if lk in FORBIDDEN_HEADERS or lk.startswith(FORBIDDEN_PREFIXES):
            raise ValueError(f"header {k!r} may not be set by callers: HttpNet declares identity itself (DESIGN.md §1)")
        out[str(k)] = v
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _redacted(headers: dict[str, str]) -> dict[str, str]:
    return {k: ("<redacted>" if k.lower() in REDACTED_HEADERS else v) for k, v in headers.items()}


def _retry_after(headers: dict[str, str]) -> float | None:
    v = (headers.get("retry-after") or "").strip()
    if not v:
        return None
    if v.isdigit():
        return float(v)
    try:
        when = email.utils.parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _redirect_method(method: str, status: int | None) -> str:
    if status in (302, 303) and method != "HEAD":
        return "GET"
    if status == 301 and method == "POST":
        return "GET"
    return method


def _same_origin(a: str, b: str) -> bool:
    ua, ub = httpx.URL(a), httpx.URL(b)
    return (ua.scheme, ua.raw_host, ua.port) == (ub.scheme, ub.raw_host, ub.port)


@dataclass
class _Call:
    url: str
    method: str
    purpose: str
    max_bytes: int
    started: float = field(default_factory=time.monotonic)
    redirects: list[str] = field(default_factory=list)


class HttpNet:
    """See the module docstring. One instance = one identity, one client, one origin label."""

    def __init__(self, profile: Profile, *, origin: str = "residential:unknown", gate: Gate | None = None,
                 timeout: float = 20.0, transport: httpx.BaseTransport | None = None, log: Log | None = None,
                 resolve: Callable[[str], list[str]] | None = None) -> None:
        self.profile = profile
        self.origin = origin
        self.gate = gate
        self.log = log
        # Resolver precedence: explicit > the gate's (so gate and net judge the same answers) > system DNS.
        self.resolve: Callable[[str], list[str]] = resolve or getattr(gate, "resolve", None) or policy.default_resolve
        gate_deny = getattr(gate, "deny", None)
        self.deny = {**policy.DEFAULT_DENY, **(gate_deny if isinstance(gate_deny, dict) else {})}
        self._client = httpx.Client(
            headers={"User-Agent": profile.user_agent},
            cookies=CookieJar(policy=_RefuseAllCookies()),
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=transport if transport is not None else GuardedTransport(self.resolve),
            event_hooks={"request": [self._on_request], "response": [self._on_response]},
        )
        self._sent_headers: dict[str, str] = {}

    # -- enforcement hooks (run inside httpx for every request it sends) -------------------------------------

    def _on_request(self, request: httpx.Request) -> None:
        if request.headers.get_list("user-agent") != [self.profile.user_agent]:
            raise IdentityError(f"refusing to send User-Agent {request.headers.get_list('user-agent')!r}")
        if "cookie" in request.headers or "cookie2" in request.headers:
            raise IdentityError("refusing to send a Cookie header (DESIGN.md §1 rule 2)")
        self._sent_headers = dict(request.headers)

    def _on_response(self, response: httpx.Response) -> None:
        self._client.cookies.clear()

    # -- public API ----------------------------------------------------------------------------------------

    def fetch(self, url: str, *, method: str = "GET", headers: dict[str, str] | None = None,
              follow_redirects: bool = False, max_bytes: int = 50_000_000, purpose: str = "read") -> FetchResult:
        """Fetch one URL. Never raises for network trouble or refusals: they come back as FetchResult.error.

        Raises ValueError for forbidden caller headers and IdentityError if a request would leave with
        another identity (a bug). The returned result is the last request's: url is what the caller asked,
        final_url the last URL requested, redirects every URL followed, elapsed_ms the whole call."""
        extra = check_caller_headers(headers)
        call = _Call(url=url, method=method.upper(), purpose=purpose, max_bytes=max(0, int(max_bytes)))
        retries_left = max(0, int(self.profile.max_retries))
        current = url
        last: FetchResult | None = None
        for hop in range(MAX_HOPS + 1):
            refusal = self._refusal(current, purpose, hop)
            if refusal is not None:
                if last is None:
                    return self._result(call, current, None, {}, b"", 0, error=f"gate_refused:{refusal}")
                return replace(last, error=f"gate_refused:{refusal}")
            if last is not None and not _same_origin(url, current):
                extra = {k: v for k, v in extra.items() if k.lower() not in REDACTED_HEADERS}
            attempt = 1
            while True:
                res, retryable = self._send(call, current, extra, attempt, hop)
                wait = self._retry_wait(res) if retryable else None
                if wait is None or retries_left <= 0:
                    break
                retries_left -= 1
                _sleep(wait)
                attempt += 1
            if res.error is not None and res.error.startswith("gate_refused:"):
                return res if last is None else replace(last, error=res.error)
            last = res
            if res.status is None or res.status not in REDIRECT_STATUSES or not follow_redirects:
                return res
            location = res.headers.get("location")
            if not location:
                return res
            if hop == MAX_HOPS:
                return replace(res, error=f"too_many_redirects:{MAX_HOPS}")
            try:
                nxt = str(httpx.URL(current).join(location))
            except (httpx.InvalidURL, ValueError):
                return replace(res, error="invalid_redirect")
            call.redirects.append(nxt)
            call.method = _redirect_method(call.method, res.status)
            current = nxt
        assert last is not None  # the loop always returns
        return last

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpNet":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- internals -----------------------------------------------------------------------------------------

    def _emit(self, event: str, **fields: Any) -> None:
        if self.log is not None:
            self.log.emit(event, **fields)

    def _refusal(self, url: str, purpose: str, hop: int) -> str | None:
        """Policy refusal for one URL, or None. The deny list comes first (it holds even if a gate's list was
        emptied, and a denied host never gets as far as a robots.txt lookup); then the gate, whose decision
        is logged; then the URL checks a gate-less net still needs."""
        u, bad = policy.parse_target(url)
        reason = policy.denied(policy.host_of(u), self.deny) if u is not None else None
        by = "net"
        if reason is None and self.gate is not None:
            d = self.gate.check(url, purpose=purpose)
            if not d.allowed:
                reason, by = d.reason, "gate"
        if reason is None:
            reason = bad
        if reason is not None:
            self._emit("fetch_refused", url=url, reason=reason, by=by, hop=hop, purpose=purpose)
        return reason

    def _result(self, call: _Call, url: str, status: int | None, headers: dict[str, str], body: bytes,
                attempt: int, error: str | None = None, fetched_at: str | None = None) -> FetchResult:
        return FetchResult(
            url=call.url, final_url=url, status=status, headers=headers, bytes=len(body),
            sha256=sha256_bytes(body), fetched_at=fetched_at or _now(),
            elapsed_ms=int((time.monotonic() - call.started) * 1000), origin=self.origin,
            user_agent=self.profile.user_agent, method=call.method, redirects=list(call.redirects),
            error=error, attempt=attempt, body=body,
        )

    def _send(self, call: _Call, url: str, headers: dict[str, str], attempt: int, hop: int) -> tuple[FetchResult, bool]:
        """One request on the wire. Returns (result, retryable-by-kind)."""
        fetched_at = _now()
        t0 = time.monotonic()
        host = policy.host_of(httpx.URL(url))
        chk = policy.check_addresses(host, self.resolve)
        if chk.reason:
            self._emit("fetch_refused", url=url, reason=chk.reason, by="net", problems=chk.problems, hop=hop,
                       purpose=call.purpose)
            return self._result(call, url, None, {}, b"", 0, f"gate_refused:{chk.reason}", fetched_at), False
        status: int | None = None
        resp_headers: dict[str, str] = {}
        body = bytearray()
        truncated = False
        error: str | None = None
        retryable = False
        self._sent_headers = {}
        if not chk.addresses:
            error, retryable = f"ConnectError: cannot resolve {host}: {chk.error}", True
        else:
            # httpx copies the client's cookies into a fresh jar (default policy) when it builds a request, so
            # the refuse-all policy only stops extraction; anything placed in the jar is dropped here.
            self._client.cookies.clear()
            try:
                with self._client.stream(call.method, url, headers=headers, follow_redirects=False) as resp:
                    status = resp.status_code
                    resp_headers = dict(resp.headers)
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) > call.max_bytes:
                            truncated = True
                            del body[call.max_bytes:]
                            break
            except AddressRefused as e:
                self._emit("fetch_refused", url=url, reason="private_address", by="connect", problems=[str(e)],
                           hop=hop, purpose=call.purpose)
                return self._result(call, url, None, {}, b"", 0, "gate_refused:private_address", fetched_at), False
            except httpx.TransportError as e:
                status, resp_headers, body = None, {}, bytearray()
                error, retryable = f"{type(e).__name__}: {e}", True
            except httpx.HTTPError as e:  # e.g. DecodingError: the server answered; not a transport failure
                status, body = None, bytearray()
                error = f"{type(e).__name__}: {e}"
            finally:
                self._client.cookies.clear()
        if status is not None and (status == 429 or 500 <= status <= 599):
            retryable = True
        res = self._result(call, url, status, resp_headers, bytes(body), attempt, error, fetched_at)
        self._emit("fetch", **res.to_log(), hop=hop, purpose=call.purpose, truncated=truncated,
                   request_ms=int((time.monotonic() - t0) * 1000), request_headers=_redacted(self._sent_headers))
        return res, retryable

    def _retry_wait(self, res: FetchResult) -> float | None:
        """Seconds to wait before the one permitted retry, or None for no retry."""
        if res.status in NO_RETRY_STATUSES:
            return None
        wait = float(self.profile.backoff_s)
        if res.status is not None:
            asked = _retry_after(res.headers)
            if asked is not None:
                if asked > MAX_RETRY_AFTER_S:
                    return None
                wait = max(wait, asked)
        return wait


def make_stack(profile: Profile, *, origin: str = "residential:unknown", log: Log | None = None,
               transport: httpx.BaseTransport | None = None, resolve: Callable[[str], list[str]] | None = None,
               deny: dict[str, str] = policy.DEFAULT_DENY, robots_ttl_s: int = 86400,
               timeout: float = 20.0) -> tuple[policy.PolicyGate, HttpNet]:
    """Gate + robots cache + net, wired the one correct way: robots.txt is fetched through the same HttpNet
    (declared identity, no cookies, SSRF guard, logged) with purpose="robots", so the gate never recurses,
    and every other request the net sends is gated (robots evaluated) first."""
    holder: dict[str, HttpNet] = {}
    robots = RobotsCache(lambda u: holder["net"].fetch(u, purpose="robots", max_bytes=ROBOTS_MAX_BYTES),
                         ttl_s=robots_ttl_s)
    gate = policy.PolicyGate(profile, robots=robots, deny=deny, resolve=resolve, log=log)
    net = HttpNet(profile, origin=origin, gate=gate, timeout=timeout, transport=transport, log=log)
    holder["net"] = net
    return gate, net
