"""抖音 Douyin.

Link forms (from LinkDigest's production resolver, 2026-09 → 2026-11):
  www.douyin.com/video/<id>, /note/<id> (图文), /slides/<id>       the desktop address bar
  www.iesdouyin.com/share/video/<id>/ (…/share/note/<id>/)        what a v.douyin.com share link opens
  www.douyin.com/discover?modal_id=<id>, /user/<sec_uid>?modal_id=<id>, /?vid=<id>
                                                                 a post opened over a feed or a profile
  open.douyin.com/player/video?vid=<id>                          Douyin's own embed player
  v.douyin.com/<code>/                                           the share button's short link
Videos and 图文 notes share one aweme id space (19 digits), so the id alone names the post.

Short codes are case-sensitive: an unknown code 302s to https://www.douyin.com (confirmed live
2026-11-21 with the ReadProof UA), which is how a link copied with one letter changed reads like a dead one.

The embed player (open.douyin.com/player/video?vid=<id>) is the surface Douyin publishes for other sites;
inside a browser it requests the post's detail itself. Its two refusals look alike and are not:
``filter_reason: status_self_see`` (作品不见了) means nobody but the author can see the post;
``core_dep`` is answered for live posts it declines *and* for ids that never existed, so it proves nothing
about the post (LinkDigest 2026-10-21, 2026-11-21).
"""

from __future__ import annotations

import re

from ..types import FetchResult, PostRef, WallType, WallVerdict
from ._base import Spec, bytedance_challenge, host_of, json_of, page_of, param, path_of, verdict

_ID = r"\d{15,20}"
_POST_PATH = re.compile(rf"^/(?:(share)/)?(video|note|slides)/({_ID})(?:/|$)")
_ID_PARAMS = ("modal_id", "vid", "aweme_id")
# Douyin's own words for a post that is gone, on its web pages and in its player.
_GONE = ("你要观看的视频不存在", "该作品已删除", "作品不存在", "作品已删除", "视频不存在")  # most specific first
_PLAYER_CANNOT_PLAY = "当前内容无法播放"


class DouyinSpec(Spec):
    name = "douyin"
    domains = ("douyin.com", "iesdouyin.com")
    short_hosts = ("v.douyin.com",)

    def parse(self, url: str) -> PostRef | None:
        if not self.match(url) or self.is_short(url):
            return None
        host, path = host_of(url), path_of(url)
        m = _POST_PATH.match(path)
        if m:
            share, kind, pid = m.groups()
            form = "share_page" if (share or host.endswith("iesdouyin.com")) else "desktop"
            return self.ref(pid, form, url, kind="note" if kind in ("note", "slides") else "video")
        if host == "open.douyin.com" and path.startswith("/player/video"):
            vid = param(url, "vid")
            if vid and re.fullmatch(_ID, vid):
                return self.ref(vid, "embed", url)
            return None
        for name in _ID_PARAMS:
            value = param(url, name)
            if value and re.fullmatch(_ID, value):
                return self.ref(value, "modal", url, via=name)
        return None

    def page_kind(self, url: str) -> str | None:
        host, path = host_of(url), path_of(url).rstrip("/")
        if host == "live.douyin.com" or path.startswith(("/share/live", "/live")):
            return "live"
        if path in ("", "/"):
            return "home"
        for prefix, kind in (("/user", "profile"), ("/share/user", "profile"), ("/music", "music"),
                             ("/share/music", "music"), ("/collection", "collection"), ("/mix", "collection"),
                             ("/share/mix", "collection"), ("/share/collection", "collection"),
                             ("/shipin", "topic"), ("/search", "search"), ("/article", "article")):
            if path == prefix or path.startswith(prefix + "/"):
                return kind
        if path in ("/discover", "/jingxuan", "/hot", "/follow", "/foryou", "/channel", "/recommend"):
            return "listing"
        return None

    def canonical(self, ref: PostRef) -> str:
        segment = "note" if ref.extra.get("kind") == "note" else "video"
        return f"https://www.douyin.com/{segment}/{ref.post_id}"

    def forms(self, ref: PostRef, **known: str) -> list[tuple[str, str]]:
        segment = "note" if (ref.extra.get("kind") or known.get("kind")) == "note" else "video"
        pid = ref.post_id
        out = [
            ("desktop", f"https://www.douyin.com/{segment}/{pid}"),
            ("share_page", f"https://www.iesdouyin.com/share/{segment}/{pid}/"),
            ("modal", f"https://www.douyin.com/discover?modal_id={pid}"),
        ]
        link = known.get("short") or out[0][1]
        author = known.get("author") or "某某"
        title = known.get("title") or ""
        # The app's copy-link text: a share-code float, the author, the title, the link, then the instruction.
        text = f"7.43 复制打开抖音，看看【{author}的作品】{title} {link} 复制此链接，打开Dou音搜索，直接观看视频！"
        return self.with_short(out, known, text)

    def embed_url(self, ref: PostRef) -> str | None:
        return f"https://open.douyin.com/player/video?vid={ref.post_id}&autoplay=0"

    def judge(self, fetch: FetchResult, visible_text: str) -> WallVerdict | None:
        n = len(visible_text)
        data = json_of(fetch)
        if isinstance(data, dict):
            detail = data.get("aweme_detail")
            if isinstance(detail, dict) and detail:
                return verdict(None, "douyin:aweme-detail", fetch, n)
            filtered = data.get("filter_detail")
            if isinstance(filtered, dict) and filtered:
                reason = str(filtered.get("filter_reason") or "").strip()
                said = str(filtered.get("detail_msg") or filtered.get("notice") or "").strip()
                if reason == "status_self_see":
                    return verdict(WallType.SELF_VISIBLE, "douyin:filter", fetch, n, marker=reason, said=said)
                # core_dep (or any other reason): the player declines; ids that never existed answer the same.
                return verdict(WallType.UNKNOWN, "douyin:player-declined", fetch, n,
                               marker=reason or "no-reason", said=said)
        html = page_of(fetch).html if fetch.body else ""
        if fetch.status == 403 and "ArgusSecurityPlugin" in html:
            return verdict(WallType.CHALLENGE, "douyin:security-refusal", fetch, n, marker="ArgusSecurityPlugin")
        challenge = bytedance_challenge(fetch, html, n, "douyin")
        if challenge:
            return challenge
        title = page_of(fetch).title if fetch.body else ""
        if title.startswith("验证码中间页"):
            return verdict(WallType.CHALLENGE, "douyin:verify-page", fetch, n, marker=title[:20])
        if _PLAYER_CANNOT_PLAY in visible_text:
            # 「当前内容无法播放，你可以前往抖音查看更多内容」: the player sends the viewer to the app.
            return verdict(WallType.APP_ONLY, "douyin:player-cannot-play", fetch, n, marker=_PLAYER_CANNOT_PLAY)
        if n < 2000:
            gone = next((m for m in _GONE if m in visible_text), None)
            if gone:
                return verdict(WallType.DELETED, "douyin:gone", fetch, n, marker=gone)
        return None


SPEC = DouyinSpec()
