"""TikTok.

Link forms: www.tiktok.com/@<user>/video/<id>, /@<user>/photo/<id> (a picture post), m.tiktok.com/v/<id>.html,
vm.tiktok.com/<code>/ and vt.tiktok.com/<code>/ (the app's short links), www.tiktok.com/t/<code>/, and the
embed players /embed/v2/<id> and /player/v1/<id>. A short link may open a TikTok Shop product page
(/view/product/<id>), which is not a post (LinkDigest 2026-11-14).

Live 2026-11-21 (ReadProof UA, residential): /@scout2015/video/6718335390845095173 answered 200 with a
519,776-byte page whose only visible text is its title "TikTok - Make Your Day" — and the post in
<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"> (webapp.video-detail.statusCode 0, itemStruct.id set).
So an app-shell title with no visible text is a served post here, not an empty page, and the script
mentions "captcha" 34 times, so no raw-HTML captcha marker may be trusted on this site.

robots.txt (`User-agent: *`, 2026-11-21) disallows /embed/v2, /embed/@, /link, /auth, /search? and
/shop/view/product/: the embed player is not fetchable under P0; /@user/video/ and /oembed are.

statusCode values (webapp.video-detail): 0 served; 10204 not found -> deleted; 10216 private -> self_visible;
others -> unknown (from public extractor knowledge, not confirmed live).
"""

from __future__ import annotations

import re
from urllib.parse import quote

from ..types import FetchResult, PostRef, WallType, WallVerdict
from ._base import Spec, bytedance_challenge, host_of, landing, on_domain, page_of, path_of, script_json, verdict

_ID = r"\d{15,21}"
_USER_POST = re.compile(rf"^/@([^/?#]*)/(video|photo)/({_ID})(?:/|$)")
_MOBILE = re.compile(rf"^/v/({_ID})(?:\.html)?(?:/|$)")
_EMBED = re.compile(rf"^/(?:embed(?:/v2)?|player/v1)/({_ID})(?:/|$)")
_T_SHORT = re.compile(r"^/t/[A-Za-z0-9_-]+/?$")
_STATUS_WALLS = {10204: (WallType.DELETED, "not-found"), 10216: (WallType.SELF_VISIBLE, "private")}


class TikTokSpec(Spec):
    name = "tiktok"
    domains = ("tiktok.com",)
    short_hosts = ("vm.tiktok.com", "vt.tiktok.com")

    def is_short(self, url: str) -> bool:
        return super().is_short(url) or (on_domain(host_of(url), "tiktok.com") and bool(_T_SHORT.match(path_of(url))))

    def parse(self, url: str) -> PostRef | None:
        if not self.match(url) or self.is_short(url):
            return None
        host, path = host_of(url), path_of(url)
        m = _USER_POST.match(path)
        if m:
            user, kind, pid = m.groups()
            if kind == "photo":
                form = "photo"
            elif host.startswith("m."):
                form = "mobile"
            elif "is_from_webapp" in url or "sender_device" in url:
                form = "share_param"
            else:
                form = "desktop"
            return self.ref(pid, form, url, user=user or None, kind=kind)
        m = _MOBILE.match(path)
        if m:
            return self.ref(m.group(1), "mobile", url)
        m = _EMBED.match(path)
        if m:
            return self.ref(m.group(1), "embed", url)
        return None

    def page_kind(self, url: str) -> str | None:
        path = path_of(url).rstrip("/")
        if path == "":
            return "home"
        if path.startswith(("/view/product", "/shop")):
            return "product"
        if path.startswith("/@") and path.endswith("/live"):
            return "live"
        if path.startswith("/@") and path.count("/") == 1:
            return "profile"
        for prefix, kind in (("/foryou", "listing"), ("/explore", "listing"), ("/discover", "listing"),
                             ("/search", "search"), ("/music", "music"), ("/tag", "topic"), ("/live", "live")):
            if path == prefix or path.startswith(prefix + "/"):
                return kind
        return None

    def canonical(self, ref: PostRef) -> str:
        user, kind = ref.extra.get("user"), ref.extra.get("kind") or "video"
        if user:
            return f"https://www.tiktok.com/@{user}/{kind}/{ref.post_id}"
        return f"https://m.tiktok.com/v/{ref.post_id}.html"

    def forms(self, ref: PostRef, **known: str) -> list[tuple[str, str]]:
        pid = ref.post_id
        user = ref.extra.get("user") or known.get("user")
        kind = ref.extra.get("kind") or known.get("kind") or "video"
        out: list[tuple[str, str]] = []
        if user:
            out.append(("desktop", f"https://www.tiktok.com/@{user}/{kind}/{pid}"))
            out.append(("share_param", f"https://www.tiktok.com/@{user}/{kind}/{pid}?is_from_webapp=1&sender_device=pc"))
        out.append(("mobile", f"https://m.tiktok.com/v/{pid}.html"))
        link = known.get("short") or out[0][1]
        who = f"@{user}'s" if user else "this"
        text = f"Check out {who} video! #TikTok > {link}"
        return self.with_short(out, known, text)

    def embed_url(self, ref: PostRef) -> str | None:
        # The player TikTok's own oEmbed response advertises (2026-11-21). robots.txt disallows /embed/v2 for
        # `User-agent: *` but not /player/v1, and on a live read /embed/v2 never started media while /player/v1 did.
        return f"https://www.tiktok.com/player/v1/{ref.post_id}?autoplay=1&muted=1"

    def oembed_url(self, url: str) -> str | None:
        ref = self.parse(url)
        if ref is None:
            return None
        return f"https://www.tiktok.com/oembed?url={quote(self.canonical(ref), safe='')}"

    def judge(self, fetch: FetchResult, visible_text: str) -> WallVerdict | None:
        n = len(visible_text)
        lpath = path_of(landing(fetch))
        if lpath.startswith(("/view/product", "/shop/")):
            return verdict(WallType.UNSUPPORTED, "tiktok:product-page", fetch, n, marker=lpath[:40])
        page = page_of(fetch)
        data = script_json(page.html, "__UNIVERSAL_DATA_FOR_REHYDRATION__")
        if isinstance(data, dict):
            detail = (data.get("__DEFAULT_SCOPE__") or {}).get("webapp.video-detail")
            if isinstance(detail, dict):
                code = detail.get("statusCode")
                item = (detail.get("itemInfo") or {}).get("itemStruct") or {}
                if code == 0 and item.get("id"):
                    return verdict(None, "tiktok:item", fetch, n, item=item.get("id"))
                if isinstance(code, int) and code != 0:
                    wall, rule = _STATUS_WALLS.get(code, (WallType.UNKNOWN, "status-code"))
                    return verdict(wall, f"tiktok:{rule}", fetch, n, marker=f"statusCode={code}",
                                   said=str(detail.get("statusMsg") or "") or None)
        return bytedance_challenge(fetch, page.html, n, "tiktok")


SPEC = TikTokSpec()
