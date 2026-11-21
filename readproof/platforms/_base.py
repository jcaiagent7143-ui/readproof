"""Network-free helpers every platform spec shares.

Why one module: every platform answers the same questions — is this host mine, which link form is this
URL, what evidence goes into a wall signal — and the answers must be computed the same way everywhere,
or Track A compares link forms that were parsed by different rules.

Nothing here opens a connection, reads a cookie or computes a request signature (DESIGN §1 rules 2–3).
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

from ..types import FetchResult, PostRef, WallType, WallVerdict, sha256_bytes
from ..walls import evidence, verdict, visible_text

__all__ = [
    "Page", "Spec", "body_text", "evidence", "flip_case_once", "flip_short_link", "header", "host_of",
    "json_of", "landing", "on_domain", "page_of", "param", "path_of", "query", "raw_param", "script_json",
    "split", "verdict", "bytedance_challenge",
]


# --- URLs ----------------------------------------------------------------------------------------------------


def split(url: str):
    """urlsplit that never raises (a malformed port or bracket is simply not a URL we recognise)."""
    try:
        parts = urlsplit((url or "").strip())
        parts.port  # noqa: B018 - raises on a malformed port
        return parts
    except ValueError:
        return urlsplit("")


def host_of(url: str) -> str:
    return (split(url).hostname or "").lower().rstrip(".")


def path_of(url: str) -> str:
    return split(url).path or ""


def on_domain(host: str, *domains: str) -> bool:
    """host is one of domains or a subdomain of one (never a look-alike: notxhslink.cn is not xhslink.cn)."""
    return any(host == d or host.endswith("." + d) for d in domains)


def query(url: str) -> dict[str, list[str]]:
    """Decoded query parameters. Not for tokens: parse_qs turns a base64 '+' into a space."""
    return parse_qs(split(url).query, keep_blank_values=True)


def param(url: str, *names: str) -> str | None:
    """The first non-empty decoded value of any of `names`."""
    q = query(url)
    for name in names:
        for value in q.get(name, []):
            if value.strip():
                return value.strip()
    return None


def raw_param(url: str, name: str) -> str | None:
    """A query value exactly as written, for values a platform compares byte for byte (xsec_token)."""
    m = re.search(r"[?&]" + re.escape(name) + r"=([^&#]*)", url or "")
    return m.group(1) if m else None


def landing(fetch: FetchResult) -> str:
    """Where a fetch ended: the Location of an unfollowed redirect, else its final URL."""
    final = fetch.final_url or fetch.url
    location = header(fetch, "location")
    if fetch.status is not None and 300 <= fetch.status < 400 and location:
        return urljoin(final, location.strip())
    return final


# --- the share-button control (DESIGN §6) ----------------------------------------------------------------------


def flip_case_once(code: str) -> str | None:
    """`code` with its first ASCII letter's case flipped, or None when it has no letter.

    Short codes are case-sensitive (LinkDigest, 2026-11-12: a known-good v.douyin.com code with one
    letter's case changed lands on the home page, exactly like a dead link)."""
    for i, ch in enumerate(code):
        if ch.isascii() and ch.isalpha():
            return code[:i] + ch.swapcase() + code[i + 1:]
    return None


def flip_short_link(url: str) -> str | None:
    """The same short link with one letter of its code (the last path segment) case-flipped."""
    parts = split(url)
    segments = parts.path.split("/")
    for i in range(len(segments) - 1, -1, -1):
        if segments[i]:
            flipped = flip_case_once(segments[i])
            if flipped is None:
                return None
            segments[i] = flipped
            return urlunsplit((parts.scheme, parts.netloc, "/".join(segments), parts.query, parts.fragment))
    return None


# --- bodies ----------------------------------------------------------------------------------------------------


def header(fetch: FetchResult, name: str) -> str:
    name = name.lower()
    for key, value in (fetch.headers or {}).items():
        if key.lower() == name:
            return value or ""
    return ""


_CHARSET = re.compile(r"charset=[\"']?([A-Za-z0-9_\-]+)", re.I)


def body_text(fetch: FetchResult) -> str:
    """The body as text: charset from Content-Type, else the page's <meta charset>, else UTF-8."""
    body = fetch.body or b""
    if not body:
        return ""
    m = _CHARSET.search(header(fetch, "content-type")) or _CHARSET.search(body[:4096].decode("ascii", "ignore"))
    charset = m.group(1) if m else "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class Page:
    html: str
    title: str
    text: str


_PAGES: "OrderedDict[tuple[str, str], Page]" = OrderedDict()


def page_of(fetch: FetchResult) -> Page:
    """The decoded body and its visible text, parsed once per snapshot (walls.judge and spec.judge share it)."""
    if not fetch.body:
        return Page("", "", "")
    key = (fetch.sha256 or sha256_bytes(fetch.body), header(fetch, "content-type"))
    page = _PAGES.get(key)
    if page is None:
        html = body_text(fetch)
        title, text = visible_text(html)
        page = Page(html, title, text)
        _PAGES[key] = page
        while len(_PAGES) > 16:
            _PAGES.popitem(last=False)
    return page


def json_of(fetch: FetchResult) -> object | None:
    """The body as JSON when it is JSON (API answers, oEmbed), else None."""
    body = (fetch.body or b"").lstrip()
    if not body or body[:1] not in (b"{", b"[") or len(body) > 5_000_000:
        return None
    try:
        return json.loads(body_text(fetch))
    except ValueError:
        return None


def script_json(html: str, script_id: str) -> object | None:
    """The JSON inside <script id="script_id">…</script> (TikTok's rehydration data), else None."""
    m = re.search(r"<script[^>]*\bid=[\"']" + re.escape(script_id) + r"[\"'][^>]*>(.*?)</script>", html or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


# --- the ByteDance verification page ---------------------------------------------------------------------------

# Live 2026-11-21 (ReadProof UA, residential MY): www.douyin.com/video/<id> and www.toutiao.com/article/<id>/
# both answered HTTP 200 with the same 72,914-byte page (sha256 9b56a6ac…): an empty <body></body>, an
# obfuscated VM script and a script that runs ByteDance's crawler check and reloads. It has no visible
# character at all, so the generic "empty" rule would also catch it; this names it.
_BYTEDANCE_MARKERS = ("byted_acrawler", "_$jsvmprt")
_EMPTY_BODY = re.compile(r"<body[^>]*>\s*</body>", re.I)


def bytedance_challenge(fetch: FetchResult, html: str, visible_chars: int, platform: str) -> WallVerdict | None:
    if visible_chars:
        return None
    marker = next((m for m in _BYTEDANCE_MARKERS if m in html), None)
    if marker:
        return verdict(WallType.CHALLENGE, f"{platform}:bytedance-verify", fetch, 0, marker=marker)
    if fetch.bytes >= 40_000 and _EMPTY_BODY.search(html):
        return verdict(WallType.CHALLENGE, f"{platform}:bytedance-verify", fetch, 0, marker="empty-body-large-script")
    return None


# --- the spec base ---------------------------------------------------------------------------------------------


class Spec:
    """Shared behaviour of a PlatformSpec (readproof.interfaces.PlatformSpec).

    Subclasses set `name`, `domains` and `short_hosts`, and implement parse/canonical/forms; embed_url,
    oembed_url and judge default to "none" / "no opinion".
    """

    name: str = ""
    domains: tuple[str, ...] = ()
    short_hosts: tuple[str, ...] = ()  # hosts whose every path is a short code that must be expanded

    def match(self, url: str) -> bool:
        parts = split(url)
        if parts.scheme.lower() not in ("http", "https", ""):
            return False
        host = (parts.hostname or "").lower().rstrip(".")
        return bool(host) and on_domain(host, *self.domains, *self.short_hosts)

    def is_short(self, url: str) -> bool:
        """A share short link: no post id until its redirect is read."""
        host = host_of(url)
        return host in self.short_hosts or any(host.endswith("." + h) for h in self.short_hosts)

    def page_kind(self, url: str) -> str | None:
        """What a non-post address on this platform is ("home", "profile", …), for resolve's verdict."""
        path = path_of(url)
        return "home" if path in ("", "/") else None

    def parse(self, url: str) -> PostRef | None:  # pragma: no cover - every platform overrides
        raise NotImplementedError

    def canonical(self, ref: PostRef) -> str:  # pragma: no cover - every platform overrides
        raise NotImplementedError

    def forms(self, ref: PostRef, **known: str) -> list[tuple[str, str]]:  # pragma: no cover
        raise NotImplementedError

    def embed_url(self, ref: PostRef) -> str | None:
        return None

    def oembed_url(self, url: str) -> str | None:
        return None

    def judge(self, fetch: FetchResult, visible_text: str) -> WallVerdict | None:
        return None

    # helpers for subclasses

    def ref(self, post_id: str, form: str, url: str, **extra: object) -> PostRef:
        return PostRef(platform=self.name, post_id=post_id, form=form, url=url,
                       extra={k: v for k, v in extra.items() if v is not None})

    @staticmethod
    def with_short(out: list[tuple[str, str]], known: dict[str, str], share_text: str) -> list[tuple[str, str]]:
        """Append the short link and its case-flipped control (only when the share button gave one), then the
        share text. A short link is never invented: an invented code is a dead link, not a link form."""
        short = (known.get("short") or "").strip()
        if short:
            out.append(("short", short))
            flipped = flip_short_link(short)
            if flipped and flipped != short:
                out.append(("short_case_flipped", flipped))
        out.append(("share_text", share_text))
        return out


def unquote_once(value: str | None) -> str | None:
    """Percent-decode exactly once: a token double-encoded by a login interstitial (%253D) becomes literal."""
    return unquote(value) if value is not None else None
