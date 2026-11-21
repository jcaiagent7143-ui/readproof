"""哔哩哔哩 Bilibili.

Link forms (LinkDigest production, 2026-09 → 2026-11):
  www.bilibili.com/video/BV…/            the address bar (also m.bilibili.com/video/BV…)
  www.bilibili.com/video/av<aid>/        the old numeric id (up to 16 digits: av117614726617753)
  …/zh-Hans/video/BV…, …/s/video/BV…     a language segment or the search-engine page in front
  …?bvid=BV…                             a list, watch-later or festival page playing it
  …?p=N                                  part N of a multi-part video (分P); a one-part video plays P1 for any N
  b23.tv/<code>                          the share button's short link
  player.bilibili.com/player.html?bvid=  Bilibili's own embed player

Canonical is always www.bilibili.com/video/<BV>/ with no language segment: robots.txt (`User-agent: *`,
read 2026-11-21) disallows /zh-Hant/, /en/, /ja/, /es/, /pt/, /id/, /th/ and /ar/.

Wall signatures:
  JSON code -412 / -352 (risk control: the address is refused, not the video)    -> challenge
  HTTP 412, 「由于触发哔哩哔哩安全风控策略」                                       -> challenge
  title 「验证码_哔哩哔哩」, #risk-captcha-app (live 2026-11-21: www.bilibili.com/video/<BV>/ answered the
  ReadProof UA from a residential address with this 1,374-byte captcha page)    -> challenge
  JSON code -404 「啥都木有」 (live 2026-11-21 from api.bilibili.com), 62002 (invisible) -> deleted
  62012 (visible to its uploader only) -> self_visible; 62004 (under review) -> unknown
  -10403 (region) -> region; -101 (not logged in) -> login; -509 / -799 (too frequent) -> rate_limited
"""

from __future__ import annotations

import re

from ..types import FetchResult, PostRef, WallType, WallVerdict
from ._base import Spec, header, host_of, json_of, page_of, param, path_of, verdict

BV = r"BV[0-9A-Za-z]{10}"
_FRONT = r"(/[A-Za-z]{2}(?:-[A-Za-z]{2,4})?)?(/s)?"
_VIDEO_BV = re.compile(rf"^{_FRONT}/video/({BV})(?:/|$)")
_VIDEO_AV = re.compile(rf"^{_FRONT}/video/av(\d{{1,16}})(?:/|$)", re.I)

# Bilibili's own av <-> BV mapping (the 2024 scheme, every aid below 2^51). av170001 is BV17x411w7KC and
# av117614726617753 is BV1wRsV6gEeF (both pinned live by LinkDigest). An id encoding, not a signature.
_XOR = 23442827791579
_MASK = 2251799813685247
_MAX_AID = 1 << 51
_ALPHABET = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"

_CODES: dict[int, tuple[WallType | None, str]] = {
    0: (None, "ok"),
    -412: (WallType.CHALLENGE, "risk-refused"),
    -352: (WallType.CHALLENGE, "risk-check-failed"),
    -404: (WallType.DELETED, "not-found"),
    62002: (WallType.DELETED, "invisible"),
    62012: (WallType.SELF_VISIBLE, "uploader-only"),
    62004: (WallType.UNKNOWN, "under-review"),
    -10403: (WallType.REGION, "region"),
    -101: (WallType.LOGIN, "not-logged-in"),
    -509: (WallType.RATE_LIMITED, "too-frequent"),
    -799: (WallType.RATE_LIMITED, "too-frequent"),
}
_RISK_TEXT = "由于触发哔哩哔哩安全风控策略"
_GONE_TEXT = ("啥都木有", "视频不见了", "视频去哪了呢")


def av_to_bv(aid: int) -> str | None:
    if not 0 < aid < _MAX_AID:
        return None
    out = list("BV1000000000")
    i = len(out) - 1
    t = (_MAX_AID | aid) ^ _XOR
    while t > 0:
        out[i] = _ALPHABET[t % 58]
        t //= 58
        i -= 1
    out[3], out[9] = out[9], out[3]
    out[4], out[7] = out[7], out[4]
    return "".join(out)


def bv_to_av(bvid: str) -> int | None:
    if not re.fullmatch(BV, bvid or "") or any(c not in _ALPHABET for c in bvid[3:]):
        return None
    chars = list(bvid)
    chars[3], chars[9] = chars[9], chars[3]
    chars[4], chars[7] = chars[7], chars[4]
    t = 0
    for c in chars[3:]:
        t = t * 58 + _ALPHABET.index(c)
    aid = (t & _MASK) ^ _XOR
    return aid if 0 < aid < _MAX_AID else None


def part_of(url: str) -> int:
    """?p=N, 1 when absent or nonsense (as LinkDigest's part_from)."""
    raw = (param(url, "p") or "").strip()
    return int(raw) if raw.isdigit() and 1 <= int(raw) <= 9999 else 1


class BilibiliSpec(Spec):
    name = "bilibili"
    domains = ("bilibili.com",)
    short_hosts = ("b23.tv",)

    def parse(self, url: str) -> PostRef | None:
        if not self.match(url) or self.is_short(url):
            return None
        host, path = host_of(url), path_of(url)
        part = part_of(url)
        if host == "player.bilibili.com":
            bvid = param(url, "bvid")
            aid = param(url, "aid")
            if not (bvid and re.fullmatch(BV, bvid)) and aid and aid.isdigit():
                bvid = av_to_bv(int(aid))
            if bvid and re.fullmatch(BV, bvid):
                return self.ref(bvid, "embed", url, part=part, aid=bv_to_av(bvid))
            return None
        front, via, bvid = "", "bv", None
        m = _VIDEO_BV.match(path)
        if m:
            front, bvid = (m.group(1) or "") + (m.group(2) or ""), m.group(3)
        else:
            m = _VIDEO_AV.match(path)
            if m:
                front, via, bvid = (m.group(1) or "") + (m.group(2) or ""), "av", av_to_bv(int(m.group(3)))
            else:
                listed = param(url, "bvid")
                if listed and re.fullmatch(BV, listed):
                    via, bvid = "list", listed
        if not bvid:
            return None
        if via == "av":
            form = "av"
        elif host.startswith("m."):
            form = "mobile"
        elif front:
            form = "lang_prefix"
        elif via == "list":
            form = "list_bvid"
        elif part > 1:
            form = "part"
        else:
            form = "desktop"
        return self.ref(bvid, form, url, part=part, aid=bv_to_av(bvid), prefix=front or None)

    def page_kind(self, url: str) -> str | None:
        host, path = host_of(url), path_of(url).rstrip("/")
        if host.startswith("space."):
            return "profile"
        if host.startswith("live."):
            return "live"
        if host.startswith("search."):
            return "search"
        if path in ("", "/index.html"):
            return "home"
        for prefix, kind in (("/bangumi", "bangumi"), ("/read", "article"), ("/list", "listing"),
                             ("/medialist", "listing"), ("/festival", "listing"), ("/v/popular", "listing"),
                             ("/video", "video_without_id")):
            if path == prefix or path.startswith(prefix + "/"):
                return kind
        return None

    def canonical(self, ref: PostRef) -> str:
        part = int(ref.extra.get("part") or 1)
        return f"https://www.bilibili.com/video/{ref.post_id}/" + (f"?p={part}" if part > 1 else "")

    def forms(self, ref: PostRef, **known: str) -> list[tuple[str, str]]:
        bv = ref.post_id
        part = int(ref.extra.get("part") or known.get("part") or 1)
        p = f"?p={part}" if part > 1 else ""
        out = [
            ("desktop", f"https://www.bilibili.com/video/{bv}/{p}"),
            ("mobile", f"https://m.bilibili.com/video/{bv}{p}"),
        ]
        aid = bv_to_av(bv)
        if aid:
            out.append(("av", f"https://www.bilibili.com/video/av{aid}/{p}"))
        out.append(("part", f"https://www.bilibili.com/video/{bv}/?p={part}"))
        share = f"p={part}&share_source=copy_web" if part > 1 else "share_source=copy_web"
        out.append(("share_param", f"https://www.bilibili.com/video/{bv}/?{share}"))
        link = known.get("short") or out[0][1]
        text = f"【{known.get('title') or '标题'}-哔哩哔哩】 {link}"
        return self.with_short(out, known, text)

    def embed_url(self, ref: PostRef) -> str | None:
        part = int(ref.extra.get("part") or 1)
        return f"https://player.bilibili.com/player.html?bvid={ref.post_id}" + (f"&p={part}" if part > 1 else "") + "&autoplay=0"

    def judge(self, fetch: FetchResult, visible_text: str) -> WallVerdict | None:
        n = len(visible_text)
        data = json_of(fetch)
        code: int | None = None
        said = ""
        if isinstance(data, dict) and isinstance(data.get("code"), int):
            code, said = data["code"], str(data.get("message") or "")
        else:
            raw = header(fetch, "bili-status-code").strip()
            if re.fullmatch(r"-?\d+", raw):
                code = int(raw)
        if code is not None and (code != 0 or (isinstance(data, dict) and data.get("data"))):
            wall, rule = _CODES.get(code, (WallType.UNKNOWN, "code"))
            return verdict(wall, f"bilibili:{rule}", fetch, n, marker=f"code={code}", said=said or None)
        page = page_of(fetch)
        if fetch.status == 412 or _RISK_TEXT in page.html:
            return verdict(WallType.CHALLENGE, "bilibili:412", fetch, n,
                           marker=_RISK_TEXT if _RISK_TEXT in page.html else "http-412")
        if page.title.startswith("验证码") or 'id="risk-captcha-app"' in page.html or "_riskdata_" in page.html:
            marker = page.title if page.title.startswith("验证码") else "risk-captcha-app"
            return verdict(WallType.CHALLENGE, "bilibili:risk-captcha", fetch, n, marker=marker)
        if n < 2000:
            gone = next((m for m in _GONE_TEXT if m in visible_text), None)
            if gone:
                return verdict(WallType.DELETED, "bilibili:gone", fetch, n, marker=gone)
        asked = self.parse(fetch.url)
        if asked and "__INITIAL_STATE__" in page.html and f'"bvid":"{asked.post_id}"' in page.html:
            return verdict(None, "bilibili:initial-state", fetch, n)
        return None


SPEC = BilibiliSpec()
