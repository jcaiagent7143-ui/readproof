"""Any other http(s) page: the generic fallback, always last in the registry.

A web page has no platform id, so its post id is the SHA-1 of the normalised URL: scheme and host
lower-cased, default port and fragment dropped, tracking parameters dropped, the rest sorted, a trailing slash
removed. Two pastes of one page with different utm_ tags are one post. Wall rules are the generic ones in
readproof/walls.py; this spec adds none.
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlunsplit

from ..types import PostRef
from ._base import Spec, split

# Tracking, never content: dropped from the identity of a page (not from what is fetched).
TRACKING = frozenset({
    "fbclid", "gclid", "dclid", "msclkid", "yclid", "mc_cid", "mc_eid", "igshid", "igsh", "_hsenc", "_hsmi",
    "ref_src", "spm",
})


def normalise(url: str) -> str:
    parts = split(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    port = parts.port
    netloc = host if port is None or (scheme, port) in (("http", 80), ("https", 443)) else f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    kept = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                  if not k.lower().startswith("utm_") and k.lower() not in TRACKING)
    return urlunsplit((scheme, netloc, path, urlencode(kept), ""))


class WebSpec(Spec):
    name = "web"

    def match(self, url: str) -> bool:
        parts = split(url)
        return parts.scheme.lower() in ("http", "https") and bool(parts.hostname)

    def is_short(self, url: str) -> bool:
        return False

    def parse(self, url: str) -> PostRef | None:
        if not self.match(url):
            return None
        canonical = normalise(url)
        return self.ref(hashlib.sha1(canonical.encode("utf-8")).hexdigest(), "desktop", url, normalised=canonical)

    def page_kind(self, url: str) -> str | None:
        return None

    def canonical(self, ref: PostRef) -> str:
        return ref.extra.get("normalised") or normalise(ref.url)

    def forms(self, ref: PostRef, **known: str) -> list[tuple[str, str]]:
        url = self.canonical(ref)
        return [("desktop", url), ("share_text", f"{known.get('title') or '…'} {url}")]


SPEC = WebSpec()
