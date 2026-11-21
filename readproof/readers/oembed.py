"""The oEmbed reader: a platform's own documented endpoint for publishers (spec.oembed_url).

Cheapest reader first (DESIGN §4.5): one JSON request, no rendering, and it exists exactly so that other sites
can show a post. It carries a title and an author (and a thumbnail), never media or comments, so it serves CAPTION
records only — and only when a non-empty title came back. X's endpoint has no `title`: the post's text is the
paragraph of the blockquote in `html`, and that paragraph stands in for the title there.

An oEmbed refusal says less than a page does (YouTube answers 401 for "embedding disabled" as well as for private
videos), so its walls are deliberately coarse; the harness moves on to the page reader either way.
"""

from __future__ import annotations

import json
import re

from ..interfaces import Context, ReaderOutput
from ..types import Channel, FetchResult, PostRef, WallType, WallVerdict
from .base import (
    SpecFor,
    decode_body,
    default_spec_for,
    fragment_text,
    gated_fetch,
    make_record,
    refused,
    store_fetch,
    target_url,
)

_BLOCKQUOTE_P = re.compile(r"<blockquote[^>]*>.*?<p[^>]*>(.*?)</p>", re.S | re.I)


def _verdict_for(fetch: FetchResult) -> tuple[WallVerdict, dict | None]:
    """(verdict, parsed JSON). Served needs a 200 JSON object; the title check happens in read()."""
    if fetch.error:
        return WallVerdict(False, WallType.NETWORK, f"oembed transport error: {fetch.error}"), None
    status = fetch.status or 0
    if status == 429:
        return WallVerdict(False, WallType.RATE_LIMITED, "oembed http 429"), None
    if status in (404, 410):
        return WallVerdict(False, WallType.DELETED, f"oembed http {status} (no such public post)"), None
    if status in (401, 403):
        return WallVerdict(False, WallType.UNKNOWN, f"oembed http {status} (embedding disabled, private or protected)"), None
    if status != 200:
        return WallVerdict(False, WallType.UNKNOWN, f"oembed http {status or 'none'}"), None
    try:
        data = json.loads(decode_body(fetch))
    except ValueError:
        return WallVerdict(False, WallType.UNKNOWN, f"oembed answered non-JSON ({fetch.bytes} bytes)"), None
    if not isinstance(data, dict):
        return WallVerdict(False, WallType.UNKNOWN, "oembed answered JSON that is not an object"), None
    return WallVerdict(True, None, "oembed 200"), data


class OEmbedReader:
    name = "oembed"

    def __init__(self, spec_for: SpecFor | None = None) -> None:
        self._spec_for = spec_for or default_spec_for

    def _endpoint(self, ref: PostRef) -> str | None:
        spec = self._spec_for(ref)
        return spec.oembed_url(target_url(ref, spec)) if spec is not None else None

    def applies(self, ref: PostRef, ctx: Context) -> bool:
        return bool(self._endpoint(ref))

    def read(self, ref: PostRef, ctx: Context) -> ReaderOutput:
        endpoint = self._endpoint(ref)
        if not endpoint:
            return ReaderOutput(reader=self.name, verdict=WallVerdict(False, WallType.UNSUPPORTED, "no oEmbed endpoint"))
        decision, fetch = gated_fetch(ctx, endpoint, purpose="oembed")
        if fetch is None:
            return refused(self.name, decision)
        sha = store_fetch(ctx, fetch) or fetch.sha256
        verdict, data = _verdict_for(fetch)
        out = ReaderOutput(reader=self.name, verdict=verdict, fetches=[fetch], gate=[decision], meta={"endpoint": endpoint})
        if not verdict.served or data is None:
            return out
        title = str(data.get("title") or "").strip()
        caption = ""
        if not title and ref.platform == "x":
            m = _BLOCKQUOTE_P.search(str(data.get("html") or ""))
            caption = fragment_text(m.group(1)) if m else ""
        if not (title or caption):
            out.verdict = WallVerdict(False, WallType.EMPTY, "oembed 200 without a title")
            return out
        author = str(data.get("author_name") or "").strip()
        fields = [("title", title), ("caption", caption), ("author", author)]
        out.records = [
            make_record(ref, Channel.CAPTION, {"field": f}, text, source_sha256=sha, extractor="oembed",
                        fetched_at=fetch.fetched_at)
            for f, text in fields if text
        ]
        out.verdict = WallVerdict(True, None, f"oembed 200 title_chars={len(title or caption)}", len(title or caption))
        thumb = str(data.get("thumbnail_url") or "").strip()
        if thumb.startswith("http"):
            out.meta["image_urls"] = [thumb.replace("http://", "https://", 1)]
            out.meta["image_kinds"] = ["cover"]
        for key in ("provider_name", "author_url", "type"):
            if data.get(key):
                out.meta[key] = data[key]
        return out
