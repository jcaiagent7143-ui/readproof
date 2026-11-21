"""Plumbing every reader shares: ask the Gate, fetch, store the body, judge the wall, build Records.

Why one module: each of the four readers must (1) ask the Gate before it sends anything (rule 7 logs the
decision), (2) put every body it fetched into the store by SHA-256 (DESIGN §4.7), (3) let the wall judge
decide served/not-served before a single Record exists (rule 6), and (4) build Records that name the snapshot
they came from. Doing those four steps in one place is what makes "no Record without a served verdict" a
property of the code rather than of each reader's care.

The wall judge belongs to the platform builder (`readproof.walls.judge`). It is imported lazily so the readers
import cleanly before walls.py exists. Only while that module is absent, `judge()` falls back to the generic
rules of DESIGN §5 (and the platform's own `spec.judge` first); every verdict says which judge produced it, so a
log line can never pass a fallback verdict off as the real one.
"""

from __future__ import annotations

import html as html_lib
import importlib
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urlparse

from ..interfaces import Context, PlatformSpec, ReaderOutput
from ..types import Channel, FetchResult, GateDecision, PostRef, Record, WallType, WallVerdict

SpecFor = Callable[[PostRef], "PlatformSpec | None"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- platform spec lookup ----------------------------------------------------------------------------------


def default_spec_for(ref: PostRef) -> PlatformSpec | None:
    """The platform builder's spec for this post, or None (generic web, or the registry is not installed yet)."""
    try:
        platforms = importlib.import_module("readproof.platforms")
    except ModuleNotFoundError:
        return None
    by_name = getattr(platforms, "by_name", None)
    if by_name is None:
        return None
    try:
        return by_name(ref.platform)
    except (KeyError, LookupError, ValueError):
        return None


def target_url(ref: PostRef, spec: PlatformSpec | None) -> str:
    """The URL a reader reads: the spec's canonical form (built without a fetch), else the ref's own URL."""
    if spec is not None:
        try:
            url = spec.canonical(ref)
        except (KeyError, ValueError):
            url = ""
        if url:
            return url
    return ref.url


# --- gate + fetch + store ----------------------------------------------------------------------------------


def refused(reader: str, decision: GateDecision) -> ReaderOutput:
    """A Gate refusal: no request was sent, so there is no fetch and nothing to judge."""
    signal = f"gate refused ({decision.reason})"
    if decision.matched_rule:
        signal += f" rule={decision.matched_rule!r}"
    return ReaderOutput(reader=reader, verdict=WallVerdict(False, WallType.POLICY, f"{signal} {decision.url}"), gate=[decision])


def gated_fetch(ctx: Context, url: str, *, purpose: str = "read", headers: dict[str, str] | None = None,
                follow_redirects: bool = True, max_bytes: int = 50_000_000) -> tuple[GateDecision, FetchResult | None]:
    """Ask the Gate, then fetch through ctx.net. The fetch is None when the Gate refused (nothing was sent)."""
    decision = ctx.gate.check(url, purpose=purpose)
    if not decision.allowed:
        return decision, None
    fetch = ctx.net.fetch(url, headers=headers, follow_redirects=follow_redirects, max_bytes=max_bytes, purpose=purpose)
    return decision, fetch


def header(fetch: FetchResult | None, name: str) -> str:
    """Case-insensitive header lookup (httpx lowercases names, test fixtures may not)."""
    if fetch is None:
        return ""
    want = name.lower()
    for k, v in (fetch.headers or {}).items():
        if k.lower() == want:
            return v
    return ""


def store_fetch(ctx: Context, fetch: FetchResult | None) -> str | None:
    """Every body goes to the store (DESIGN §4.7); returns its sha256 (the snapshot id Records name)."""
    if fetch is None or fetch.body is None:
        return None
    return ctx.store.put(fetch.body, content_type=header(fetch, "content-type"), source_url=fetch.final_url or fetch.url)


_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([A-Za-z0-9_\-]+)""", re.I)


def decode_body(fetch: FetchResult) -> str:
    """The body as text: the header's charset, else a <meta charset>, else UTF-8 (lossy, never raises)."""
    body = fetch.body or b""
    m = re.search(r"charset=([A-Za-z0-9_\-]+)", header(fetch, "content-type"), re.I)
    charset = m.group(1) if m else None
    if not charset:
        mm = _CHARSET.search(body[:4096])
        charset = mm.group(1).decode("ascii", "ignore") if mm else "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


# --- the wall judge ----------------------------------------------------------------------------------------


def _walls_module():
    try:
        return importlib.import_module("readproof.walls")
    except ModuleNotFoundError as exc:
        if exc.name != "readproof.walls":
            raise  # walls.py exists but one of its own imports is missing: that is a bug to see, not to hide
        return None


def judge(fetch: FetchResult, spec: PlatformSpec | None = None) -> tuple[WallVerdict, str]:
    """(verdict, judge name). The platform builder's walls.judge when installed, else the generic fallback."""
    walls = _walls_module()
    if walls is not None:
        return walls.judge(fetch, spec), "walls"
    return fallback_judge(fetch, spec), "fallback"


def visible_text(html: str) -> tuple[str, str]:
    """(title, visible text): walls.visible_text when installed, else the scanner below."""
    walls = _walls_module()
    if walls is not None:
        return walls.visible_text(html)
    scan = scan_html(html)
    return scan.title, scan.text


_CHALLENGE_MARKERS = (
    "captcha", "cf-chl", "challenge-platform", "_____tmd_____", "verify.snssdk.com", "verifycenter",
    "Sina Visitor System", "/website-login/captcha", "acw_sc__v2", "安全验证", "验证码", "环境异常",
)
_LOGIN_PATH = re.compile(r"/(login|signin|sign-in|passport|accounts/login|website-login)\b", re.I)


def fallback_judge(fetch: FetchResult, spec: PlatformSpec | None = None) -> WallVerdict:
    """DESIGN §5's generic rules, platform rules first. Used only while readproof/walls.py is not installed.

    Conservative on purpose: anything it cannot place is not served (UNKNOWN), because a wrong "served" turns a
    wall into page text, and a wrong "not served" only costs one more reader in the chain.
    """
    if fetch.error:
        return WallVerdict(False, WallType.NETWORK, f"transport error: {fetch.error}")
    html = decode_body(fetch) if fetch.body else ""
    scan = scan_html(html) if html else HtmlScan()
    text = scan.text
    if spec is not None:
        try:
            v = spec.judge(fetch, text)
        except Exception:  # a platform rule that crashes must not decide the verdict
            v = None
        if v is not None:
            return v
    status = fetch.status or 0
    chars = len(text)
    if status == 429:
        return WallVerdict(False, WallType.RATE_LIMITED, "http 429", chars)
    if status in (404, 410):
        return WallVerdict(False, WallType.DELETED, f"http {status}", chars)
    if status == 401:
        return WallVerdict(False, WallType.LOGIN, "http 401", chars)
    if 300 <= status < 400:
        loc = header(fetch, "location")
        if _LOGIN_PATH.search(loc):
            return WallVerdict(False, WallType.LOGIN, f"redirect to login {loc[:120]}", chars)
        return WallVerdict(False, WallType.UNKNOWN, f"http {status} redirect not followed {loc[:120]}", chars)
    if status >= 400 or status == 0:
        return WallVerdict(False, WallType.UNKNOWN, f"http {status or 'none'}", chars)
    final = urlparse(fetch.final_url or fetch.url)
    if _LOGIN_PATH.search(final.path):
        return WallVerdict(False, WallType.LOGIN, f"landed on login path {final.path[:80]}", chars)
    start = urlparse(fetch.url)
    if final.path in ("", "/") and start.path not in ("", "/") and fetch.redirects:
        return WallVerdict(False, WallType.SOFT_404, f"redirected to site root {final.netloc}", chars)
    low = html[:200_000].lower()
    for marker in _CHALLENGE_MARKERS:
        if marker.lower() in low and chars < 400:
            return WallVerdict(False, WallType.CHALLENGE, f"challenge marker {marker!r} visible_chars={chars}", chars)
    if chars < 40 and not scan.title.strip():
        return WallVerdict(False, WallType.EMPTY, f"visible_chars={chars} no title bytes={fetch.bytes}", chars)
    return WallVerdict(True, None, f"served visible_chars={chars} bytes={fetch.bytes}", chars)


# --- Records -----------------------------------------------------------------------------------------------


_WS_LINE = re.compile(r"[ \t 　]+")


def clean_text(text: str) -> str:
    """Normalise whitespace the way a reader sees it: CRLF -> LF, runs of spaces -> one, at most one blank line."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("​", "").replace("﻿", "")
    lines = [_WS_LINE.sub(" ", ln).strip() for ln in text.split("\n")]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return out.strip()


def make_record(ref: PostRef, channel: Channel, locator: dict[str, Any], text: str, *, source_sha256: str,
                extractor: str, fetched_at: str) -> Record:
    return Record(
        record_id=Record.make_id(ref.key, channel, locator, text), post_key=ref.key, channel=channel,
        locator=locator, text=text, source_sha256=source_sha256, extractor=extractor, fetched_at=fetched_at,
    )


def chunk_text(text: str, *, size: int = 800, limit: int = 60_000) -> list[tuple[int, str]]:
    """Cut long text into passages at line breaks, each <= size chars, with its offset into the text.

    Passages, not one blob: retrieval ranks them, and a quote is verified against the passage that holds it."""
    text = text[:limit]
    out: list[tuple[int, str]] = []
    start = 0
    pos = 0
    buf: list[str] = []
    buf_len = 0
    for line in text.split("\n"):
        piece = line + "\n"
        while len(piece) > size:  # a single very long line: hard cut
            if buf:
                out.append((start, "".join(buf)))
                buf, buf_len = [], 0
            out.append((pos, piece[:size]))
            pos += size
            piece = piece[size:]
            start = pos
        if buf_len + len(piece) > size and buf:
            out.append((start, "".join(buf)))
            buf, buf_len = [], 0
            start = pos
        if not buf:
            start = pos
        buf.append(piece)
        buf_len += len(piece)
        pos += len(piece)
    if buf:
        out.append((start, "".join(buf)))
    return [(off, t.strip()) for off, t in out if t.strip()]


# --- a small HTML scanner (stdlib only) ---------------------------------------------------------------------


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe", "object", "canvas"}
_BLOCK = {"p", "div", "section", "article", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr",
          "table", "blockquote", "pre", "header", "footer", "nav", "aside", "main", "figure", "figcaption", "dd",
          "dt", "hr", "title"}


ARTICLE_KEY = "<article>"  # HtmlScan.ids key for the text of the page's first <article> element


class HtmlScan:
    """What one pass over a page collects. Attributes are plain data so tests can assert on them."""

    def __init__(self) -> None:
        self.title = ""
        self.metas: list[tuple[str, str]] = []  # (name|property|itemprop lowercased, content)
        self.ld_json: list[str] = []
        self.json_scripts: dict[str, str] = {}  # script id -> text, for type=application/json scripts
        self.text = ""  # visible text, block elements on their own lines
        self.ids: dict[str, str] = {}  # text inside elements with a watched id
        self.id_images: dict[str, list[str]] = {}  # img data-src/src inside a watched id

    def meta(self, *keys: str) -> str:
        for key in keys:
            for k, v in self.metas:
                if k == key and v.strip():
                    return v.strip()
        return ""


class _Scanner(HTMLParser):
    def __init__(self, watch_ids: set[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.out = HtmlScan()
        self.watch = watch_ids
        self.stack: list[str] = []
        self.skip_depth = 0  # >0 while inside script/style/...
        self.script_kind: tuple[str, str] | None = None  # (type, id) of the current script
        self.script_buf: list[str] = []
        self.in_title = False
        self.title_buf: list[str] = []
        self.text_buf: list[str] = []
        self.active: list[tuple[str, int]] = []  # (watched id, stack depth where it opened)
        self.id_bufs: dict[str, list[str]] = {}

    # tags
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and "content" in a:
                self.out.metas.append((key, a["content"]))
            return
        if tag == "img":
            src = a.get("data-src") or a.get("src") or ""
            for wid, _ in self.active:
                if src:
                    self.out.id_images.setdefault(wid, []).append(src)
            return
        if tag == "br":
            self._newline()
            return
        if tag in _VOID:
            return
        self.stack.append(tag)
        if tag == "title" and "svg" not in self.stack and not self.out.title:
            self.in_title = True
            self.title_buf = []
        if tag == "script":
            self.script_kind = (a.get("type", "").lower(), a.get("id", ""))
            self.script_buf = []
        if tag in _SKIP:
            self.skip_depth += 1
        el_id = a.get("id", "")
        if el_id in self.watch:
            self.active.append((el_id, len(self.stack)))
            self.id_bufs.setdefault(el_id, [])
        if tag == "article" and ARTICLE_KEY not in self.id_bufs:  # the page's first <article>: an article's body
            self.active.append((ARTICLE_KEY, len(self.stack)))
            self.id_bufs[ARTICLE_KEY] = []
        if tag in _BLOCK:
            self._newline()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID or tag not in self.stack:
            return
        while self.stack:
            top = self.stack.pop()
            self._close(top)
            if top == tag:
                break

    def _close(self, tag: str) -> None:
        depth_after = len(self.stack)
        if tag == "title":
            self.in_title = False
            if not self.out.title:
                self.out.title = " ".join("".join(self.title_buf).split())
        if tag == "script" and self.script_kind is not None:
            typ, sid = self.script_kind
            body = "".join(self.script_buf)
            if "ld+json" in typ:
                self.out.ld_json.append(body)
            elif "json" in typ and sid:
                self.out.json_scripts[sid] = body
            self.script_kind = None
        if tag in _SKIP and self.skip_depth:
            self.skip_depth -= 1
        while self.active and self.active[-1][1] > depth_after:
            self.active.pop()
        if tag in _BLOCK:
            self._newline()

    def _newline(self) -> None:
        if not self.skip_depth:
            self.text_buf.append("\n")
            for wid, _ in self.active:
                self.id_bufs[wid].append("\n")

    def handle_data(self, data: str) -> None:
        if self.script_kind is not None:
            self.script_buf.append(data)
        if self.in_title:
            self.title_buf.append(data)
            return
        if self.skip_depth:
            return
        self.text_buf.append(data)
        for wid, _ in self.active:
            self.id_bufs[wid].append(data)

    def finish(self) -> HtmlScan:
        self.close()
        if self.in_title and not self.out.title:
            self.out.title = " ".join("".join(self.title_buf).split())
        self.out.text = clean_text("".join(self.text_buf))
        self.out.ids = {k: clean_text("".join(v)) for k, v in self.id_bufs.items()}
        return self.out


def scan_html(html: str, watch_ids: set[str] | None = None) -> HtmlScan:
    """One tolerant pass: title, metas, JSON-LD, JSON scripts, visible text, text of watched ids."""
    scanner = _Scanner(watch_ids or set())
    try:
        scanner.feed(html)
    except Exception:  # malformed markup must not lose what was already collected
        pass
    return scanner.finish()


def fragment_text(fragment: str) -> str:
    """An HTML fragment (a caption with <br> and emoji <img alt>) as plain text."""
    if not fragment:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    s = re.sub(r"<img[^>]*\balt=\"([^\"]*)\"[^>]*>", r"\1", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return clean_text(html_lib.unescape(s))
