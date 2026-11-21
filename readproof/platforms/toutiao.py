"""今日头条 Toutiao.

Link forms (LinkDigest production, 2026-11-20): the app's share sheet hands out m.toutiao.com/group/<id>/ and
/item/<id>/ (and the short m.toutiao.com/is/<code>/, whose Location is the /group/ form); the site shows
www.toutiao.com/article/<id>/ or /video/<id>/; 微头条 posts are /w/<id>/; older forms are /a<id>/, /i<id>/ and
?group_id=<id>.

robots.txt (www.toutiao.com, `User-agent: *`, read live 2026-11-21) disallows /group/ and /item/ (and /search,
/trending/, /m1…/m8) and allows /article/, /video/ and /w/. So every form is rewritten to
https://www.toutiao.com/article/<id>/ from the id alone, and the rewrite never needs a /group/ or /item/ hop:
resolve() reads a short link's Location header and stops there. /article/<id>/ sends a video id on to
/video/<id>/ by itself (LinkDigest 2026-11-20).

Wall signature: live 2026-11-21 a plain fetch of www.toutiao.com/article/<id>/ with the ReadProof UA got the
ByteDance verification page — 72,914 bytes, 0 visible characters, byte-identical to Douyin's (-> challenge).
A browser that runs it gets #RENDER_DATA (URL-encoded JSON) with the post (-> served). 「页面不存在」
「文章不存在」「内容已被删除」 -> deleted.
"""

from __future__ import annotations

import re

from ..types import FetchResult, PostRef, WallType, WallVerdict
from ._base import Spec, bytedance_challenge, host_of, on_domain, page_of, param, path_of, verdict

_ID = r"\d{15,20}"
_ID_IN_PATH = re.compile(rf"^/(group|item|article|video|w)/({_ID})(?:[/?#]|$)|^/([ai])({_ID})(?:[/?#]|$)")
_SHORT_PATH = re.compile(r"^/is/[A-Za-z0-9_-]+/?$")
_FORMS = {"group": "group", "item": "item", "article": "article", "video": "video", "w": "weitoutiao", "a": "a_path"}
_GONE = ("页面不存在", "文章不存在", "内容已被删除", "该内容已删除", "内容不存在")


class ToutiaoSpec(Spec):
    name = "toutiao"
    domains = ("toutiao.com",)

    def is_short(self, url: str) -> bool:
        return on_domain(host_of(url), *self.domains) and bool(_SHORT_PATH.match(path_of(url)))

    def parse(self, url: str) -> PostRef | None:
        if not self.match(url) or self.is_short(url):
            return None
        host, path = host_of(url), path_of(url)
        m = _ID_IN_PATH.match(path)
        if m:
            if m.group(1):
                segment, pid = m.group(1), m.group(2)
                form = _FORMS[segment]
            else:
                segment, pid = m.group(3), m.group(4)
                form = "a_path" if segment == "a" else ("mobile_i" if host.startswith("m.") else "i_path")
            kind = {"video": "video", "w": "weitoutiao"}.get(segment, "article")
            return self.ref(pid, form, url, kind=kind)
        for key in ("group_id", "item_id", "groupid", "gid"):
            value = param(url, key)
            if value and re.fullmatch(_ID, value):
                return self.ref(value, "query_id", url, kind="article")
        return None

    def page_kind(self, url: str) -> str | None:
        path = path_of(url).rstrip("/")
        if path == "":
            return "home"
        if path.startswith("/c/user"):
            return "profile"
        if path.startswith(("/search", "/trending", "/hot-event", "/ch/")):
            return "listing"
        return None

    def canonical(self, ref: PostRef) -> str:
        return f"https://www.toutiao.com/article/{ref.post_id}/"

    def forms(self, ref: PostRef, **known: str) -> list[tuple[str, str]]:
        pid = ref.post_id
        kind = ref.extra.get("kind") or known.get("kind") or "article"
        out = [
            ("article", f"https://www.toutiao.com/article/{pid}/"),
            ("group", f"https://m.toutiao.com/group/{pid}/"),
            ("item", f"https://www.toutiao.com/item/{pid}/"),
            ("mobile_i", f"https://m.toutiao.com/i{pid}/"),
            ("a_path", f"https://www.toutiao.com/a{pid}/"),
        ]
        if kind == "video":
            out.append(("video", f"https://www.toutiao.com/video/{pid}/"))
        if kind == "weitoutiao":
            out.append(("weitoutiao", f"https://www.toutiao.com/w/{pid}/"))
        link = known.get("short") or out[1][1]
        text = f"{known.get('title') or '…'} - 今日头条 {link}"
        return self.with_short(out, known, text)

    def judge(self, fetch: FetchResult, visible_text: str) -> WallVerdict | None:
        n = len(visible_text)
        page = page_of(fetch)
        challenge = bytedance_challenge(fetch, page.html, n, "toutiao")
        if challenge:
            return challenge
        if re.search(r"<script[^>]*\bid=[\"']RENDER_DATA[\"'][^>]*>\s*\S", page.html):
            return verdict(None, "toutiao:render-data", fetch, n)
        if n < 2000:
            gone = next((m for m in _GONE if m in visible_text), None)
            if gone:
                return verdict(WallType.DELETED, "toutiao:gone", fetch, n, marker=gone)
        return None


SPEC = ToutiaoSpec()
