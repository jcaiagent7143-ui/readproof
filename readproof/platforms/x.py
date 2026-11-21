"""X (Twitter).

Link forms: x.com/<user>/status/<id>, twitter.com/…, mobile.twitter.com/…, /statuses/<id>, /i/web/status/<id>,
with ?s=20 (desktop web share) or ?s=46&t=<token> (app share; the t= token is per share and never invented),
t.co/<code> (the short link X wraps every link in), platform.twitter.com/embed/Tweet.html?id=<id>.

Live 2026-11-21 (ReadProof UA): x.com/jack/status/20 answered 200 with the post server-rendered — title
'jack on X: "just setting up my twttr" / X' — beside a "Log in or sign up for X" sidebar. So sign-in text on a
page is not a login wall; only a redirect to /i/flow/login (the login interstitial) is.

robots.txt (x.com, `User-agent: *`, 2026-11-21): Disallow: / (Crawl-delay 1). The oEmbed endpoint
(publish.twitter.com) and the embed player (platform.twitter.com) are other hosts.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from ..types import FetchResult, PostRef, WallType, WallVerdict
from ._base import Spec, host_of, json_of, landing, on_domain, page_of, param, path_of, verdict

_STATUS = re.compile(r"^/([A-Za-z0-9_]{1,15})/status(?:es)?/(\d{1,20})(?:[/?#]|$)")
_WEB_STATUS = re.compile(r"^/i/(?:web/)?status/(\d{1,20})(?:[/?#]|$)")
_RESERVED = {"i", "home", "explore", "search", "settings", "notifications", "messages", "hashtag", "intent", "share"}
_LOGIN_PATHS = ("/i/flow/login", "/login", "/i/flow/signup", "/account/access")
_SERVED_TITLE = re.compile(r" on (?:X|Twitter): .+ / (?:X|Twitter)\s*$")
_DELETED = ("this page doesn’t exist", "this page doesn't exist", "post was deleted", "tweet was deleted",
            "account that no longer exists", "this post is unavailable")
_PROTECTED = ("posts are protected", "tweets are protected", "only approved followers", "age-restricted adult content")


class XSpec(Spec):
    name = "x"
    domains = ("x.com", "twitter.com")
    short_hosts = ("t.co",)

    def parse(self, url: str) -> PostRef | None:
        if not self.match(url) or self.is_short(url):
            return None
        host, path = host_of(url), path_of(url)
        if host == "platform.twitter.com" and "/embed/" in path:
            tid = param(url, "id")
            return self.ref(tid, "embed", url) if tid and tid.isdigit() else None
        m = _STATUS.match(path)
        if m and m.group(1).lower() not in _RESERVED:
            user, tid = m.groups()
            if host.startswith("mobile."):
                form = "mobile"
            elif host.endswith("twitter.com"):
                form = "twitter"
            elif param(url, "s"):
                form = "share_param"
            else:
                form = "x"
            return self.ref(tid, form, url, user=user)
        m = _WEB_STATUS.match(path)
        if m:
            return self.ref(m.group(1), "web_status", url)
        return None

    def page_kind(self, url: str) -> str | None:
        path = path_of(url).rstrip("/")
        if path in ("", "/home"):
            return "home"
        if path.startswith(_LOGIN_PATHS):
            return "login"
        if path.startswith(("/explore", "/search", "/hashtag")):
            return "listing"
        if re.fullmatch(r"/[A-Za-z0-9_]{1,15}", path):
            return "profile"
        return None

    def canonical(self, ref: PostRef) -> str:
        user = ref.extra.get("user")
        return f"https://x.com/{user}/status/{ref.post_id}" if user else f"https://x.com/i/web/status/{ref.post_id}"

    def forms(self, ref: PostRef, **known: str) -> list[tuple[str, str]]:
        tid = ref.post_id
        user = ref.extra.get("user") or known.get("user")
        if user:
            out = [
                ("x", f"https://x.com/{user}/status/{tid}"),
                ("twitter", f"https://twitter.com/{user}/status/{tid}"),
                ("mobile", f"https://mobile.twitter.com/{user}/status/{tid}"),
                ("share_param", f"https://x.com/{user}/status/{tid}?s=20"),
            ]
        else:
            out = [("web_status", f"https://x.com/i/web/status/{tid}")]
        link = known.get("short") or out[0][1]
        text = f"{known.get('title') or '…'} {link}"
        return self.with_short(out, known, text)

    def embed_url(self, ref: PostRef) -> str | None:
        return f"https://platform.twitter.com/embed/Tweet.html?id={ref.post_id}"

    def oembed_url(self, url: str) -> str | None:
        ref = self.parse(url)
        if ref is None or not ref.extra.get("user"):
            return None
        # oEmbed was specified for twitter.com addresses; that host is the one it is sure to know.
        post = f"https://twitter.com/{ref.extra['user']}/status/{ref.post_id}"
        return f"https://publish.twitter.com/oembed?url={quote(post, safe='')}"

    def judge(self, fetch: FetchResult, visible_text: str) -> WallVerdict | None:
        n = len(visible_text)
        land = landing(fetch)
        lhost, lpath = host_of(land), path_of(land)
        if on_domain(lhost, *self.domains) and lpath.startswith(_LOGIN_PATHS):
            return verdict(WallType.LOGIN, "x:login-interstitial", fetch, n, marker=lpath[:40])
        if lhost == "publish.twitter.com" or lpath.rstrip("/") == "/oembed":
            if fetch.status == 403:
                return verdict(WallType.LOGIN, "x:oembed-protected", fetch, n, marker="http-403")
            data = json_of(fetch)
            if isinstance(data, dict) and data.get("html"):
                return verdict(None, "x:oembed", fetch, n)
            return None
        page = page_of(fetch)
        if _SERVED_TITLE.search(page.title):
            return verdict(None, "x:post-title", fetch, n)
        low = visible_text.lower()
        if n < 3000:
            hit = next((m for m in _DELETED if m in low), None)
            if hit:
                return verdict(WallType.DELETED, "x:gone", fetch, n, marker=hit)
            hit = next((m for m in _PROTECTED if m in low), None)
            if hit:
                return verdict(WallType.LOGIN, "x:protected", fetch, n, marker=hit)
            if "javascript is not available" in low and n < 600:
                return verdict(WallType.EMPTY, "x:js-shell", fetch, n, marker="JavaScript is not available")
        return None


SPEC = XSpec()
