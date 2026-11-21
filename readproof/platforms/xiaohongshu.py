"""小红书 Xiaohongshu / RedNote (rednote.com is the same site: same note ids, paths and tokens).

Link forms (LinkDigest production, 2026-09 → 2026-11):
  www.xiaohongshu.com/explore/<24 hex>                    the web address bar
  www.xiaohongshu.com/discovery/item/<24 hex>             what an app share link opens
  www.xiaohongshu.com/search_result/<24 hex>              a note opened from a search
  www.xiaohongshu.com/user/profile/<uid>/<24 hex>         a note opened from a profile
  the same on www.rednote.com
  xhslink.com/<x>/<code>, xhslink.cn/o/<code>              the share button's short links (iOS copies .cn)
  …/login?redirectPath=<note URL, token and all>          the login interstitial, pasted from the address bar
  …/404?source=/404/sec_…?redirectPath=<note URL>&error_code=300031&error_msg=当前笔记暂时无法浏览

``xsec_token`` is compared byte for byte: ``…dFo%3D`` is a login wall and ``…dFo=`` the note, and a share
link that passed through the login interstitial arrives double-encoded, so the token is decoded exactly once
and always written back literally (never through urlencode).

Wall signatures:
  /website-login/captcha                  the captcha wall (rolls: it stood for hours on 2026-10-26)  -> challenge
  /login?redirectPath=…, /website-login/… the login interstitial (live 2026-11-21: xiaohongshu.com 302s the
                                          ReadProof UA to rednote.com, which 302s to /login?redirectPath=)  -> login
  /404/sec_…&error_code=300031 (当前笔记暂时无法浏览, 请打开小红书扫码查看)
        with a token, from the main site  -> app_only   (the note is kept for the app)
        without a token                   -> token      (the same note reads through a share link that has one)
        answered by rednote.com to a request that started there -> unknown (rednote answers 300031 for notes
                                          the main site serves, 2026-10-29)
  「页面不见了」  without a token -> token; with one -> deleted (Xiaohongshu shows the same screen for both)
  the feed (/explore?exSource=null) where a note was asked for -> soft_404
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, quote, unquote

from ..types import FetchResult, PostRef, WallType, WallVerdict
from ._base import Spec, host_of, landing, on_domain, page_of, param, path_of, raw_param, split, verdict

NOTE_ID = r"[0-9a-fA-F]{24}"
# rednote.com serves its English site under a locale prefix (www.rednote.com/en/discovery/item/<id>, indexed by
# search engines with machine-translated titles, 2026-11-21); the note id is the same as on xiaohongshu.com.
_LOCALE = r"(?:/(?P<locale>[a-z]{2}(?:-[A-Za-z]{2,4})?))?"
_NOTE_PATHS = (
    (re.compile(rf"^{_LOCALE}/explore/(?P<id>{NOTE_ID})(?:/|$)"), "explore"),
    (re.compile(rf"^{_LOCALE}/discovery/item/(?P<id>{NOTE_ID})(?:/|$)"), "discovery_item"),
    (re.compile(rf"^/search_result/(?P<id>{NOTE_ID})(?:/|$)"), "search_result"),
    (re.compile(rf"^/user/profile/{NOTE_ID}/(?P<id>{NOTE_ID})(?:/|$)"), "profile_note"),
)
_LOCALE_PREFIX = re.compile(r"^/[a-z]{2}(?:-[A-Za-z]{2,4})?(?=/(?:explore|discovery)(?:/|$))")


def strip_locale(path: str) -> str:
    """/en/explore -> /explore (the locale prefix rednote.com puts before its own paths)."""
    return _LOCALE_PREFIX.sub("", path or "", count=1)


def tokenless_form(site: str, kind: str) -> str:
    """The form name of a note address without xsec_token, the same names forms() writes: token_stripped (the
    canonical explore form), discovery_token_stripped, rednote_token_stripped, rednote_discovery_token_stripped, …"""
    base = {"explore": "token_stripped", "discovery_item": "discovery_token_stripped"}.get(kind, f"{kind}_token_stripped")
    return base if site == "xiaohongshu" else f"rednote_{base}"
_WRAPPERS = ("/login", "/website-login", "/404")
_XSEC = re.compile(r"(xsec_token=)([^&#]*)")
_APP_ONLY = ("当前笔记暂时无法浏览", "请打开小红书扫码查看")
_PAGE_GONE = "页面不见了"
# The note payload the server renders into window.__INITIAL_STATE__ (desktop) or noteData (mobile page).
_DESKTOP_NOTE = re.compile(r'"noteDetailMap"\s*:\s*\{\s*"([0-9a-f]{24})"\s*:\s*\{.{0,4000}?"note"\s*:\s*\{\s*"', re.S)
_MAIN, _REDNOTE = "https://www.xiaohongshu.com", "https://www.rednote.com"


def literal_token(url: str) -> str:
    """Decode the xsec_token value once, so ``%3D`` (a login wall) becomes the ``=`` the note needs."""
    return _XSEC.sub(lambda m: m.group(1) + unquote(m.group(2)), url)


def alter_token(token: str) -> str:
    """A deterministic one-character change of a token (the 'altered / expired' control of DESIGN §6)."""
    chars = list(token)
    for i in range(len(chars) - 1, -1, -1):
        ch = chars[i]
        if ch.isdigit():
            chars[i] = "0" if ch == "9" else chr(ord(ch) + 1)
            return "".join(chars)
        if ch.isascii() and ch.isalpha():
            chars[i] = {"z": "a", "Z": "A"}.get(ch, chr(ord(ch) + 1))
            return "".join(chars)
    return token + "A"


class XiaohongshuSpec(Spec):
    name = "xiaohongshu"
    domains = ("xiaohongshu.com", "rednote.com")
    short_hosts = ("xhslink.com", "xhslink.cn")

    # --- parsing --------------------------------------------------------------------------------------------

    def _direct(self, url: str) -> PostRef | None:
        """A note address itself (not an interstitial)."""
        url = literal_token(url)
        host, path = host_of(url), path_of(url)
        if not on_domain(host, *self.domains):
            return None
        for rx, kind in _NOTE_PATHS:
            m = rx.match(path)
            if m:
                note_id = m.group("id").lower()
                token = raw_param(url, "xsec_token") or None
                site = "rednote" if on_domain(host, "rednote.com") else "xiaohongshu"
                if token:
                    form = kind if site == "xiaohongshu" else f"rednote_{kind}"
                else:
                    form = tokenless_form(site, kind)
                locale = m.groupdict().get("locale")
                return self.ref(note_id, form, url, xsec_token=token, xsec_source=param(url, "xsec_source"),
                                site=site, path_kind=kind, locale=locale)
        return None

    def wrapped(self, url: str) -> str | None:
        """The note address inside a login or /404 interstitial, unwrapped without fetching it.

        The /404/sec_… page nests redirectPath after a second '?' inside its `source` value, so it is not a
        query key of its own there; it is read from the raw address in that case."""
        if not on_domain(host_of(url), *self.domains) or not path_of(url).startswith(_WRAPPERS):
            return None
        inner = (parse_qs(split(url).query).get("redirectPath") or [""])[0]
        if not inner:
            m = re.search(r"[?&]redirectPath=([^&]+)", url)
            inner = unquote(m.group(1)) if m else ""
        if not inner.startswith(("http://", "https://")) or not on_domain(host_of(inner), *self.domains):
            return None
        return literal_token("https://" + inner.split("://", 1)[1])

    def parse(self, url: str) -> PostRef | None:
        if not self.match(url) or self.is_short(url):
            return None
        path = path_of(url)
        if path.startswith(_WRAPPERS):
            inner = self.wrapped(url)
            ref = self._direct(inner) if inner else None
            if ref is None:
                return None
            ref.form = "sec_404_wrapped" if path.startswith("/404") else "login_wrapped"
            ref.extra.update({k: v for k, v in {
                "wrapper_url": url,
                "error_code": param(url, "error_code") or _code_in(unquote(url)),
                "error_msg": param(url, "error_msg"),
            }.items() if v})
            return ref
        return self._direct(url)

    def page_kind(self, url: str) -> str | None:
        path = path_of(url)
        if path.startswith(_WRAPPERS):
            inner = self.wrapped(url)
            if inner:
                return self.page_kind(inner) or ("not_found" if path.startswith("/404") else "login")
            return "not_found" if path.startswith("/404") else "login"
        p = strip_locale(path).rstrip("/")
        if p == "":
            return "home"
        if p in ("/explore", "/discovery", "/discovery/item", "/search_result"):
            return "listing"
        if p.startswith("/user/profile/"):
            return "profile"
        if p.startswith("/search"):
            return "search"
        return None

    # --- forms ----------------------------------------------------------------------------------------------

    def canonical(self, ref: PostRef) -> str:
        """explore + the literal token: the bare form the web app itself requests (a share link's own
        address can answer 300031 while this form still serves the note, LinkDigest 2026-10-29)."""
        token = ref.extra.get("xsec_token")
        base = f"{_MAIN}/explore/{ref.post_id}"
        return f"{base}?xsec_token={token}&xsec_source=pc_share" if token else base

    def forms(self, ref: PostRef, **known: str) -> list[tuple[str, str]]:
        nid = ref.post_id
        token = known.get("xsec_token") or ref.extra.get("xsec_token")
        if token:
            inner = f"{_MAIN}/discovery/item/{nid}?xsec_token={token}&xsec_source=app_share"
            out = [
                ("explore", f"{_MAIN}/explore/{nid}?xsec_token={token}&xsec_source=pc_share"),
                ("discovery_item", f"{_MAIN}/discovery/item/{nid}?xsec_token={token}&xsec_source=app_share&type=normal"),
                ("rednote_explore", f"{_REDNOTE}/explore/{nid}?xsec_token={token}&xsec_source=pc_share"),
                ("rednote_discovery_item",
                 f"{_REDNOTE}/discovery/item/{nid}?xsec_token={token}&xsec_source=app_share&type=normal"),
                ("token_stripped", f"{_MAIN}/explore/{nid}"),
                ("token_altered", f"{_MAIN}/explore/{nid}?xsec_token={alter_token(token)}&xsec_source=pc_share"),
                ("login_wrapped", f"{_MAIN}/login?redirectPath={quote(inner, safe='')}"),
            ]
            expired = known.get("expired_token")
            if expired and expired != token:
                out.append(("token_expired", f"{_MAIN}/explore/{nid}?xsec_token={expired}&xsec_source=pc_share"))
        else:
            out = [
                ("token_stripped", f"{_MAIN}/explore/{nid}"),
                ("discovery_token_stripped", f"{_MAIN}/discovery/item/{nid}"),
                ("rednote_token_stripped", f"{_REDNOTE}/explore/{nid}"),
            ]
        link = known.get("short") or out[0][1]
        title, author = known.get("title") or "标题", known.get("author") or "作者"
        text = f"【{title} - {author} | 小红书 - 你的生活指南】 😆 {link} 复制本条信息，打开【小红书】App查看精彩内容！"
        return self.with_short(out, known, text)

    # --- walls ----------------------------------------------------------------------------------------------

    def judge(self, fetch: FetchResult, visible_text: str) -> WallVerdict | None:
        n = len(visible_text)
        land = landing(fetch)
        lhost, lpath = host_of(land), path_of(land)
        on_site = on_domain(lhost, *self.domains)
        asked = self.parse(fetch.url)
        tokenless = asked is not None and not asked.extra.get("xsec_token")
        if on_site and lpath.startswith("/website-login/captcha"):
            return verdict(WallType.CHALLENGE, "xhs:captcha", fetch, n, marker="/website-login/captcha")
        if on_site and lpath.startswith(("/login", "/website-login")):
            inner = self.parse(land)
            return verdict(WallType.LOGIN, "xhs:login-interstitial", fetch, n, marker=lpath[:40],
                           wraps=inner.post_id if inner else None)
        page = page_of(fetch)
        decoded = unquote(land)
        code = param(land, "error_code") or _code_in(decoded)
        app_only = next((m for m in _APP_ONLY if m in page.html), None)
        if (on_site and (lpath.startswith("/404") or code)) or app_only:
            if code == "300031" or app_only or "当前笔记暂时无法浏览" in decoded:
                marker = app_only or f"error_code={code or '300031'}"
                if tokenless:
                    return verdict(WallType.TOKEN, "xhs:300031-tokenless", fetch, n, marker=marker)
                started_main = on_domain(host_of(fetch.url), "xiaohongshu.com")
                if on_domain(lhost, "rednote.com") and not started_main and not app_only:
                    return verdict(WallType.UNKNOWN, "xhs:300031-rednote-untrusted", fetch, n, marker=marker)
                return verdict(WallType.APP_ONLY, "xhs:300031", fetch, n, marker=marker)
            if code == "300013":  # 访问频次异常
                return verdict(WallType.RATE_LIMITED, "xhs:300013", fetch, n, marker="error_code=300013")
            if code:
                return verdict(WallType.UNKNOWN, "xhs:error-code", fetch, n, marker=f"error_code={code}")
        if _PAGE_GONE in visible_text or _PAGE_GONE in page.title:
            if tokenless:
                return verdict(WallType.TOKEN, "xhs:page-gone-tokenless", fetch, n, marker=_PAGE_GONE)
            return verdict(WallType.DELETED, "xhs:page-gone", fetch, n, marker=_PAGE_GONE,
                           token="present" if asked else None)
        if on_site and asked is not None and strip_locale(lpath).rstrip("/") in ("", "/explore", "/discovery"):
            return verdict(WallType.SOFT_404, "xhs:feed-redirect", fetch, n, marker=lpath or "/")
        m = _DESKTOP_NOTE.search(page.html)
        if m:
            same = asked is None or m.group(1) == asked.post_id
            return verdict(None, "xhs:note-payload", fetch, n, note=m.group(1), same_id=str(same).lower())
        if '"noteData"' in page.html and '"imageList"' in page.html and (asked is None or asked.post_id in page.html):
            return verdict(None, "xhs:note-payload-mobile", fetch, n)
        return None


def _code_in(text: str) -> str | None:
    m = re.search(r"error_code=(\d+)", text or "")
    return m.group(1) if m else None


SPEC = XiaohongshuSpec()
