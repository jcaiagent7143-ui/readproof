"""Is a fetched page the post, or a wall? (DESIGN.md §1 rule 6, §5.)

A wall never reaches a model as page text: every fetch a reader makes is judged here, and only a
``served`` verdict lets its body become Records. A 200 OK block page is the failure this module exists
for, so a verdict is never taken from the status code alone.

Order of rules, and why:

1. A transport error has no page to judge: ``network``.
2. Platform rules (``spec.judge``) come first, because they know things no generic rule can: that a page
   with no visible text is a served Xiaohongshu note (its payload is in a script), or that Bilibili's
   ``-412`` arrives inside an HTTP 200.
3. Generic rules (DESIGN §5): status codes, login and home redirects read from ``final_url``, challenge
   markers of Cloudflare / Akamai / Tencent / ByteDance, then "visible text under 40 characters and no
   meaningful title" as ``empty``.

Every signal carries its evidence: the rule that fired, status, bytes, visible characters and the marker
or address that matched, so a wall in a result table can be traced to the page that caused it.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .types import FetchResult, WallType, WallVerdict

__all__ = ["visible_text", "judge", "EMPTY_BELOW"]

# DESIGN §5: a 200 page with fewer visible characters than this, and no title that names anything, is empty.
EMPTY_BELOW = 40

# Elements whose content a reader never sees. <title> is returned on its own, not as body text.
_SKIP = frozenset({"script", "style", "noscript", "template", "svg", "title"})
# Inline elements do not separate words: 「参数<span>错误</span>」 must stay one word for marker matching.
_INLINE = frozenset({
    "a", "abbr", "b", "bdi", "bdo", "cite", "code", "data", "dfn", "em", "font", "i", "img", "kbd", "label",
    "mark", "q", "rp", "rt", "ruby", "s", "samp", "small", "span", "strong", "sub", "sup", "time", "u", "var",
    "wbr", "input",
})
_INVISIBLE_CHARS = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)


class _VisibleText(HTMLParser):
    """Collects the text a person would see: no scripts, styles, templates, SVG, comments or hidden inputs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: list[str] = []
        self.text: list[str] = []
        self._stack: list[str] = []  # open skipped elements, innermost last
        self._title_done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._stack.append(tag)
            return
        if not self._stack and tag not in _INLINE:
            self.text.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # <svg/>, <br/>, <input type="hidden"/>: void, so nothing is pushed; a block break still separates words.
        if not self._stack and tag not in _INLINE:
            self.text.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and tag in self._stack:
            while self._stack:
                closed = self._stack.pop()
                if closed == "title" and not self._stack:  # the page's own title, not an SVG's
                    self._title_done = True
                if closed == tag:
                    break
            return
        if not self._stack and tag not in _INLINE:
            self.text.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._stack:
            self.text.append(data)
        elif self._stack == ["title"] and not self._title_done:
            self.title.append(data)


def _squash(parts: list[str]) -> str:
    return re.sub(r"\s+", " ", "".join(parts).translate(_INVISIBLE_CHARS)).strip()


def visible_text(html: str) -> tuple[str, str]:
    """(title, visible text) of an HTML page, entities decoded and whitespace collapsed.

    Dropped: script, style, noscript, template, svg (its own <title> included), comments, hidden inputs
    (inputs carry no text nodes at all). CSS is not evaluated on purpose: WeChat hides a served article with
    ``visibility:hidden`` until its scripts run, and treating that as invisible would call every article empty.
    """
    if not html:
        return "", ""
    parser = _VisibleText()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # pragma: no cover - html.parser is lenient; a crash must not become a "served" page
        return "", ""
    return _squash(parser.title), _squash(parser.text)


# --------------------------------------------------------------------------------------------------------------
# Generic rules


# Paths of login pages and interstitials, compared as prefixes of the landing path.
_LOGIN_PATHS = (
    "/login", "/signin", "/sign_in", "/sign-in", "/accounts/login", "/account/login", "/i/flow/login",
    "/authwall", "/uas/login", "/website-login", "/passport/login", "/user/login", "/member/login", "/auth/login",
    "/sso/login", "/login.php",
)
_LOGIN_HOST_PREFIXES = ("login.", "signin.", "passport.", "sso.")
_LOGIN_HOSTS = ("accounts.google.com",)
# Where a site sends an address it will not serve: its root, its feed, a generic listing.
_HOME_PATHS = frozenset({
    "", "/", "/index", "/index.html", "/index.htm", "/index.php", "/home", "/explore", "/discover", "/discovery",
    "/feed", "/foryou", "/hot", "/trending", "/jingxuan", "/recommend",
})
# Titles that name a site, not a post: an app shell carries one of these and nothing else.
_SITE_TITLES = frozenset({
    "抖音", "小红书", "rednote", "tiktok", "tiktok - make your day", "哔哩哔哩", "bilibili", "微博", "weibo",
    "新浪微博", "今日头条", "头条", "x", "twitter", "youtube", "instagram", "facebook", "linkedin", "微信公众平台",
    "微信", "wechat", "error", "loading", "loading...", "加载中",
})
_SITE_SLOGAN = re.compile(
    r"^\s*(小红书|rednote|抖音|douyin|tiktok|instagram|facebook|bilibili|哔哩哔哩|twitter|youtube|微博|今日头条)\s*[-|·—_]\s*\S",
    re.I,
)
# A short 200 page whose title says the page is missing (a soft "not found").
_NOT_FOUND_TITLE = re.compile(r"^\s*(?:404(?:\b|$)|page not found|not found|页面不存在|页面未找到|找不到页面)", re.I)
# Challenge markers. Matched only on short pages or refusal statuses (see _challenge_marker): a served
# TikTok page mentions "captcha" 34 times in its scripts (live capture 2026-11-21).
_CHALLENGE_TITLE = re.compile(
    r"^\s*(?:just a moment\.{0,3}|attention required! \| cloudflare|access denied|security check|robot check|"
    r"are you a robot\??|ddos-guard|验证码|安全验证|人机验证|访问验证)(?:$|[\s_\-|·—:：])",
    re.I,
)
_CHALLENGE_HTML = (
    ("cloudflare", "/cdn-cgi/challenge-platform/"),
    ("cloudflare", "cf-chl-"),
    ("cloudflare", "cf_chl_opt"),
    ("cloudflare", "challenges.cloudflare.com/turnstile"),
    ("akamai", "errors.edgesuite.net"),
    ("imperva", "_Incapsula_Resource"),
    ("datadome", "geo.captcha-delivery.com"),
    ("tencent", "TCaptcha"),
    ("tencent", "turing.captcha.qcloud.com"),
    ("tencent", "captcha.qq.com"),
    ("bytedance", "byted_acrawler"),
    ("bytedance", "_$jsvmprt"),
    ("bytedance", "verifycenter"),
)
_CHALLENGE_TEXT = (
    "verify you are human", "checking your browser", "are you a robot", "complete the security check",
    "enable javascript and cookies to continue", "请完成安全验证", "请完成下列验证", "拖动下方滑块", "请拖动滑块",
    "人机验证", "安全验证", "访问验证",
)
_SHORT_PAGE = 1000  # visible characters; challenge text and titles count only on pages shorter than this
_REFUSAL = frozenset({403, 412, 429, 503})
_MEDIA_TYPES = ("image/", "video/", "audio/", "font/", "application/octet-stream", "application/vnd.apple.mpegurl",
                "application/x-mpegurl", "application/dash+xml")


def _header(fetch: FetchResult, name: str) -> str:
    name = name.lower()
    for key, value in (fetch.headers or {}).items():
        if key.lower() == name:
            return value or ""
    return ""


def _landing(fetch: FetchResult) -> str:
    """Where this fetch ended: the Location of an unfollowed redirect, else the final URL."""
    final = fetch.final_url or fetch.url
    location = _header(fetch, "location")
    if fetch.status is not None and 300 <= fetch.status < 400 and location:
        return urljoin(final, location.strip())
    return final


def _parts(url: str) -> tuple[str, str]:
    try:
        sp = urlsplit(url)
        return (sp.hostname or "").lower().rstrip("."), sp.path or ""
    except ValueError:
        return "", ""


def _is_login(url: str) -> str | None:
    host, path = _parts(url)
    lowered = path.lower()
    for prefix in _LOGIN_PATHS:
        if lowered == prefix or lowered.startswith(prefix + "/"):
            return prefix
    if host in _LOGIN_HOSTS or host.startswith(_LOGIN_HOST_PREFIXES):
        return host
    return None


def _is_home(url: str) -> bool:
    _, path = _parts(url)
    return path.rstrip("/").lower() in _HOME_PATHS


def _is_not_found_path(url: str) -> bool:
    _, path = _parts(url)
    lowered = path.lower()
    return lowered == "/404" or lowered.startswith(("/404/", "/404.", "/error/404"))


def _site_title(title: str) -> bool:
    cleaned = title.strip().strip(" ·|-—_").lower()
    return not cleaned or cleaned in _SITE_TITLES or bool(_SITE_SLOGAN.match(title))


def _quote(value: str) -> str:
    value = value.replace("\n", " ")[:160]
    return repr(value) if (" " in value or not value) else value


def evidence(rule: str, fetch: FetchResult, visible_chars: int, /, *, marker: str | None = None,
             **extra: object) -> str:
    """The signal string: rule, status, bytes, visible characters, the marker, and where the fetch landed."""
    parts = [rule, f"status={fetch.status}", f"bytes={fetch.bytes}", f"visible_chars={visible_chars}"]
    if marker:
        parts.append(f"marker={_quote(marker)}")
    for key, value in extra.items():
        if value is not None and value != "":
            parts.append(f"{key}={_quote(str(value))}")
    landed = _landing(fetch)
    if landed and landed != fetch.url:
        parts.append(f"final={landed[:200]}")
    return " ".join(parts)


def verdict(wall: WallType | None, rule: str, fetch: FetchResult, visible_chars: int, /, *,
            marker: str | None = None, **extra: object) -> WallVerdict:
    """A WallVerdict whose signal carries the evidence. ``wall=None`` means served."""
    return WallVerdict(
        served=wall is None,
        wall=wall,
        signal=evidence(rule, fetch, visible_chars, marker=marker, **extra),
        visible_chars=visible_chars,
    )


def _challenge_marker(fetch: FetchResult, html: str, title: str, text: str) -> str | None:
    if _header(fetch, "cf-mitigated").lower() == "challenge":
        return "cloudflare:cf-mitigated"
    short = len(text) < _SHORT_PAGE or fetch.status in _REFUSAL
    if not short:
        return None
    if title and _CHALLENGE_TITLE.match(title):
        return f"title:{title[:60]}"
    for vendor, marker in _CHALLENGE_HTML:
        if marker in html:
            return f"{vendor}:{marker}"
    lowered = text.lower()
    for marker in _CHALLENGE_TEXT:
        if marker in lowered:
            return f"text:{marker}"
    return None


def _generic(fetch: FetchResult, html: str, title: str, text: str) -> WallVerdict:
    n = len(text)
    status = fetch.status or 0
    landed = _landing(fetch)
    if status == 429:
        return verdict(WallType.RATE_LIMITED, "generic:http-429", fetch, n, retry_after=_header(fetch, "retry-after"))
    if status in (404, 410):
        return verdict(WallType.DELETED, f"generic:http-{status}", fetch, n)
    if status == 401:
        return verdict(WallType.LOGIN, "generic:http-401", fetch, n)
    if status == 451:
        return verdict(WallType.REGION, "generic:http-451", fetch, n)
    login = _is_login(landed)
    if login:
        return verdict(WallType.LOGIN, "generic:login-redirect", fetch, n, marker=login)
    if landed != fetch.url and _is_not_found_path(landed) and not _is_not_found_path(fetch.url):
        return verdict(WallType.DELETED, "generic:not-found-redirect", fetch, n)
    if landed != fetch.url and _is_home(landed) and not _is_home(fetch.url):
        return verdict(WallType.SOFT_404, "generic:home-redirect", fetch, n)
    if 300 <= status < 400:
        return verdict(WallType.UNKNOWN, "generic:redirect-not-followed", fetch, n)
    marker = _challenge_marker(fetch, html, title, text)
    if marker:
        return verdict(WallType.CHALLENGE, "generic:challenge", fetch, n, marker=marker)
    if status in (403, 412):
        return verdict(WallType.CHALLENGE, f"generic:http-{status}-refused", fetch, n)
    if status >= 500:
        return verdict(WallType.NETWORK, f"generic:http-{status}", fetch, n)
    if status >= 400:
        return verdict(WallType.UNKNOWN, f"generic:http-{status}", fetch, n)
    if n < _SHORT_PAGE and title and _NOT_FOUND_TITLE.match(title):
        return verdict(WallType.DELETED, "generic:not-found-title", fetch, n, marker=title[:60])
    if n < EMPTY_BELOW and _site_title(title):
        return verdict(WallType.EMPTY, "generic:empty", fetch, n, title=title or None)
    return verdict(None, "served", fetch, n)


def _spec_for(fetch: FetchResult):
    """The platform of the address that was asked for (not where it landed: a login host belongs to it)."""
    from . import platforms  # late import: platform modules import this module

    spec = platforms.for_url(fetch.url) or platforms.for_url(fetch.final_url or "")
    return None if spec is None or spec.name == "web" else spec


def _media(fetch: FetchResult) -> bool:
    ctype = _header(fetch, "content-type").split(";")[0].strip().lower()
    return bool(ctype) and ctype.startswith(_MEDIA_TYPES)


def judge(fetch: FetchResult, spec=None) -> WallVerdict:
    """Served, or which wall, with the evidence. Platform rules first, then the generic rules (DESIGN §5).

    ``spec`` defaults to the platform of ``fetch.url``; pass one to judge a fetch against a platform
    explicitly (an embed player address, say)."""
    if fetch.error and str(fetch.error).startswith("gate_refused:"):
        # Our own gate refused this URL or a redirect hop (HttpNet sets "gate_refused:<reason>"): nothing was sent
        # for it, so this is a policy wall, not a transport failure.
        return WallVerdict(
            served=False, wall=WallType.POLICY,
            signal=f"gate:{str(fetch.error).split(':', 1)[1]} status={fetch.status} bytes={fetch.bytes} visible_chars=0",
            visible_chars=0,
        )
    if fetch.error or fetch.status is None:
        return WallVerdict(
            served=False, wall=WallType.NETWORK,
            signal=f"generic:transport status=None bytes={fetch.bytes} visible_chars=0 error={_quote(str(fetch.error))}",
            visible_chars=0,
        )
    if 200 <= fetch.status < 300 and _media(fetch):
        return verdict(None, "served:media", fetch, 0, content_type=_header(fetch, "content-type"))
    from .platforms._base import page_of  # late import, as above

    page = page_of(fetch)
    if spec is None:
        spec = _spec_for(fetch)
    if spec is not None:
        platform_verdict = spec.judge(fetch, page.text)
        if platform_verdict is not None:
            return platform_verdict
    return _generic(fetch, page.html, page.title, page.text)
