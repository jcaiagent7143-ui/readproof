"""The embed reader: the platform's official embed player (spec.embed_url), loaded in a fresh headless browser.

The official-player principle: media is taken only from what the platform's own player serves to an anonymous
visitor. The player page is opened exactly as an embedding site would open it, through the browser reader's
machinery (fresh context, empty cookie jar, declared UA, every request logged, top-level URL gated). If it has
not started on its own, the reader presses play the way a visitor does (muted play() on the page's own media
elements, then one click on the player) — nothing about the page is changed and no request is composed by us.

Media is recognised by what the player itself fetched: responses whose content-type is video/* or audio/*, or
whose path ends in .mp4/.m4a/.m4s/.flv/.webm/.mp3/.aac. Players fetch byte ranges; when one did, the same URL is
requested once in full through the same browser context's request API — same UA, same context (so only cookies
the player's own page set during this read, exactly what its own range requests carried), Referer = the embed URL,
gated like any request of ours — never through a different identity. Files above 300 MB are not taken. Each file
goes to the store and to ctx.workdir; ReaderOutput.media lists the paths and meta carries ffprobe's duration.
Poster/cover images the player shows go to meta["image_urls"].

The player's own JSON answers (Douyin `aweme_detail` / `filter_detail`, Bilibili `view` code and `View`) are read
when the player received them: they give the caption, and they say when a post is self-visible (Douyin
`status_self_see`), deleted or under review (Bilibili -404/62002/62004/62012) or refused (-412/-352). The wall
verdict otherwise comes from walls.judge on the rendered player page. A player page is mostly a video and loads the
platform's own SDKs, so two GENERIC page verdicts are weaker evidence than the player's own answer and are
overridden by it: "empty" (little visible text) by media the player served or the post's detail in its JSON, and
a generic challenge marker (live 2026-11-21: Douyin's player page carries the `byted_acrawler` SDK it signs its
own requests with, under 1000 visible characters, while it was serving the post) only by the post's detail in the
player's own JSON. A platform rule's verdict (signal not "generic:") is never overridden.

Known limit: HLS players (one URL per segment) are not assembled; only whole-file and byte-range players are.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from ..interfaces import Context, ReaderOutput
from ..types import Channel, FetchResult, PostRef, WallType, WallVerdict
from .base import SpecFor, default_spec_for, judge, make_record, now_iso, refused, store_fetch
from .browser import VIEWPORT, BrowserSession, playwright_available

MEDIA_EXT = (".mp4", ".m4a", ".m4s", ".flv", ".webm", ".mp3", ".aac")
CAP_BYTES = 300 * 1024 * 1024
MAX_MEDIA_URLS = 3
PLAY_WAIT_S = 4.0  # after play(); then one click and the same again
COLLECT_S = 2.5  # after the first media response, so a DASH player's audio arrives with its video
_EXT_BY_TYPE = {"video/mp4": ".mp4", "audio/mp4": ".m4a", "video/x-flv": ".flv", "video/webm": ".webm",
                "audio/webm": ".webm", "audio/mpeg": ".mp3", "audio/aac": ".aac", "audio/x-m4a": ".m4a"}
_PLAY_JS = """() => { let n = 0;
  for (const m of document.querySelectorAll('video, audio')) {
    try { m.muted = true; const p = m.play(); if (p && p.catch) p.catch(() => {}); n++; } catch (e) {} }
  return n; }"""
_POSTERS_JS = "() => Array.from(document.querySelectorAll('video')).map(v => v.poster).filter(Boolean)"


@dataclass
class MediaHit:
    url: str
    status: int
    content_type: str
    ranged: bool
    total: int | None
    resource_type: str
    response: Any = field(repr=False, default=None)


@dataclass
class PlayerInfo:
    """What the player's own JSON said about the post."""

    items: list[tuple[str, str, str]] = field(default_factory=list)  # (field, text, extractor)
    source_sha256: str = ""
    wall: WallVerdict | None = None
    covers: list[str] = field(default_factory=list)
    duration_s: float | None = None


def _total_bytes(headers: dict[str, str], status: int) -> int | None:
    m = re.search(r"/(\d+)\s*$", headers.get("content-range", ""))
    if m:
        return int(m.group(1))
    if status == 200 and headers.get("content-length", "").isdigit():
        return int(headers["content-length"])
    return None


def is_media(url: str, content_type: str) -> bool:
    ct = content_type.split(";")[0].strip().lower()
    return ct.startswith(("video/", "audio/")) or urlparse(url).path.lower().endswith(MEDIA_EXT)


def _ext(url: str, content_type: str) -> str:
    ct = content_type.split(";")[0].strip().lower()
    if ct in _EXT_BY_TYPE and not urlparse(url).path.lower().endswith(".m4s"):
        return _EXT_BY_TYPE[ct]
    path = urlparse(url).path.lower()
    return next((e for e in MEDIA_EXT if path.endswith(e)), ".bin")


def _dig(obj: Any, *path: Any) -> Any:
    for p in path:
        if isinstance(obj, dict):
            obj = obj.get(p)
        elif isinstance(obj, list) and isinstance(p, int) and -len(obj) <= p < len(obj):
            obj = obj[p]
        else:
            return None
    return obj


def parse_douyin_player(data: dict[str, Any]) -> PlayerInfo | None:
    info = PlayerInfo()
    det = data.get("aweme_detail")
    if isinstance(det, dict) and det:
        ex = "embed:douyin_detail"
        info.items += [("caption", str(det.get("desc") or ""), ex),
                       ("author", str(_dig(det, "author", "nickname") or ""), ex)]
        for path in (("video", "origin_cover", "url_list", 0), ("video", "cover", "url_list", 0)):
            if isinstance(_dig(det, *path), str):
                info.covers.append(_dig(det, *path))
                break
        dur = _dig(det, "video", "duration") or det.get("duration")
        if isinstance(dur, (int, float)) and dur > 0:
            info.duration_s = float(dur) / 1000.0
        return info
    filt = data.get("filter_detail")
    if isinstance(filt, dict) and filt:
        reason = str(filt.get("filter_reason") or "")
        msg = str(filt.get("detail_msg") or filt.get("notice") or "").strip()
        if reason == "status_self_see":
            info.wall = WallVerdict(False, WallType.SELF_VISIBLE, f"douyin player filter_reason=status_self_see {msg}".strip())
        else:
            # The player answers an id that never existed exactly like a live post it declines: say what it did,
            # never that the post is gone.
            info.wall = WallVerdict(False, WallType.UNKNOWN, f"douyin player declined filter_reason={reason or 'none'} {msg}".strip())
        return info
    return None


_BILI_WALLS = {-404: WallType.DELETED, 62002: WallType.DELETED, 62012: WallType.SELF_VISIBLE,
               62004: WallType.UNKNOWN, -412: WallType.CHALLENGE, -352: WallType.CHALLENGE}


def parse_bilibili_player(url: str, data: dict[str, Any]) -> PlayerInfo | None:
    if "/view" not in urlparse(url).path:
        return None
    code = data.get("code")
    info = PlayerInfo()
    if code == 0:
        view = _dig(data, "data", "View") or data.get("data")
        if not isinstance(view, dict) or not view.get("title"):
            return None
        ex = "embed:bili_view"
        desc = str(view.get("desc") or "").strip()
        info.items += [("title", str(view.get("title") or ""), ex), ("description", "" if desc == "-" else desc, ex),
                       ("author", str(_dig(view, "owner", "name") or ""), ex)]
        if isinstance(view.get("pic"), str):
            info.covers.append(view["pic"])
        if isinstance(view.get("duration"), (int, float)) and view["duration"] > 0:
            info.duration_s = float(view["duration"])
        return info
    if code in _BILI_WALLS:
        note = {62004: "under review", -412: "risk control refused this address", -352: "risk control"}.get(code, "")
        info.wall = WallVerdict(False, _BILI_WALLS[code], f"bilibili player code {code} {data.get('message') or ''} {note}".strip())
        return info
    return None


class EmbedReader:
    name = "embed"

    def __init__(self, spec_for: SpecFor | None = None) -> None:
        self._spec_for = spec_for or default_spec_for

    def _embed_url(self, ref: PostRef) -> str | None:
        spec = self._spec_for(ref)
        return spec.embed_url(ref) if spec is not None else None

    def applies(self, ref: PostRef, ctx: Context) -> bool:
        return bool(ctx.profile.allow_browser) and playwright_available() and bool(self._embed_url(ref))

    def read(self, ref: PostRef, ctx: Context) -> ReaderOutput:
        spec = self._spec_for(ref)
        embed_url = spec.embed_url(ref) if spec is not None else None
        if not embed_url:
            return ReaderOutput(self.name, WallVerdict(False, WallType.UNSUPPORTED, "no official embed player"))
        if not ctx.profile.allow_browser:
            return ReaderOutput(self.name, WallVerdict(False, WallType.POLICY, f"profile {ctx.profile.name} forbids the browser"))
        if not playwright_available():
            return ReaderOutput(self.name, WallVerdict(False, WallType.UNSUPPORTED, "playwright is not installed"))
        decision = ctx.gate.check(embed_url, purpose="browser")
        if not decision.allowed:
            return refused(self.name, decision)
        want_media = bool(ctx.options.get("speech", True) or ctx.options.get("on_screen", True))
        keep_media = want_media and bool(ctx.profile.allow_embed_media)
        hits: list[MediaHit] = []
        json_resps: list[Any] = []

        def watch(resp: Any) -> None:
            url = resp.url
            if url.startswith(("data:", "blob:")):
                return
            headers = resp.headers
            ct = headers.get("content-type", "")
            if is_media(url, ct):
                status = resp.status
                hits.append(MediaHit(url=url, status=status, content_type=ct, total=_total_bytes(headers, status),
                                     ranged=status == 206 or "content-range" in headers or bool(resp.request.headers.get("range")),
                                     resource_type=resp.request.resource_type, response=resp))
            elif "json" in ct.lower() and len(json_resps) < 60:
                json_resps.append(resp)

        t0 = time.monotonic()
        media: list[dict[str, Any]] = []
        media_fetches: list[FetchResult] = []
        media_gates: list[Any] = []
        player = PlayerInfo()
        player_snaps: list[str] = []
        with BrowserSession(ctx, reader=self.name, block_media=False) as s:
            s.response_hooks.append(watch)
            s.open(embed_url)
            s.lock_navigation = True
            pressed = ""
            if keep_media and not hits and s.nav_error is None:
                pressed = self._press_play(s, hits)
            if hits:
                s.wait(COLLECT_S)
            try:
                posters = [p for p in (s.page.evaluate(_POSTERS_JS) or []) if isinstance(p, str)]
            except Exception:
                posters = []
            html, title, text, final_url = s.snapshot()
            png = s.screenshot()
            fetch = s.fetch_result(html, final_url)
            verdict0, judge_name = judge(fetch, spec)
            found = self._player_info(ref, json_resps, ctx)
            if found is not None:
                player, player_snaps = found
            overridable = (not verdict0.served and verdict0.signal.startswith("generic:")
                           and verdict0.wall in (WallType.EMPTY, WallType.CHALLENGE))
            go_on = verdict0.served or overridable
            # Download only when the media could make the read served: never for a generic challenge the player's
            # own JSON did not contradict (that outcome is "not served" whatever the media).
            detail = any(t for _, t, _ in player.items)
            may_serve = verdict0.served or (overridable and (detail or verdict0.wall is WallType.EMPTY))
            if may_serve and player.wall is None and keep_media and hits:
                media, media_fetches, media_gates = self._download(s, hits, embed_url, ctx)
            ua, gates = s.user_agent, list(s.gate_decisions)
            subrequests, blocked = s.subrequests, s.blocked
        shot = ctx.store.put(png, content_type="image/png", source_url=final_url) if png else None
        sha = store_fetch(ctx, fetch) or fetch.sha256
        # the verdict, in order of evidence
        evidence = f"player served media_files={len(media)} detail={detail}"
        if not go_on:
            verdict = verdict0
        elif player.wall is not None:
            verdict = player.wall
        elif verdict0.served and (media or detail):
            verdict = WallVerdict(True, None, f"{evidence}; page: {verdict0.signal}", verdict0.visible_chars)
        elif overridable and (detail or (media and verdict0.wall is WallType.EMPTY)):
            verdict = WallVerdict(True, None, f"{evidence}; generic page verdict {verdict0.wall.value} overridden by the "
                                              f"player's own answer; page: {verdict0.signal}", verdict0.visible_chars)
        elif not verdict0.served:
            verdict = verdict0
        else:
            verdict = WallVerdict(False, WallType.EMPTY, f"player served no media and no post detail; page: {verdict0.signal}",
                                  verdict0.visible_chars)
        if not verdict.served:
            for m in media:
                _unlink(m["path"])
            media = []
        image_urls = [u for u in dict.fromkeys(posters + player.covers) if isinstance(u, str) and u.startswith("http")]
        meta: dict[str, Any] = {
            "judge": judge_name, "embed_url": embed_url, "final_url": final_url, "title": title, "user_agent": ua,
            "screenshot": shot, "snapshot": sha, "page_url": embed_url, "subrequests": subrequests, "blocked": blocked,
            "media_hits": len(hits), "media_urls_seen": len({h.url for h in hits}), "pressed_play": pressed,
            "snapshots": [x for x in [shot, *player_snaps] if x], "media": media,
        }
        if want_media and not ctx.profile.allow_embed_media:
            meta["media_skipped"] = f"profile {ctx.profile.name} does not keep embed media"
        durations = [m["duration_s"] for m in media if m.get("duration_s")]
        if durations or player.duration_s:
            meta["duration_s"] = max(durations) if durations else player.duration_s
        if image_urls and verdict.served:
            meta["image_urls"] = image_urls[:2]
            meta["image_kinds"] = ["cover"] * len(meta["image_urls"])
        ctx.log.emit("embed_read", url=embed_url, final_url=final_url, status=fetch.status, served=verdict.served,
                     wall=verdict.wall.value if verdict.wall else None, signal=verdict.signal, media_hits=len(hits),
                     media_files=len(media), pressed_play=pressed, elapsed_ms=int((time.monotonic() - t0) * 1000))
        out = ReaderOutput(self.name, verdict, fetches=[fetch, *media_fetches], gate=[decision, *gates, *media_gates],
                           media=[m["path"] for m in media], meta=meta)
        if verdict.served:
            out.records = [
                make_record(ref, Channel.CAPTION, {"field": f}, t.strip(), source_sha256=player.source_sha256 or sha,
                            extractor=ex, fetched_at=fetch.fetched_at)
                for f, t, ex in player.items if t and t.strip()
            ]
        return out

    # --- helpers -----------------------------------------------------------------------------------------

    def _press_play(self, s: BrowserSession, hits: list[MediaHit]) -> str:
        """Start the player the way a visitor does; returns what was done ("play()", "click", or "")."""
        done = ""
        try:
            if s.page.evaluate(_PLAY_JS):
                done = "play()"
                self._wait_for(s, hits, PLAY_WAIT_S)
        except Exception:
            pass
        if not hits:
            try:
                s.page.mouse.click(VIEWPORT["width"] / 2, VIEWPORT["height"] / 2)
                done = (done + "+click") if done else "click"
                self._wait_for(s, hits, PLAY_WAIT_S * 1.5)
            except Exception:
                pass
        return done

    @staticmethod
    def _wait_for(s: BrowserSession, hits: list[MediaHit], seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while not hits and time.monotonic() < deadline:
            s.wait(0.25)

    def _player_info(self, ref: PostRef, json_resps: list[Any], ctx: Context) -> tuple[PlayerInfo, list[str]] | None:
        """The post as the player's own JSON answers describe it (Douyin and Bilibili), else None."""
        if ref.platform not in ("douyin", "bilibili"):
            return None
        snaps: list[str] = []
        for resp in json_resps:
            try:
                body = resp.body()
                data = json.loads(body.decode("utf-8", "replace"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            info = parse_douyin_player(data) if ref.platform == "douyin" else parse_bilibili_player(resp.url, data)
            if info is None:
                continue
            sha = ctx.store.put(body, content_type="application/json", source_url=resp.url)
            snaps.append(sha)
            info.source_sha256 = sha
            return info, snaps
        return None

    def _download(self, s: BrowserSession, hits: list[MediaHit], embed_url: str,
                  ctx: Context) -> tuple[list[dict[str, Any]], list[FetchResult], list[Any]]:
        """Each distinct media URL the player fetched, once, in full, through the same context."""
        from ..extract.probe import probe

        distinct: dict[str, MediaHit] = {}
        for h in hits:
            if h.status >= 400:
                continue
            first = distinct.setdefault(h.url, h)
            first.ranged = first.ranged or h.ranged
            first.total = first.total or h.total
        files: list[dict[str, Any]] = []
        fetches: list[FetchResult] = []
        gates: list[Any] = []
        os.makedirs(ctx.workdir, exist_ok=True)
        for url, h in list(distinct.items())[:MAX_MEDIA_URLS]:
            if h.total and h.total > CAP_BYTES:
                ctx.log.emit("media_skipped", reader=self.name, url=url[:300], reason="over 300 MB", bytes=h.total)
                continue
            body, status, headers, final, how = b"", 0, {}, url, ""
            t0 = time.monotonic()
            if not h.ranged and h.response is not None:
                try:
                    body, status, headers, how = h.response.body(), h.status, dict(h.response.headers), "player response"
                except Exception:
                    body = b""
            if not body:
                decision = ctx.gate.check(url, purpose="embed_media")
                gates.append(decision)
                if not decision.allowed:
                    continue
                try:
                    r = s.context.request.get(url, headers={"Referer": embed_url}, timeout=180_000,
                                              fail_on_status_code=False)
                    status, headers, final, how = r.status, dict(r.headers), r.url, "full re-request"
                    body = r.body() if status in (200, 206) else b""
                except Exception as exc:
                    ctx.log.emit("media_error", reader=self.name, url=url[:300], error=str(exc)[:200])
                    continue
                s._log(url=url, resource_type="media-refetch", method="GET", status=status)
            if not body or len(body) > CAP_BYTES:
                ctx.log.emit("media_skipped", reader=self.name, url=url[:300], status=status, bytes=len(body),
                             reason="empty" if not body else "over 300 MB")
                continue
            ct = headers.get("content-type", h.content_type)
            sha = ctx.store.put(body, content_type=ct, source_url=url)
            path = os.path.join(ctx.workdir, f"embed-{sha[:16]}{_ext(url, ct)}")
            with open(path, "wb") as fh:
                fh.write(body)
            info = probe(path)
            partial = status == 206 and h.total is not None and len(body) < h.total
            files.append({"path": path, "sha256": sha, "bytes": len(body), "url": url, "content_type": ct, "how": how,
                          "partial": partial, "fetched_at": now_iso(),
                          **{k: info[k] for k in ("duration_s", "has_audio", "has_video")}})
            fetches.append(FetchResult(url=url, final_url=final, status=status, headers=headers, bytes=len(body),
                                       sha256=sha, fetched_at=now_iso(), elapsed_ms=int((time.monotonic() - t0) * 1000),
                                       origin=getattr(ctx.net, "origin", "unknown"), user_agent=s.user_agent))
            del body
        return _choose(files), fetches, gates


def _choose(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One file with sound and one with pictures — the same file when one carries both. The rest are deleted."""
    usable = [f for f in files if f.get("has_audio") or f.get("has_video")]
    both = next((f for f in usable if f.get("has_audio") and f.get("has_video")), None)
    chosen = [both] if both else [x for x in (next((f for f in usable if f.get("has_audio")), None),
                                              next((f for f in usable if f.get("has_video")), None)) if x]
    for f in files:
        if f not in chosen:
            _unlink(f["path"])
    return chosen


def _unlink(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
