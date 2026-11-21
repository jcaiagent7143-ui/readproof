"""The page reader: one plain GET of the post's canonical URL, judged before anything is parsed.

What it reads is only what the page itself serves to an anonymous visitor in its HTML: the <title>, Open Graph
and meta description, JSON-LD (VideoObject / SocialMediaPosting / Article and kin), and the state some platforms
inline for their own scripts — Xiaohongshu/RedNote and Bilibili `window.__INITIAL_STATE__`, TikTok
`__UNIVERSAL_DATA_FOR_REHYDRATION__`, Weibo `$render_data`, WeChat `msg_title` / `msg_desc` / `#js_content`,
YouTube `ytInitialPlayerResponse`. Nothing is requested beyond the one GET: media and image URLs the page lists are
handed to the extraction step in `ReaderOutput.meta` ("media_urls", "image_urls"), and comments become Records only
when the page already carries them inline.

Order of trust when two sources say the same thing: platform state, then JSON-LD, then Open Graph, then <title>;
a later text already contained in an earlier one of the same field is dropped, so a teaser og:description does
not sit beside the full caption it was cut from.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..interfaces import Context, ReaderOutput
from ..types import Channel, PostRef, Record, WallType, WallVerdict
from .base import (
    ARTICLE_KEY,
    SpecFor,
    chunk_text,
    clean_text,
    decode_body,
    default_spec_for,
    fragment_text,
    gated_fetch,
    judge,
    make_record,
    refused,
    scan_html,
    store_fetch,
    target_url,
    visible_text,
)

# Platforms whose post is the page's running text: their visible text is always kept as PAGE passages, unless
# the page's first <article> holds the body (then that body is the post and the rest is navigation).
TEXT_PLATFORMS = {"web", "toutiao"}
ARTICLE_MIN_CHARS = 120
MAX_PAGE_PASSAGES = 80
MAX_COMMENTS = 50
_WECHAT_IDS = {"js_content", "activity-name", "js_name"}


# --- inline JavaScript state ------------------------------------------------------------------------------


def balanced_literal(src: str, start: int) -> str | None:
    """The object/array literal that opens at src[start] ('{' or '['), braces balanced, strings respected."""
    if start >= len(src) or src[start] not in "{[":
        return None
    depth = 0
    quote = ""
    escaped = False
    for i in range(start, len(src)):
        c = src[i]
        if quote:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == quote:
                quote = ""
            continue
        if c in "\"'`":
            quote = c
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    return None


def find_assignment(src: str, pattern: str) -> str | None:
    """The literal assigned right after `pattern` (a regex that ends where the literal starts)."""
    for m in re.finditer(pattern, src):
        i = m.end()
        while i < len(src) and src[i] in " \t\r\n":
            i += 1
        lit = balanced_literal(src, i)
        if lit:
            return lit
    return None


_JS_ESC = re.compile(r"\\(x[0-9a-fA-F]{2}|u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|\r\n|\n|.)", re.S)
_SIMPLE_ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0", "\n": "", "\r\n": ""}


def js_string_value(literal: str) -> str:
    """A JavaScript string literal (quotes included) as its value: \\x41, \\u{1F600}, \\u4E2D, \\' all decoded."""
    body = literal[1:-1]

    def rep(m: re.Match[str]) -> str:
        e = m.group(1)
        if e[0] == "x" and len(e) == 3:
            return chr(int(e[1:], 16))
        if e.startswith("u{"):
            return chr(int(e[2:-1], 16))
        if e[0] == "u" and len(e) == 5:
            return chr(int(e[1:], 16))
        return _SIMPLE_ESC.get(e, e)

    value = _JS_ESC.sub(rep, body)
    return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")  # join \uD83D\uDE00 pairs


_JS_TOKEN = re.compile(
    r'(?P<dq>"(?:[^"\\\n]|\\.)*")'
    r"|(?P<sq>'(?:[^'\\\n]|\\.)*')"
    r"|(?P<comment>/\*.*?\*/|//[^\n]*)"
    r"|(?P<ident>[A-Za-z_$][\w$]*)"
    r"|(?P<bang>![01](?![\w$]))"
    r"|(?P<trail>,(?=\s*[}\]]))"
    r"|(?P<other>[^\"'A-Za-z_$!,/]+|.)",
    re.S,
)
_NON_JSON_ESCAPE = re.compile(r"\\(?![\"\\/bfnrtu])|\\u(?![0-9a-fA-F]{4})")
_CONSTRUCTORS = {"Set", "Map", "Date", "Array", "Object", "Number", "String", "Boolean"}
_CALL_AFTER = re.compile(r"\s*\(")
_KEY_AFTER = re.compile(r"\s*:")


def js_to_json(src: str) -> str:
    """A JavaScript object literal of data (what platforms inline for their scripts) as JSON text.

    Handles what such blobs really contain: `undefined` / `NaN` / `Infinity` values, single-quoted strings, \\x
    escapes, unquoted keys, `!0` / `!1`, trailing commas, comments, and constructor calls (live 2026-11-21, the
    RedNote note page: `"originalCommentIds":new Set([])`): `new Set([...])` / `new Map([[k, v]])` become their
    array, `new Date(n)` its argument. Outside strings a data blob has parentheses only around such calls, so they
    are dropped. Not a JavaScript parser: code is not data."""
    out: list[str] = []
    for m in _JS_TOKEN.finditer(src):
        kind, tok = m.lastgroup, m.group(0)
        if kind == "ident" and not _KEY_AFTER.match(src, m.end()) and (
                tok == "new" or (tok in _CONSTRUCTORS and _CALL_AFTER.match(src, m.end()))):
            continue
        if kind == "other" and ("(" in tok or ")" in tok):
            tok = tok.replace("(", "").replace(")", "")
            if not tok:
                continue
        if kind == "dq":
            out.append(json.dumps(js_string_value(tok), ensure_ascii=False) if _NON_JSON_ESCAPE.search(tok) else tok)
        elif kind == "sq":
            out.append(json.dumps(js_string_value(tok), ensure_ascii=False))
        elif kind in ("comment", "trail"):
            continue
        elif kind == "ident":
            if tok in ("undefined", "NaN", "Infinity"):
                if tok == "Infinity" and out and out[-1].rstrip().endswith("-"):
                    out[-1] = out[-1].rstrip()[:-1]
                out.append("null")
            elif tok in ("true", "false", "null"):
                out.append(tok)
            elif _KEY_AFTER.match(src, m.end()):
                out.append(json.dumps(tok))  # an unquoted key
            else:
                out.append(tok)
        elif kind == "bang":
            out.append("true" if tok == "!0" else "false")
        else:
            out.append(tok)
    return "".join(out)


def js_loads(literal: str | None) -> Any:
    """Parse an inline state literal; None when it is not data we can read (never raises)."""
    if not literal:
        return None
    try:
        return json.loads(literal)
    except ValueError:
        pass
    try:
        return json.loads(js_to_json(literal))
    except ValueError:
        return None


def js_var(src: str, name: str) -> str:
    """The string a page script assigns to `name` (`var msg_title = '…'.html(false);`, `htmlDecode("…")`)."""
    pat = re.compile(r"\b" + re.escape(name) + r"""\s*=\s*(?:htmlDecode\(\s*)?("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')""")
    m = pat.search(src)
    if not m:
        return ""
    import html as html_lib

    return html_lib.unescape(js_string_value(m.group(1))).strip()


def dig(obj: Any, *path: Any) -> Any:
    """obj[p0][p1]... or None, through dicts and lists, never raising."""
    for p in path:
        if isinstance(obj, dict):
            obj = obj.get(p)
        elif isinstance(obj, list) and isinstance(p, int) and -len(obj) <= p < len(obj):
            obj = obj[p]
        else:
            return None
    return obj


def _s(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


# --- what one page yields ----------------------------------------------------------------------------------


@dataclass
class Item:
    channel: Channel
    locator: dict[str, Any]
    text: str
    extractor: str


@dataclass
class Collected:
    """Everything parse_html found in one served page, before it becomes Records."""

    items: list[Item] = field(default_factory=list)
    media_urls: list[str] = field(default_factory=list)  # alternatives for the post's stream, best first
    image_urls: list[str] = field(default_factory=list)  # carousel images (or one cover when there are none)
    image_kinds: list[str] = field(default_factory=list)  # parallel to image_urls: "carousel" | "cover"
    covers: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    found_post: bool = False  # a platform parser recognised the post itself (not just the page chrome)
    base_url: str = ""  # relative image/media URLs are resolved against the page that listed them

    def caption(self, field_name: str, text: Any, extractor: str) -> None:
        self._add(Channel.CAPTION, {"field": field_name}, _s(text), extractor)

    def page_text(self, text: Any, extractor: str) -> None:
        for offset, passage in chunk_text(_s(text)):
            self._add(Channel.PAGE, {"offset": offset}, passage, extractor)

    def comment(self, rank: int, text: Any, extractor: str) -> None:
        self._add(Channel.COMMENT, {"rank": rank}, _s(text), extractor)

    def _add(self, channel: Channel, locator: dict[str, Any], text: str, extractor: str) -> None:
        text = clean_text(text)
        if text:
            self.items.append(Item(channel, locator, text, extractor))

    def media(self, url: Any) -> None:
        u = _abs_url(url, self.base_url)
        if u and u not in self.media_urls:
            self.media_urls.append(u)

    def image(self, url: Any) -> None:
        u = _abs_url(url, self.base_url)
        if u and u not in self.image_urls:
            self.image_urls.append(u)
            self.image_kinds.append("carousel")

    def cover(self, url: Any) -> None:
        u = _abs_url(url, self.base_url)
        if u and u not in self.covers:
            self.covers.append(u)

    def finish(self) -> None:
        """A post with no carousel gets its cover as its one image (OCR reads text designed onto covers)."""
        if not self.image_urls and self.covers:
            self.image_urls.append(self.covers[0])
            self.image_kinds.append("cover")


def _abs_url(url: Any, base: str = "") -> str:
    """Absolute https URL. http is upgraded the way Chrome auto-upgrades mixed-content images and media."""
    u = _s(url)
    if not u or u.startswith(("data:", "blob:", "javascript:")):
        return ""
    if u.startswith("//"):
        u = "https:" + u
    elif base and not re.match(r"^[a-z][a-z0-9+.-]*:", u, re.I):
        from urllib.parse import urljoin

        u = urljoin(base, u)
    if u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    return u if u.startswith("https://") else ""


# --- platform parsers --------------------------------------------------------------------------------------


def _xiaohongshu(html: str, col: Collected, post_id: str, ex: str) -> None:
    state = js_loads(find_assignment(html, r"window\.__INITIAL_STATE__\s*="))
    if not isinstance(state, dict):
        return
    note, entry = None, None
    ndm = dig(state, "note", "noteDetailMap")
    if isinstance(ndm, dict):
        keys = sorted(ndm, key=lambda k: k != post_id)  # the URL's own note first
        for k in keys:
            n = dig(ndm, k, "note")
            if isinstance(n, dict) and (n.get("title") or n.get("desc") or n.get("imageList")):
                note, entry = n, ndm[k]
                break
    if note is None:
        n = dig(state, "noteData", "data", "noteData")  # the mobile page (rednote.com to a phone)
        note = n if isinstance(n, dict) and n else None
    if note is None:
        return
    col.found_post = True
    col.caption("title", note.get("title"), ex)
    col.caption("caption", note.get("desc"), ex)
    user = note.get("user") if isinstance(note.get("user"), dict) else {}
    col.caption("author", user.get("nickname") or user.get("nickName"), ex)
    tags = [_s(t.get("name")) for t in note.get("tagList") or [] if isinstance(t, dict) and _s(t.get("name"))]
    if tags:
        col.meta["tags"] = tags
    images: list[str] = []
    for img in note.get("imageList") or []:
        if not isinstance(img, dict):
            continue
        u = img.get("urlDefault") or img.get("urlPre") or img.get("url")
        if not u:
            u = next((i.get("url") for i in img.get("infoList") or [] if isinstance(i, dict) and i.get("url")), "")
        if u:
            images.append(u)
    streams: list[tuple[int, str]] = []
    for codec in ("h264", "h265", "av1"):
        for fmt in dig(note, "video", "media", "stream", codec) or []:
            if not isinstance(fmt, dict):
                continue
            height = fmt.get("height") if isinstance(fmt.get("height"), (int, float)) else 0
            backups = fmt.get("backupUrls") if isinstance(fmt.get("backupUrls"), list) else []
            for u in [fmt.get("masterUrl"), *backups]:
                if isinstance(u, str) and u:
                    streams.append((-height, u))
        if streams:
            break  # h264 first; h265/av1 only when there is no h264 rendition
    for _, u in sorted(streams, key=lambda x: x[0]):
        col.media(u)
    is_video = note.get("type") == "video" or bool(streams)
    if is_video:
        for u in images[:1]:
            col.cover(u)
        dur = dig(note, "video", "capa", "duration") or dig(note, "video", "media", "video", "duration")
        if isinstance(dur, (int, float)) and dur > 0:
            col.meta["duration_s"] = float(dur)
    else:
        for u in images:
            col.image(u)
    comments = dig(state, "noteData", "data", "commentData", "comments") or dig(entry, "comments", "list") or []
    rank = 0
    for c in comments if isinstance(comments, list) else []:
        if isinstance(c, dict) and _s(c.get("content")):
            rank += 1
            col.comment(rank, c.get("content"), ex)


def _bilibili(html: str, col: Collected, ex: str) -> None:
    state = js_loads(find_assignment(html, r"window\.__INITIAL_STATE__\s*="))
    vd = dig(state, "videoData") if isinstance(state, dict) else None
    if not isinstance(vd, dict) or not (vd.get("title") or vd.get("bvid")):
        return
    col.found_post = True
    col.caption("title", vd.get("title"), ex)
    desc = _s(vd.get("desc"))
    if desc and desc != "-":  # "-" is Bilibili's placeholder for "no description"
        col.caption("description", desc, ex)
    col.caption("author", dig(vd, "owner", "name"), ex)
    col.cover(vd.get("pic"))
    pages = [p for p in vd.get("pages") or [] if isinstance(p, dict)]
    if pages:
        col.meta["parts"] = [{"p": p.get("page"), "part": _s(p.get("part")), "cid": p.get("cid"),
                              "duration_s": p.get("duration")} for p in pages[:200]]
    if isinstance(vd.get("duration"), (int, float)) and vd["duration"] > 0:
        col.meta["duration_s"] = float(vd["duration"])


def _tiktok(scan: Any, col: Collected, ex: str) -> None:
    raw = scan.json_scripts.get("__UNIVERSAL_DATA_FOR_REHYDRATION__") or scan.json_scripts.get("SIGI_STATE")
    data = js_loads(raw)
    if not isinstance(data, dict):
        return
    detail = dig(data, "__DEFAULT_SCOPE__", "webapp.video-detail")
    item = dig(detail, "itemInfo", "itemStruct")
    if not isinstance(item, dict):
        mod = data.get("ItemModule")
        item = next(iter(mod.values()), None) if isinstance(mod, dict) and mod else None
    if isinstance(detail, dict) and "statusCode" in detail:
        col.meta["status_code"] = detail.get("statusCode")
    if not isinstance(item, dict) or not (item.get("desc") or item.get("id")):
        return
    col.found_post = True
    col.caption("caption", item.get("desc"), ex)
    author = item.get("author")
    if isinstance(author, dict):
        col.caption("author", author.get("nickname") or author.get("uniqueId"), ex)
    elif isinstance(author, str):
        col.caption("author", author, ex)
    video = item.get("video") if isinstance(item.get("video"), dict) else {}
    for u in [video.get("playAddr"), video.get("downloadAddr")]:
        col.media(u)
    for b in video.get("bitrateInfo") or []:
        for u in dig(b, "PlayAddr", "UrlList") or []:
            col.media(u)
    if isinstance(video.get("duration"), (int, float)) and video["duration"] > 0:
        col.meta["duration_s"] = float(video["duration"])
    col.cover(video.get("originCover") or video.get("cover"))
    post = item.get("imagePost") if isinstance(item.get("imagePost"), dict) else {}
    col.caption("title", post.get("title"), ex)
    for img in post.get("images") or []:
        col.image(dig(img, "imageURL", "urlList", 0))


def _weibo(html: str, col: Collected, ex: str) -> None:
    data = js_loads(find_assignment(html, r"\$render_data\s*="))
    status = dig(data, 0, "status") if isinstance(data, list) else dig(data, "status")
    if not isinstance(status, dict):
        return
    col.found_post = True
    text = fragment_text(_s(dig(status, "longText", "longTextContent")) or _s(status.get("text")))
    quoted = status.get("retweeted_status")
    if isinstance(quoted, dict):
        qtext = fragment_text(_s(quoted.get("text")))
        if qtext:
            text = f"{text}\n\n转发自 @{_s(dig(quoted, 'user', 'screen_name'))}：{qtext}".strip()
    col.caption("caption", text, ex)
    col.caption("author", dig(status, "user", "screen_name"), ex)
    for pic in status.get("pics") or []:
        if isinstance(pic, dict):
            col.image(dig(pic, "large", "url") or pic.get("url"))
    page_info = status.get("page_info") if isinstance(status.get("page_info"), dict) else {}
    info = page_info.get("media_info") if isinstance(page_info.get("media_info"), dict) else {}
    for key in ("stream_url_hd", "mp4_720p_mp4", "mp4_hd_url", "stream_url", "mp4_sd_url"):
        col.media(info.get(key))
    if isinstance(info.get("duration"), (int, float)) and info["duration"] > 0:
        col.meta["duration_s"] = float(info["duration"])
    col.cover(dig(page_info, "page_pic", "url"))


def _wechat(html: str, scan: Any, col: Collected, ex: str) -> None:
    title = js_var(html, "msg_title") or scan.ids.get("activity-name", "")
    body = scan.ids.get("js_content", "")
    if not (title or body):
        return
    col.caption("title", title, ex)
    col.caption("description", js_var(html, "msg_desc"), ex)
    col.caption("author", js_var(html, "nickname") or scan.ids.get("js_name", ""), ex)
    if body:
        col.found_post = True
        col.page_text(body, ex)
    for u in scan.id_images.get("js_content", []):
        col.image(u)
    col.cover(js_var(html, "msg_cdn_url"))


def _youtube(html: str, col: Collected, ex: str) -> None:
    resp = js_loads(find_assignment(html, r"ytInitialPlayerResponse\s*="))
    details = dig(resp, "videoDetails")
    if not isinstance(details, dict):
        return
    col.found_post = True
    col.caption("title", details.get("title"), ex)
    col.caption("description", details.get("shortDescription"), ex)
    col.caption("author", details.get("author"), ex)
    try:
        col.meta["duration_s"] = float(details.get("lengthSeconds"))
    except (TypeError, ValueError):
        pass
    thumbs = dig(details, "thumbnail", "thumbnails") or []
    if thumbs:
        col.cover(dig(thumbs, -1, "url"))


_LD_TYPES = {"videoobject", "socialmediaposting", "article", "newsarticle", "blogposting", "reportagenewsarticle",
             "discussionforumposting", "liveblogposting", "techarticle", "imageobject"}


def _ld_nodes(data: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    stack = [data]
    while stack:
        cur = stack.pop(0)
        if isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, dict):
            out.append(cur)
            if isinstance(cur.get("@graph"), list):
                stack.extend(cur["@graph"])
            if isinstance(cur.get("mainEntity"), (dict, list)):
                stack.append(cur["mainEntity"])
    return out


def _names(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [_s(v.get("name"))] if _s(v.get("name")) else []
    if isinstance(v, list):
        return [n for x in v for n in _names(x)]
    return []


def _urls(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [_s(v.get("url") or v.get("contentUrl"))]
    if isinstance(v, list):
        return [u for x in v for u in _urls(x)]
    return []


def _jsonld(scan: Any, col: Collected, ex: str) -> None:
    for raw in scan.ld_json:
        raw = re.sub(r"^\s*(<!--|<!\[CDATA\[)|(-->|\]\]>)\s*$", "", raw.strip()).strip().rstrip(";")
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for node in _ld_nodes(data):
            types = node.get("@type")
            types = [types] if isinstance(types, str) else types if isinstance(types, list) else []
            if not any(isinstance(t, str) and t.lower() in _LD_TYPES for t in types):
                continue
            col.caption("title", node.get("headline") or node.get("name"), ex)
            col.caption("description", node.get("description"), ex)
            for name in _names(node.get("author")):
                col.caption("author", name, ex)
            col.page_text(node.get("articleBody"), ex)
            col.page_text(node.get("transcript"), ex + ":transcript")
            if any(isinstance(t, str) and t.lower() == "videoobject" for t in types):
                for u in _urls(node.get("contentUrl")):
                    col.media(u)
            for u in _urls(node.get("thumbnailUrl")) + _urls(node.get("image")):
                col.cover(u)
            comments = node.get("comment")
            rank = 0
            for c in comments if isinstance(comments, list) else []:
                if isinstance(c, dict) and _s(c.get("text")):
                    rank += 1
                    col.comment(rank, c.get("text"), ex)


def parse_html(html: str, *, platform: str, url: str = "", post_id: str = "", prefix: str = "page",
               include_visible: bool = True) -> Collected:
    """Everything a served page itself carries, as Items plus media/image URLs. Pure: no network, no store."""
    col = Collected(base_url=url)
    scan = scan_html(html, _WECHAT_IDS if platform == "wechat" else None)
    if platform == "xiaohongshu":
        _xiaohongshu(html, col, post_id, f"{prefix}:xhs_state")
    elif platform == "bilibili":
        _bilibili(html, col, f"{prefix}:bili_state")
    elif platform == "tiktok":
        _tiktok(scan, col, f"{prefix}:tiktok_state")
    elif platform == "weibo":
        _weibo(html, col, f"{prefix}:weibo_render")
    elif platform == "wechat":
        _wechat(html, scan, col, f"{prefix}:wechat")
    elif platform == "youtube":
        _youtube(html, col, f"{prefix}:yt_player")
    _jsonld(scan, col, f"{prefix}:jsonld")
    col.caption("title", scan.meta("og:title", "twitter:title"), f"{prefix}:og")
    col.caption("description", scan.meta("og:description", "twitter:description"), f"{prefix}:og")
    col.caption("description", scan.meta("description"), f"{prefix}:meta")
    author = scan.meta("author", "article:author")
    if author and not author.startswith("http"):
        col.caption("author", author, f"{prefix}:meta")
    col.caption("title", scan.title, f"{prefix}:title")
    if (scan.meta("og:video:type") or "").startswith("video/"):
        col.media(scan.meta("og:video:secure_url", "og:video:url", "og:video"))
    if platform != "web":
        col.cover(scan.meta("og:image", "twitter:image"))
    article = scan.ids.get(ARTICLE_KEY, "") if platform in TEXT_PLATFORMS else ""
    if len(article) >= ARTICLE_MIN_CHARS:
        col.page_text(article, f"{prefix}:article")
        col.found_post = True
    elif include_visible and (platform in TEXT_PLATFORMS or not col.found_post):
        _, text = visible_text(html)
        col.page_text(text, f"{prefix}:text")
    col.finish()
    return col


# --- Items -> Records --------------------------------------------------------------------------------------


def _group(item: Item) -> tuple[str, str]:
    if item.channel is Channel.CAPTION:
        f = item.locator.get("field", "")
        return ("caption", "body" if f in ("caption", "description") else f)
    return (item.channel.value, "")


def build_records(col: Collected, ref: PostRef, *, source_sha256: str, fetched_at: str) -> list[Record]:
    """Items as Records, duplicates dropped: same id, or text already inside a kept text of the same field."""
    from ..verify import norm

    kept: list[Record] = []
    seen_ids: set[str] = set()
    normed: dict[tuple[str, str], list[str]] = {}
    counts: dict[Channel, int] = {}
    for item in col.items:
        cap = MAX_PAGE_PASSAGES if item.channel is Channel.PAGE else MAX_COMMENTS if item.channel is Channel.COMMENT else 1000
        if counts.get(item.channel, 0) >= cap:
            continue
        n = norm(item.text)
        if not n:
            continue
        group = _group(item)
        if item.channel is not Channel.COMMENT and any(n in k for k in normed.get(group, [])):
            continue
        rec = make_record(ref, item.channel, item.locator, item.text, source_sha256=source_sha256,
                          extractor=item.extractor, fetched_at=fetched_at)
        if rec.record_id in seen_ids:
            continue
        seen_ids.add(rec.record_id)
        normed.setdefault(group, []).append(n)
        counts[item.channel] = counts.get(item.channel, 0) + 1
        kept.append(rec)
    return kept


def collected_meta(col: Collected) -> dict[str, Any]:
    meta = dict(col.meta)
    if col.media_urls:
        meta["media_urls"] = list(col.media_urls)
    if col.image_urls:
        meta["image_urls"] = list(col.image_urls)
        meta["image_kinds"] = list(col.image_kinds)
    if col.covers:
        meta["cover_urls"] = list(col.covers)
    return meta


class PageReader:
    """One plain GET (after the Gate), judged by walls.judge, parsed only when served."""

    name = "page"

    def __init__(self, spec_for: SpecFor | None = None) -> None:
        self._spec_for = spec_for or default_spec_for

    def applies(self, ref: PostRef, ctx: Context) -> bool:
        return bool(ref.url)

    def read(self, ref: PostRef, ctx: Context) -> ReaderOutput:
        spec = self._spec_for(ref)
        url = target_url(ref, spec)
        decision, fetch = gated_fetch(ctx, url, purpose="read")
        if fetch is None:
            return refused(self.name, decision)
        sha = store_fetch(ctx, fetch)
        verdict, judge_name = judge(fetch, spec)
        out = ReaderOutput(reader=self.name, verdict=verdict, fetches=[fetch], gate=[decision],
                           meta={"judge": judge_name, "page_url": fetch.final_url or url, "snapshot": sha})
        if not verdict.served:
            return out  # rule 6: a wall is never parsed into Records
        html = decode_body(fetch)
        col = parse_html(html, platform=ref.platform, url=fetch.final_url or url, post_id=ref.post_id)
        out.records = build_records(col, ref, source_sha256=sha or fetch.sha256, fetched_at=fetch.fetched_at)
        out.meta.update(collected_meta(col))
        if not out.records:
            out.verdict = WallVerdict(False, WallType.EMPTY, f"judged served but carried no text ({verdict.signal})",
                                      verdict.visible_chars)
            out.meta.pop("media_urls", None)
            out.meta.pop("image_urls", None)
        return out
