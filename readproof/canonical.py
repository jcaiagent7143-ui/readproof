"""From what a person pasted to the post it names, without reading the post (DESIGN.md §4 steps 1–4).

What the apps put on the clipboard is a paragraph with a link in it —
「7.43 复制打开抖音，看看【…】 https://v.douyin.com/…/ 复制此链接…」,
「【标题 - 作者 | 小红书 - 你的生活指南】 😆 http://xhslink.com/o/… 复制本条信息…」 — and people paste all of it.
``extract_url`` takes the first link out; ``resolve`` turns it into a PostRef and the URL to read.

How a short link is expanded, and why:
- One hop at a time, HEAD with redirects off (GET when HEAD answers >= 400 or no Location), each request asked
  of the gate first. Each Location is parsed before anything else is fetched: the first address a platform
  recognises is the post, so a hop a rewrite makes unnecessary is never requested — Toutiao's short link
  points at /group/<id>/, which its robots.txt disallows, and the id is read off the Location header.
- A login interstitial wrapping a post (xiaohongshu.com/login?redirectPath=<note>) is unwrapped by the
  platform's parse(), never fetched (LinkDigest 2026-10-28: fetching that page timed out and lost a note
  the first redirect had already named).
- A short link that lands on the platform's home page or a listing is a ``soft_404``: an unknown or
  case-changed code (v.douyin.com 302s it to https://www.douyin.com, confirmed live 2026-11-21).
- At most MAX_HOPS hops.

A post URL pasted directly is parsed without any request; the caller gates the canonical URL it then reads
(Resolution.url), not the pasted one — gating a pasted Toutiao /group/ form would refuse a link the rewrite
serves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from . import platforms, walls
from .interfaces import Context, PlatformSpec
from .types import FetchResult, GateDecision, PostRef, WallType, WallVerdict

__all__ = ["MAX_HOPS", "Resolution", "extract_url", "resolve"]

MAX_HOPS = 8
_GET_MAX_BYTES = 2_000_000  # a short link that answers with a page is judged, not downloaded in full

# Full-width ASCII (U+FF01–FF5E) and the ideographic space fold to ASCII: 「ｈｔｔｐｓ：／／…」 and a
# link glued to 「，」「：」「）」 then parse like any other. CJK brackets and 。、 are not folded, so they end a link.
_FOLD = {cp: cp - 0xFEE0 for cp in range(0xFF01, 0xFF5F)}
_FOLD[0x3000] = 0x20
# ASCII only, on purpose: Chinese share text glues words straight onto a link (「…/abc/复制此链接」).
_URL_CHARS = r"[A-Za-z0-9\-._~:/?#@!$&()*+,;=%]"
_SCHEMED = re.compile(rf"(?<![A-Za-z0-9.\-/@])https?://{_URL_CHARS}+", re.I)
_BARE_WWW = re.compile(rf"(?<![A-Za-z0-9.\-/@])www\.[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+(?:/{_URL_CHARS}*)?", re.I)
_TRAILING = ".,;:!?*~"
_BARE_SHORT: re.Pattern[str] | None = None

# What a redirect target that is not a post is, when it is not a dead end (soft_404) but another kind of page.
_NOT_A_POST = frozenset({"profile", "music", "live", "collection", "product", "article", "topic", "bangumi",
                         "video_without_id"})


def _trim(url: str) -> str:
    """Drop the punctuation a sentence put after a link; keep a balanced ')'."""
    while url:
        if url[-1] in _TRAILING:
            url = url[:-1]
        elif url[-1] == ")" and url.count("(") < url.count(")"):
            url = url[:-1]
        else:
            break
    return url


def _bare_short() -> re.Pattern[str]:
    """Share short links written without a scheme (v.douyin.com/…, xhslink.com/…, b23.tv/…, youtu.be/…)."""
    global _BARE_SHORT
    if _BARE_SHORT is None:
        hosts = sorted({h for spec in platforms.REGISTRY
                        for h in tuple(getattr(spec, "short_hosts", ())) + tuple(getattr(spec, "bare_hosts", ()))},
                       key=len, reverse=True)
        alternation = "|".join(re.escape(h) for h in hosts) or r"(?!)"
        _BARE_SHORT = re.compile(rf"(?<![A-Za-z0-9.\-/@])(?:{alternation})/{_URL_CHARS}+", re.I)
    return _BARE_SHORT


def extract_url(text: str) -> str | None:
    """The first http(s) URL in a pasted text, trailing CJK and ASCII punctuation removed; else a share
    short link or www. address written without a scheme (returned with https://); else None."""
    if not text:
        return None
    folded = text.translate(_FOLD)
    m = _SCHEMED.search(folded)
    if m:
        url = _trim(m.group(0))
        scheme, _, rest = url.partition("://")
        return f"{scheme.lower()}://{rest}" if rest else None
    for pattern in (_bare_short(), _BARE_WWW):
        m = pattern.search(folded)
        if m:
            url = _trim(m.group(0))
            return f"https://{url}" if url else None
    return None


@dataclass
class Resolution:
    """What resolve() found. ``url`` is the address to read (the platform's canonical form) once a post is
    recognised, else the last address reached; ``verdict`` is None exactly when ``ref`` is set."""

    input: str
    url: str | None
    ref: PostRef | None
    spec: PlatformSpec | None
    fetches: list[FetchResult] = field(default_factory=list)
    gate: list[GateDecision] = field(default_factory=list)
    verdict: WallVerdict | None = None

    @property
    def resolved(self) -> bool:
        return self.ref is not None and self.verdict is None


def _clip(value: str | None, limit: int = 160) -> str:
    return (value or "").replace("\n", " ")[:limit]


def _fail(wall: WallType, rule: str, /, **fields: object) -> WallVerdict:
    parts = [rule] + [f"{k}={_clip(str(v))}" for k, v in fields.items() if v is not None and v != ""]
    return WallVerdict(served=False, wall=wall, signal=" ".join(parts), visible_chars=0)


def _header(fetch: FetchResult, name: str) -> str:
    for key, value in (fetch.headers or {}).items():
        if key.lower() == name:
            return (value or "").strip()
    return ""


def _is_short(spec: PlatformSpec, url: str) -> bool:
    check = getattr(spec, "is_short", None)
    return bool(check(url)) if check else False


def _kind(spec: PlatformSpec, url: str) -> str | None:
    check = getattr(spec, "page_kind", None)
    return check(url) if check else None


def _rewrite(url: str) -> tuple[PlatformSpec, PostRef] | None:
    """A post identified from an address alone (a canonical rewrite needs no request)."""
    spec = platforms.for_url(url)
    ref = spec.parse(url) if spec is not None else None
    return (spec, ref) if ref is not None else None


def _not_a_post(spec: PlatformSpec, url: str, *, hop: int, status: int | None = None) -> WallVerdict:
    kind = _kind(spec, url)
    if hop == 0:
        return _fail(WallType.UNSUPPORTED, "resolve:not-a-post", platform=spec.name, page_kind=kind or "unknown", url=url)
    fields = {"hop": hop, "status": status, "platform": spec.name, "page_kind": kind or "unknown", "location": url}
    if kind == "login":
        return _fail(WallType.LOGIN, "resolve:login", **fields)
    if kind == "not_found":
        return _fail(WallType.DELETED, "resolve:not-found", **fields)
    if kind in _NOT_A_POST:
        return _fail(WallType.UNSUPPORTED, "resolve:not-a-post", **fields)
    # The home page, a feed, a listing, or a page that names nothing: the link opened somewhere that is not it.
    return _fail(WallType.SOFT_404, "resolve:soft-404", **fields)


def _finish(ctx: Context, res: Resolution, verdict: WallVerdict | None = None) -> Resolution:
    if verdict is not None:
        res.verdict = verdict
    try:
        ctx.log.emit(
            "resolve",
            input=_clip(res.input, 300), url=res.url,
            platform=res.spec.name if res.spec is not None else None,
            post_key=res.ref.key if res.ref is not None else None,
            form=res.ref.form if res.ref is not None else None,
            requests=len(res.fetches), gate_refused=sum(1 for d in res.gate if not d.allowed),
            wall=res.verdict.wall.value if res.verdict is not None and res.verdict.wall is not None else None,
            signal=res.verdict.signal if res.verdict is not None else None,
        )
    except Exception:  # a broken log must not turn a resolution into a crash; the log builder owns failures
        pass
    return res


def _found(ctx: Context, res: Resolution, spec: PlatformSpec, ref: PostRef) -> Resolution:
    res.spec, res.ref, res.url, res.verdict = spec, ref, spec.canonical(ref), None
    return _finish(ctx, res)


def _hop(ctx: Context, res: Resolution, url: str, hop: int) -> tuple[FetchResult | None, str | None, WallVerdict | None]:
    """One hop: HEAD, then GET when HEAD gave >= 400 or no Location. Returns (fetch, location, verdict)."""
    fetch: FetchResult | None = None
    for method in ("HEAD", "GET"):
        decision = ctx.gate.check(url, purpose="resolve")
        res.gate.append(decision)
        if not decision.allowed:
            rewrite = _rewrite(url)
            if rewrite is not None:  # never the case for a short code, kept so the rule holds for any hop
                return None, url, None
            return None, None, _fail(WallType.POLICY, "resolve:policy", hop=hop, reason=decision.reason,
                                     rule=decision.matched_rule, group=decision.robots_group, url=url)
        fetch = ctx.net.fetch(url, method=method, follow_redirects=False,
                              max_bytes=_GET_MAX_BYTES, purpose="resolve")
        res.fetches.append(fetch)
        if fetch.body:
            try:
                ctx.store.put(fetch.body, content_type=_header(fetch, "content-type"), source_url=fetch.url)
            except Exception:  # pragma: no cover - the store builder owns its failures
                pass
        location = _header(fetch, "location")
        try:
            ctx.log.emit("resolve.hop", hop=hop, method=method, url=url, status=fetch.status,
                         location=location or None, error=fetch.error)
        except Exception:  # pragma: no cover
            pass
        if fetch.error or fetch.status is None:
            return fetch, None, walls.judge(fetch)
        if 300 <= fetch.status < 400 and location:
            return fetch, location, None
    assert fetch is not None
    # Even GET sent us nowhere: the shortener answered with a page of its own.
    judged = walls.judge(fetch)
    if judged.served:
        judged = _fail(WallType.SOFT_404, "resolve:no-redirect", hop=hop, status=fetch.status,
                       bytes=fetch.bytes, url=url)
    return fetch, None, judged


def resolve(text_or_url: str, ctx: Context) -> Resolution:
    """Share text or URL -> the post and the URL to read, or a typed verdict saying why there is none."""
    res = Resolution(input=text_or_url, url=None, ref=None, spec=None)
    url = extract_url(text_or_url or "")
    if url is None:
        return _finish(ctx, res, _fail(WallType.UNSUPPORTED, "resolve:no-url", input=_clip(text_or_url, 80)))
    res.url = url
    spec = platforms.for_url(url)
    res.spec = spec
    if spec is None:
        return _finish(ctx, res, _fail(WallType.UNSUPPORTED, "resolve:not-http", url=url))
    ref = spec.parse(url)
    if ref is not None:
        return _found(ctx, res, spec, ref)
    if not _is_short(spec, url):
        return _finish(ctx, res, _not_a_post(spec, url, hop=0))
    current = url
    for hop in range(1, MAX_HOPS + 1):
        fetch, location, stop = _hop(ctx, res, current, hop)
        if stop is not None:
            return _finish(ctx, res, stop)
        target = urljoin(current, location or "")
        res.url = target
        rewrite = _rewrite(target)
        if rewrite is not None:
            return _found(ctx, res, *rewrite)
        next_spec = platforms.for_url(target)
        if next_spec is None:
            return _finish(ctx, res, _fail(WallType.UNSUPPORTED, "resolve:non-web-location", hop=hop, location=target))
        if not _is_short(next_spec, target):
            return _finish(ctx, res, _not_a_post(next_spec, target, hop=hop,
                                                 status=fetch.status if fetch is not None else None))
        current = target
    return _finish(ctx, res, _fail(WallType.UNKNOWN, "resolve:too-many-hops", hops=MAX_HOPS, url=current))
