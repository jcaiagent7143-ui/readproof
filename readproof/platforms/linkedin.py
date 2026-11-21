"""LinkedIn: parsed so a link is named, never read.

DESIGN §1 rule 8: LinkedIn is refused under every profile (reason ``terms``). Its robots.txt disallows every
path for agents it has not whitelisted and its user agreement prohibits automated access; LinkDigest refused
it before any adapter ran from 2026-11-21. This spec exists so that a pasted LinkedIn post is identified
(post key, link form) and the gate's refusal is reported against the right post; it offers no embed, no oEmbed
and a single form. lnkd.in is not matched: it redirects anywhere, and the gate judges where it lands.
"""

from __future__ import annotations

import re

from ..types import FetchResult, PostRef, WallType, WallVerdict
from ._base import Spec, host_of, landing, on_domain, path_of, verdict

_ACTIVITY = re.compile(r"urn:li:(?:activity|share|ugcPost):(\d{15,25})")
_POSTS = re.compile(r"^/posts/[^/]*?(?:activity|share|ugcPost)-(\d{15,25})(?:-[A-Za-z0-9_]+)?/?$")
_PULSE = re.compile(r"^/pulse/([^/?#]+)/?$")


class LinkedInSpec(Spec):
    name = "linkedin"
    domains = ("linkedin.com",)

    def parse(self, url: str) -> PostRef | None:
        if not self.match(url):
            return None
        path = path_of(url)
        m = _POSTS.match(path)
        if m:
            return self.ref(m.group(1), "post", url)
        if path.startswith("/feed/update/"):
            from urllib.parse import unquote

            m = _ACTIVITY.search(unquote(path))
            if m:
                return self.ref(m.group(1), "activity", url)
        m = _PULSE.match(path)
        if m:
            return self.ref(f"pulse:{m.group(1)}", "pulse", url, slug=m.group(1))
        return None

    def page_kind(self, url: str) -> str | None:
        path = path_of(url).rstrip("/")
        if path == "":
            return "home"
        if path.startswith(("/in/", "/company/")):
            return "profile"
        return None

    def canonical(self, ref: PostRef) -> str:
        if ref.form == "pulse":
            return f"https://www.linkedin.com/pulse/{ref.extra['slug']}/"
        return f"https://www.linkedin.com/feed/update/urn:li:activity:{ref.post_id}/"

    def forms(self, ref: PostRef, **known: str) -> list[tuple[str, str]]:
        return [("desktop", self.canonical(ref))]

    def judge(self, fetch: FetchResult, visible_text: str) -> WallVerdict | None:
        land = landing(fetch)
        if on_domain(host_of(land), *self.domains) and path_of(land).startswith(("/authwall", "/uas/login", "/checkpoint", "/login")):
            return verdict(WallType.LOGIN, "linkedin:authwall", fetch, len(visible_text), marker=path_of(land)[:30])
        return None


SPEC = LinkedInSpec()
