"""微信公众号 WeChat Official Account articles.

Link forms: mp.weixin.qq.com/s/<id> (what 复制链接 gives) and the long form
mp.weixin.qq.com/s?__biz=<biz>&mid=<mid>&idx=<idx>&sn=<sn>[&chksm=…&scene=…#rd] (older shares, search results,
the 阅读原文 chain). The two forms of one article cannot be mapped to each other without a fetch, so each is
its own post key. ``sn`` and ``chksm`` are values WeChat put in the link; they are kept as given, never made.

LinkDigest (2026-10-12/27): the long form is answered with a 302 to /mp/wappoc_appmsgcaptcha (a captcha,
never answered) while the short form of the same article reads.

robots.txt (mp.weixin.qq.com, `User-agent: *`, 2026-11-21): Disallow: / (articles are not fetchable under P0).

Wall signatures:
  /mp/wappoc_appmsgcaptcha, 「环境异常」「完成验证后即可继续访问」          -> challenge
  「该内容已被发布者删除」「此内容因违规无法查看」「此内容被投诉且经审核涉嫌侵权」 -> deleted
  「参数错误」 as the page's message (live 2026-11-21: a made-up /s/<id> answered a 32,067-byte page with no
  <title> whose message box reads 参数错误)                                  -> deleted (no such article)
  「链接已过期」 -> token; 「访问过于频繁」 -> rate_limited
"""

from __future__ import annotations

import re

from ..types import FetchResult, PostRef, WallType, WallVerdict
from ._base import Spec, landing, page_of, param, path_of, raw_param, unquote_once, verdict

_S_ID = re.compile(r"^/s/([A-Za-z0-9_-]{6,})/?$")
_CHALLENGE = ("环境异常", "完成验证后即可继续访问")
_DELETED = ("该内容已被发布者删除", "此内容因违规无法查看", "此内容被投诉且经审核涉嫌侵权", "此内容发送失败无法查看")
_ERROR_BOX = re.compile(r'class="weui-msg__title[^"]*"[^>]*>\s*([^<]{1,40}?)\s*<')


class WeChatSpec(Spec):
    name = "wechat"
    domains = ("mp.weixin.qq.com",)

    def parse(self, url: str) -> PostRef | None:
        if not self.match(url):
            return None
        path = path_of(url)
        m = _S_ID.match(path)
        if m:
            return self.ref(m.group(1), "s_path", url)
        if path.rstrip("/") in ("/s", "/mp/appmsg/show"):
            biz = unquote_once(raw_param(url, "__biz"))  # base64: decode %3D once, keep a literal "+"
            mid = param(url, "mid") or param(url, "appmsgid")
            idx = param(url, "idx") or param(url, "itemidx")
            if biz and mid and idx:
                return self.ref(f"{biz}:{mid}:{idx}", "long_query", url, biz=biz, mid=mid, idx=idx,
                                sn=param(url, "sn") or param(url, "sign"))
        return None

    def page_kind(self, url: str) -> str | None:
        path = path_of(url).rstrip("/")
        if path == "":
            return "home"
        if path.startswith("/mp/profile_ext") or path.startswith("/mp/homepage"):
            return "profile"
        return None

    def canonical(self, ref: PostRef) -> str:
        if ref.form == "s_path" or "biz" not in ref.extra:
            return f"https://mp.weixin.qq.com/s/{ref.post_id}"
        e = ref.extra
        tail = f"&sn={e['sn']}" if e.get("sn") else ""
        return f"https://mp.weixin.qq.com/s?__biz={e['biz']}&mid={e['mid']}&idx={e['idx']}{tail}"

    def forms(self, ref: PostRef, **known: str) -> list[tuple[str, str]]:
        out = [(ref.form if ref.form in ("s_path", "long_query") else "s_path", self.canonical(ref))]
        other = known.get("long") if ref.form == "s_path" else known.get("s")
        if other:
            out.append(("long_query" if ref.form == "s_path" else "s_path", other))
        text = f"{known.get('title') or '…'} {out[0][1]}"
        return self.with_short(out, known, text)

    def judge(self, fetch: FetchResult, visible_text: str) -> WallVerdict | None:
        n = len(visible_text)
        lpath = path_of(landing(fetch))
        if lpath.startswith("/mp/wappoc_appmsgcaptcha"):
            return verdict(WallType.CHALLENGE, "wechat:captcha", fetch, n, marker="/mp/wappoc_appmsgcaptcha")
        page = page_of(fetch)
        if 'id="js_content"' in page.html and n >= 200:
            return verdict(None, "wechat:article", fetch, n)
        if n >= 3000:
            return None
        for markers, wall, rule in ((_CHALLENGE, WallType.CHALLENGE, "wechat:environment-check"),
                                    (_DELETED, WallType.DELETED, "wechat:removed")):
            hit = next((m for m in markers if m in visible_text), None)
            if hit:
                return verdict(wall, rule, fetch, n, marker=hit)
        box = _ERROR_BOX.search(page.html)
        message = box.group(1).strip() if box else visible_text[:12]
        if message.startswith("参数错误"):
            return verdict(WallType.DELETED, "wechat:param-error", fetch, n, marker="参数错误")
        if "链接已过期" in message or "链接已过期" in visible_text[:200]:
            return verdict(WallType.TOKEN, "wechat:link-expired", fetch, n, marker="链接已过期")
        if "访问过于频繁" in visible_text:
            return verdict(WallType.RATE_LIMITED, "wechat:too-frequent", fetch, n, marker="访问过于频繁")
        return None


SPEC = WeChatSpec()
