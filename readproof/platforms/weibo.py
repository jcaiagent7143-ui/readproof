"""微博 Weibo.

Link forms (LinkDigest production, 2026-11):
  weibo.com/<uid>/<bid>              the desktop address bar (bid: base62, e.g. RlylI8KlM)
  weibo.com/status/<bid|mid>, weibo.com/detail/<mid>
  m.weibo.cn/detail/<mid>            what a share link opens (mid: 16 digits, e.g. 5351369302084798)
  m.weibo.cn/status/<bid|mid>        what weibo.com sends a phone to
  weibo.com/?layerid=<mid>           a post opened as a layer over the feed
  video.weibo.com/show?fid=<oid>, h5.video.weibo.com/show/<oid>, weibo.com/tv/show/<oid>
                                     a video page; the post behind it is named only in the page's data
  t.cn/<code>                        Weibo's short-link service

A post is keyed by its bid; the mid is derived from it (Weibo's base62.js: 7-digit groups from the right,
each written in base62 and padded to four characters except the leftmost — the padding is the part that is
easy to miss: 5351093540225587 is RlraW0WGv, not RlraWWGv).

Wall signatures:
  302 to passport.weibo.com/visitor/visitor?… and the page titled "Sina Visitor System" (live 2026-11-21:
  weibo.com/<uid>/<bid> answered the ReadProof UA with exactly that; the page collects a device fingerprint
  to issue a guest session)                                                    -> challenge
  passport.weibo.com/sso/…, passport.weibo.cn/signin/…, weibo.com/login.php      -> login
  「该微博不存在」「此微博已被删除」                                               -> deleted
  「由于作者设置，你暂时没有这条微博的查看权限」                                     -> self_visible
  JSON {"ok": -100} (sign-in required) -> login; errno 100015 「不合法的请求」 -> challenge
"""

from __future__ import annotations

import re

from ..types import FetchResult, PostRef, WallType, WallVerdict
from ._base import Spec, host_of, json_of, landing, on_domain, page_of, param, path_of, verdict

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_MID = re.compile(r"\d{12,20}")
_BID = re.compile(r"(?=[0-9A-Za-z]*[A-Za-z])[0-9A-Za-z]{7,10}")
_OID = re.compile(r"\b(\d{3,5}:\d{12,20})\b")
_PAGE_WORDS = {"detail", "status", "u", "tv", "show", "p", "profile", "n", "api", "s", "comment", "repost"}
_GONE = ("该微博不存在", "此微博已被删除", "此微博已被作者删除", "微博不存在")
_SELF_ONLY = ("由于作者设置", "没有这条微博的查看权限")
_LOGIN_TEXT = ("请先登录", "登录查看")


def _b62(n: int) -> str:
    out = ""
    while n:
        out = _ALPHABET[n % 62] + out
        n //= 62
    return out or "0"


def mid_to_bid(mid: str) -> str:
    groups = [mid[max(0, i - 7):i] for i in range(len(mid), 0, -7)]
    out = ""
    for index, group in enumerate(reversed(groups)):
        code = _b62(int(group))
        out += code.rjust(4, "0") if index else code
    return out


def bid_to_mid(bid: str) -> str:
    groups = [bid[max(0, i - 4):i] for i in range(len(bid), 0, -4)]
    out = ""
    for index, group in enumerate(reversed(groups)):
        number = 0
        for ch in group:
            number = number * 62 + _ALPHABET.index(ch)
        out += str(number).rjust(7, "0") if index else str(number)
    return out


class WeiboSpec(Spec):
    name = "weibo"
    domains = ("weibo.com", "weibo.cn")
    short_hosts = ("t.cn",)

    def _video_oid(self, url: str) -> str | None:
        from urllib.parse import unquote

        for key in ("fid", "object_id"):
            value = param(url, key)
            if value:
                m = _OID.search(unquote(value))
                if m:
                    return m.group(1)
        path = path_of(url)
        if "/show/" in path:
            m = _OID.search(unquote(path))
            if m:
                return m.group(1)
        return None

    def parse(self, url: str) -> PostRef | None:
        if not self.match(url) or self.is_short(url):
            return None
        host, path = host_of(url), path_of(url)
        layer = param(url, "layerid")
        segments = [s for s in path.split("/") if s]
        ref, uid, form = None, None, None
        if layer and _MID.fullmatch(layer):
            ref, form = layer, "layer"
        elif len(segments) >= 2 and segments[-2] in ("detail", "status") and len(segments) <= 3:
            ref = segments[-1]
            mobile = host in ("m.weibo.cn", "weibo.cn") or host.startswith("m.")
            form = ("mobile_" if mobile else "desktop_") + segments[-2]
        elif len(segments) == 2 and segments[0].isdigit() and segments[0] not in _PAGE_WORDS:
            uid, ref, form = segments[0], segments[1], "desktop"
        if ref is None:
            oid = self._video_oid(url)
            if oid:
                return self.ref(f"video:{oid}", "video_page", url, oid=oid, kind="video_page")
            return None
        if _MID.fullmatch(ref):
            mid, bid = ref, mid_to_bid(ref)
        elif _BID.fullmatch(ref) and all(c in _ALPHABET for c in ref):
            bid, mid = ref, bid_to_mid(ref)
        else:
            return None
        return self.ref(bid, form, url, mid=mid, bid=bid, uid=uid)

    def page_kind(self, url: str) -> str | None:
        host, path = host_of(url), path_of(url).rstrip("/")
        if on_domain(host, "passport.weibo.com", "passport.weibo.cn", "login.sina.com.cn"):
            return "login"
        if path == "":
            return "home"
        if path.startswith("/u/") or re.fullmatch(r"/\d{5,12}", path):
            return "profile"
        if path.startswith(("/hot", "/search", "/ajax")) or host.startswith("s."):
            return "listing"
        return None

    def canonical(self, ref: PostRef) -> str:
        if ref.extra.get("kind") == "video_page":
            return f"https://h5.video.weibo.com/show/{ref.extra['oid']}"
        return f"https://m.weibo.cn/detail/{ref.extra.get('mid') or bid_to_mid(ref.post_id)}"

    def forms(self, ref: PostRef, **known: str) -> list[tuple[str, str]]:
        if ref.extra.get("kind") == "video_page":
            oid = ref.extra["oid"]
            out = [("video_page", f"https://video.weibo.com/show?fid={oid}"),
                   ("h5_video_page", f"https://h5.video.weibo.com/show/{oid}")]
        else:
            bid = ref.post_id
            mid = ref.extra.get("mid") or bid_to_mid(bid)
            uid = ref.extra.get("uid") or known.get("uid")
            out = []
            if uid:
                out.append(("desktop", f"https://weibo.com/{uid}/{bid}"))
            out += [
                ("desktop_status", f"https://weibo.com/status/{bid}"),
                ("mobile_detail", f"https://m.weibo.cn/detail/{mid}"),
                ("mobile_status", f"https://m.weibo.cn/status/{bid}"),
            ]
        link = known.get("short") or out[0][1]
        author = known.get("author") or "博主"
        text = f"{known.get('title') or '…'} 分享自@{author}的微博 {link}"
        return self.with_short(out, known, text)

    def judge(self, fetch: FetchResult, visible_text: str) -> WallVerdict | None:
        n = len(visible_text)
        land = landing(fetch)
        lhost, lpath = host_of(land), path_of(land)
        page = page_of(fetch)
        if (on_domain(lhost, "passport.weibo.com", "passport.weibo.cn") and lpath.startswith("/visitor")) \
                or page.title == "Sina Visitor System":
            return verdict(WallType.CHALLENGE, "weibo:visitor-system", fetch, n, marker="Sina Visitor System")
        if (on_domain(lhost, "passport.weibo.com", "passport.weibo.cn") and lpath.startswith(("/sso", "/signin")))\
                or on_domain(lhost, "login.sina.com.cn") or lpath.startswith("/login.php"):
            return verdict(WallType.LOGIN, "weibo:sign-in", fetch, n, marker=f"{lhost}{lpath}"[:60])
        data = json_of(fetch)
        if isinstance(data, dict) and "ok" in data:
            if data.get("ok") == -100:
                return verdict(WallType.LOGIN, "weibo:api-sign-in", fetch, n, marker="ok=-100")
            if str(data.get("errno") or "") == "100015":
                return verdict(WallType.CHALLENGE, "weibo:api-refused", fetch, n, marker="errno=100015",
                               said=str(data.get("msg") or "") or None)
            if data.get("ok") == 1 and data.get("data"):
                return verdict(None, "weibo:api-ok", fetch, n)
        if "$render_data" in page.html and '"status"' in page.html:
            return verdict(None, "weibo:render-data", fetch, n)
        if n < 2000:
            for markers, wall, rule in ((_GONE, WallType.DELETED, "weibo:gone"),
                                        (_SELF_ONLY, WallType.SELF_VISIBLE, "weibo:author-setting"),
                                        (_LOGIN_TEXT, WallType.LOGIN, "weibo:login-text")):
                hit = next((m for m in markers if m in visible_text), None)
                if hit:
                    return verdict(wall, rule, fetch, n, marker=hit)
        return None


SPEC = WeiboSpec()
