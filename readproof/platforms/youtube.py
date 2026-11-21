"""YouTube.

Link forms: www.youtube.com/watch?v=<id>, m.youtube.com/watch?v=<id>, youtu.be/<id> (the share button's
form, usually with ?si=<share token>), /shorts/<id>, /live/<id>, /embed/<id>, youtube-nocookie.com/embed/<id>.
An id is 11 characters of [A-Za-z0-9_-], so youtu.be needs no expansion: the id is in the address.

``si`` is a per-share token, so a ?si= form is produced only when the share button's value is passed in.

Wall signatures (from the watch page's playabilityStatus, which the page carries server-side):
  status OK -> served; ERROR ("Video unavailable", "This video isn't available anymore") -> deleted;
  LOGIN_REQUIRED + "not a bot" ("Sign in to confirm you’re not a bot") -> challenge;
  LOGIN_REQUIRED + private -> self_visible; LOGIN_REQUIRED (age) -> login; UNPLAYABLE + country -> region.
  oEmbed answers 401/403 for private videos and for videos whose owner disabled embedding alike -> unknown.
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote

from ..types import FetchResult, PostRef, WallType, WallVerdict
from ._base import Spec, host_of, landing, on_domain, page_of, param, path_of, verdict

VIDEO_ID = r"[A-Za-z0-9_-]{11}"
_PATH_ID = re.compile(rf"^/(shorts|live|embed|v|e)/({VIDEO_ID})(?:[/?#]|$)")
_PLAYABILITY = re.compile(r'"playabilityStatus"\s*:\s*\{')
_STATUS = re.compile(r'"status"\s*:\s*"([A-Z_]+)"')
_REASON = re.compile(r'"reason"\s*:\s*"((?:[^"\\]|\\.)*)"')
_BOT_CHECK = ("confirm you’re not a bot", "confirm you're not a bot")


class YouTubeSpec(Spec):
    name = "youtube"
    domains = ("youtube.com", "youtube-nocookie.com", "youtu.be")
    short_hosts = ()  # youtu.be carries the id itself
    bare_hosts = ("youtu.be",)

    def parse(self, url: str) -> PostRef | None:
        if not self.match(url):
            return None
        host, path = host_of(url), path_of(url)
        video, form = None, "desktop"
        if host == "youtu.be" or host.endswith(".youtu.be"):
            video = path.strip("/").split("/")[0]
            form = "si" if param(url, "si") else "youtu_be"
        elif path.rstrip("/") == "/watch":
            video = param(url, "v")
            form = "mobile" if host.startswith("m.") else ("music" if host.startswith("music.") else "desktop")
        elif path.startswith("/attribution_link"):
            inner = param(url, "u") or ""
            return self.parse("https://www.youtube.com" + inner) if inner.startswith("/") else None
        else:
            m = _PATH_ID.match(path)
            if m:
                kind, video = m.groups()
                form = {"shorts": "shorts", "live": "live"}.get(kind, "embed")
        if not video or not re.fullmatch(VIDEO_ID, video):
            return None
        return self.ref(video, form, url, si=param(url, "si"), t=param(url, "t"), list=param(url, "list"))

    def page_kind(self, url: str) -> str | None:
        path = path_of(url).rstrip("/")
        if path in ("", "/index"):
            return "home"
        if path.startswith(("/@", "/channel/", "/c/", "/user/")):
            return "profile"
        if path.startswith("/results"):
            return "search"
        if path.startswith("/playlist"):
            return "collection"
        if path.startswith("/feed"):
            return "listing"
        return None

    def canonical(self, ref: PostRef) -> str:
        return f"https://www.youtube.com/watch?v={ref.post_id}"

    def forms(self, ref: PostRef, **known: str) -> list[tuple[str, str]]:
        vid = ref.post_id
        out = [
            ("desktop", f"https://www.youtube.com/watch?v={vid}"),
            ("mobile", f"https://m.youtube.com/watch?v={vid}"),
            ("youtu_be", f"https://youtu.be/{vid}"),
            ("shorts", f"https://www.youtube.com/shorts/{vid}"),
        ]
        si = known.get("si") or ref.extra.get("si")
        if si:
            out.append(("si", f"https://youtu.be/{vid}?si={si}"))
        link = known.get("short") or (out[-1][1] if si else out[2][1])
        text = f'Watch "{known.get("title") or "…"}" on YouTube\n{link}'
        return self.with_short(out, known, text)

    def embed_url(self, ref: PostRef) -> str | None:
        return f"https://www.youtube-nocookie.com/embed/{ref.post_id}"

    def oembed_url(self, url: str) -> str | None:
        ref = self.parse(url)
        if ref is None:
            return None
        return f"https://www.youtube.com/oembed?url={quote(self.canonical(ref), safe='')}&format=json"

    def judge(self, fetch: FetchResult, visible_text: str) -> WallVerdict | None:
        n = len(visible_text)
        land = landing(fetch)
        lhost, lpath = host_of(land), path_of(land)
        if on_domain(lhost, "consent.youtube.com", "consent.google.com"):
            return verdict(WallType.UNKNOWN, "youtube:consent-interstitial", fetch, n, marker=lhost)
        if lpath.rstrip("/") == "/oembed" and fetch.status in (401, 403):
            return verdict(WallType.UNKNOWN, "youtube:oembed-unauthorized", fetch, n,
                           marker=f"http-{fetch.status} (private or embedding disabled)")
        page = page_of(fetch)
        m = _PLAYABILITY.search(page.html)
        if m:
            # The status is the object's first key; its reason follows before any nested errorScreen/messages.
            block = page.html[m.end():m.end() + 1500]
            block = re.split(r'"(?:errorScreen|messages|contextParams)"', block, maxsplit=1)[0]
            status = (_STATUS.search(block) or [None, ""])[1]
            reason_raw = (_REASON.search(block) or [None, ""])[1]
            try:
                reason = json.loads(f'"{reason_raw}"')
            except ValueError:
                reason = reason_raw
            low = reason.lower()
            if status == "OK":
                return verdict(None, "youtube:playable", fetch, n)
            if any(b in low for b in _BOT_CHECK):
                return verdict(WallType.CHALLENGE, "youtube:bot-check", fetch, n, marker=reason[:80])
            if "private" in low:
                return verdict(WallType.SELF_VISIBLE, "youtube:private", fetch, n, marker=reason[:80])
            if status in ("LOGIN_REQUIRED", "AGE_CHECK_REQUIRED", "AGE_VERIFICATION_REQUIRED", "CONTENT_CHECK_REQUIRED"):
                return verdict(WallType.LOGIN, "youtube:login-required", fetch, n, marker=f"{status}: {reason[:60]}")
            if "country" in low or "region" in low:
                return verdict(WallType.REGION, "youtube:region", fetch, n, marker=reason[:80])
            if status == "ERROR":
                return verdict(WallType.DELETED, "youtube:unavailable", fetch, n, marker=reason[:80] or status)
            if status:
                return verdict(WallType.UNKNOWN, "youtube:unplayable", fetch, n, marker=f"{status}: {reason[:60]}")
        low_text = visible_text.lower()
        if n < 3000 and any(b in low_text for b in _BOT_CHECK):
            return verdict(WallType.CHALLENGE, "youtube:bot-check", fetch, n, marker="not a bot")
        return None


SPEC = YouTubeSpec()
