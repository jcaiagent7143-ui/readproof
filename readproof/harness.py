"""Harness.read(url): the read path of DESIGN §4, wired from one of each module.

    resolve (share text -> URL -> PostRef, every hop gated)
      -> reader chain for the platform, cheapest first; the first reader whose wall verdict is served wins
      -> media pass (only when the winner carried no media and the platform has a media-capable reader)
      -> extraction under the options: SPEECH (whisper.cpp), ON_SCREEN (frames + Vision OCR), IMAGE_TEXT (Vision OCR)
      -> every body in the store, every Record naming its snapshot, Records added to the store

Rule 6 is enforced here as well as in each reader: when no reader is served, ReadResult.records is empty and
wall / wall_signal say why; Records from a reader that was not served are never used, and the wall page's text is
never turned into a Record. The wall reported is the most specific one any reader saw (a deletion outranks a
challenge, which outranks "empty"), and wall_signal names every reader's verdict.

Media comes only from where the official-player principle allows it: files the official embed player itself
fetched (ReaderOutput.media), or URLs the served page itself listed (meta["media_urls"]), fetched with ONE GET each
through ctx.net — same declared identity, no cookies, Referer = the page that listed them. Carousel images
(meta["image_urls"]) are fetched the same way and read with OCR.

The core modules (profiles, robots, policy, net, log, evidence, budget) and the platform modules (platforms,
canonical, walls) are imported lazily, so this module imports cleanly while they are being written; tests inject
doubles through the keyword arguments after `options`.
"""

from __future__ import annotations

import hashlib
import importlib
import math
import os
import shutil
import time
import uuid
import weakref
from typing import Any, Callable
from urllib.parse import urlparse

from .interfaces import Context, ReaderOutput
from .readers.base import gated_fetch, header, store_fetch
from .types import Channel, FetchResult, GateDecision, PostRef, Profile, ReadResult, Record, WallType, WallVerdict

DEFAULT_OPTIONS: dict[str, Any] = {
    "speech": True, "on_screen": True, "images": True, "max_media_s": 600,
    "max_images": 9, "keep_media": False, "vlm": False, "min_host_interval_s": 2.0, "cap_usd": 1.0,
}

# Reader chains, cheapest first (DESIGN §4.5). The browser is the last resort and runs only when the profile allows.
TEXT_CHAIN: dict[str, list[str]] = {
    "youtube": ["oembed", "page", "browser"],
    "tiktok": ["oembed", "page", "browser"],
    "x": ["oembed", "page", "browser"],
    "xiaohongshu": ["page", "browser"],
    "weibo": ["page", "browser"],
    "wechat": ["page", "browser"],
    "toutiao": ["page", "browser"],
    "bilibili": ["page", "browser"],
    "douyin": ["embed", "browser"],
    "web": ["page", "browser"],
}
# Readers that can still supply media (or a carousel) after another reader served the text.
# TikTok: its page lists the stream but the CDN refuses it without the page's cookie (live 2026-11-21: 403 on all
# three addresses), so its official embed player, which plays for anonymous visitors, comes after the page.
MEDIA_CHAIN: dict[str, list[str]] = {"bilibili": ["embed"], "douyin": ["embed"], "tiktok": ["page", "embed"]}
REFUSAL_STATUSES = frozenset({401, 403, 412, 451})

# Most specific first: the wall reported when no reader is served.
WALL_PRIORITY = [
    WallType.DELETED, WallType.SELF_VISIBLE, WallType.TOKEN, WallType.APP_ONLY, WallType.REGION, WallType.LOGIN,
    WallType.CHALLENGE, WallType.RATE_LIMITED, WallType.SOFT_404, WallType.POLICY, WallType.EMPTY, WallType.NETWORK,
    WallType.UNKNOWN, WallType.UNSUPPORTED,
]
MEDIA_CAP_BYTES = 300 * 1024 * 1024
IMAGE_CAP_BYTES = 20 * 1024 * 1024
_IMAGE_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic",
              "image/avif": ".avif", "image/gif": ".gif"}

Resolver = Callable[[str, Context], Any]
ReaderFactory = Callable[[str, Callable[[PostRef], Any]], Any]


def _core(name: str) -> Any:
    return importlib.import_module(f"readproof.{name}")


def default_resolver(text: str, ctx: Context) -> Any:
    return _core("canonical").resolve(text, ctx)


def default_reader(name: str, spec_for: Callable[[PostRef], Any]) -> Any:
    from .readers.browser import BrowserReader
    from .readers.embed import EmbedReader
    from .readers.oembed import OEmbedReader
    from .readers.page import PageReader

    return {"oembed": OEmbedReader, "page": PageReader, "browser": BrowserReader, "embed": EmbedReader}[name](spec_for=spec_for)


def web_ref(url: str) -> PostRef:
    """A page no platform module claims: read as generic web, keyed by the URL's hash."""
    return PostRef(platform="web", post_id=hashlib.sha256(url.encode("utf-8")).hexdigest()[:16], form="web", url=url)


class _Pacer:
    """At least `interval` seconds between requests to one host (DESIGN §10), across readers and media GETs."""

    def __init__(self, interval: float) -> None:
        self.interval = max(0.0, float(interval))
        self.last: dict[str, float] = {}

    def wait(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        if self.interval and host in self.last:
            gap = self.interval - (time.monotonic() - self.last[host])
            if gap > 0:
                time.sleep(gap)

    def mark(self, urls: list[str]) -> None:
        now = time.monotonic()
        for u in urls:
            host = urlparse(u).hostname
            if host:
                self.last[host] = now


def _close_quietly(objs: list[Any]) -> None:
    for obj in objs:
        closer = getattr(obj, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass


def _remaining(budget: Any) -> float:
    try:
        v = float(budget.remaining_usd())
    except Exception:
        return float("nan")
    return v


def pick_wall(outputs: list[ReaderOutput]) -> WallVerdict:
    """The most specific wall any reader saw, with every reader's verdict named in the signal."""
    if not outputs:
        return WallVerdict(False, WallType.UNSUPPORTED, "no reader applies to this post under this profile")

    def rank(o: ReaderOutput) -> int:
        w = o.verdict.wall or WallType.UNKNOWN
        return WALL_PRIORITY.index(w) if w in WALL_PRIORITY else len(WALL_PRIORITY)

    best = min(outputs, key=rank)
    signal = f"{best.reader}: {best.verdict.signal}"
    others = [f"{o.reader}={(o.verdict.wall or WallType.UNKNOWN).value}" for o in outputs if o is not best]
    if others:
        signal += " | also " + ", ".join(others)
    return WallVerdict(False, best.verdict.wall or WallType.UNKNOWN, signal, best.verdict.visible_chars)


class Harness:
    """One profile, one origin, one evidence root. `read()` is DESIGN §4; see the module docstring."""

    def __init__(self, profile: Profile | str, *, root: str = "runs", origin: str = "residential:unknown",
                 options: dict[str, Any] | None = None, net: Any = None, gate: Any = None, log: Any = None,
                 store: Any = None, budget: Any = None, resolver: Resolver | None = None,
                 reader_factory: ReaderFactory | None = None) -> None:
        if isinstance(profile, str):
            profile = _core("profiles").get(profile)
        self.profile: Profile = profile
        self.root = root
        self.origin = origin
        self.options = {**DEFAULT_OPTIONS, **(options or {})}
        self.run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        os.makedirs(root, exist_ok=True)
        self.log = log if log is not None else _core("log").JsonlLog(os.path.join(root, "logs", f"{self.run_id}.jsonl"),
                                                                    run_id=self.run_id)
        self.store = store if store is not None else _core("evidence").FileStore(os.path.join(root, "store"))
        self.budget = budget if budget is not None else _core("budget").PricedBudget(
            cap_usd=float(self.options["cap_usd"]), log=self.log)
        net_mod = None
        if gate is None:
            # net.make_stack is the one correct wiring: robots.txt fetched through the same HttpNet with
            # purpose="robots" (so the gate never recurses), every other request gated first.
            net_mod = _core("net")
            gate, stack_net = net_mod.make_stack(profile, origin=origin, log=self.log)
            net = net if net is not None else stack_net
        elif net is None:
            net_mod = _core("net")
            net = net_mod.HttpNet(profile, origin=origin, gate=gate, log=self.log)
        self.gate = gate
        self.net = net
        self.resolver = resolver or default_resolver
        self.reader_factory = reader_factory or default_reader
        # The log is fsync'd and closed, and the HTTP client released, by close() — or when the Harness is collected,
        # for callers that never close it. Idempotent.
        self._finalizer = weakref.finalize(self, _close_quietly, [self.net, self.log])

    def context(self, read_id: str | None = None) -> Context:
        read_id = read_id or (time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6])
        return Context(profile=self.profile, gate=self.gate, net=self.net, log=self.log, store=self.store,
                       budget=self.budget, workdir=os.path.join(self.root, "work", read_id),
                       keep_media=bool(self.options.get("keep_media")), options=dict(self.options))

    def read(self, text_or_url: str) -> ReadResult:
        ctx = self.context()
        try:
            return read_in(ctx, text_or_url, resolver=self.resolver, reader_factory=self.reader_factory)
        finally:
            if not ctx.keep_media:
                shutil.rmtree(ctx.workdir, ignore_errors=True)

    def close(self) -> None:
        self._finalizer()

    def __enter__(self) -> "Harness":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# --- the read path -----------------------------------------------------------------------------------------


def read_in(ctx: Context, text_or_url: str, *, resolver: Resolver = default_resolver,
            reader_factory: ReaderFactory = default_reader) -> ReadResult:
    """DESIGN §4 inside one Context. Pure wiring: every decision is a reader's, the judge's or the gate's."""
    t0 = time.monotonic()
    usd0 = _remaining(ctx.budget)
    opts = {**DEFAULT_OPTIONS, **(ctx.options or {})}
    ctx.options = opts
    pacer = _Pacer(opts.get("min_host_interval_s", 0.0))
    ctx.log.emit("read_start", input=text_or_url[:500], profile=ctx.profile.name,
                 origin=getattr(ctx.net, "origin", "unknown"), options={k: opts[k] for k in sorted(opts)})
    res = resolver(text_or_url, ctx)
    result = ReadResult(url=text_or_url, post=getattr(res, "ref", None), served=False, wall=None, wall_signal="",
                        fetches=list(getattr(res, "fetches", []) or []), gate=list(getattr(res, "gate", []) or []))
    snapshots: list[str] = []
    for f in result.fetches:
        sha = store_fetch(ctx, f)
        if sha and f.bytes:
            snapshots.append(sha)
    pacer.mark([u for f in result.fetches for u in (f.url, f.final_url)])

    def finish(verdict: WallVerdict, records: list[Record] | None = None, reader: str | None = None) -> ReadResult:
        result.served = verdict.served
        result.wall = None if verdict.served else (verdict.wall or WallType.UNKNOWN)
        result.wall_signal = verdict.signal
        result.records = list(records or []) if verdict.served else []  # rule 6
        result.reader = reader if verdict.served else None
        result.snapshots = list(dict.fromkeys(snapshots + [f.sha256 for f in result.fetches if f.bytes]))
        usd1 = _remaining(ctx.budget)
        cost = usd0 - usd1
        result.cost_usd = round(cost, 6) if math.isfinite(cost) else 0.0
        result.seconds = round(time.monotonic() - t0, 3)
        ctx.log.emit("read_end", served=result.served, wall=result.wall.value if result.wall else None,
                     wall_signal=result.wall_signal[:500], reader=result.reader,
                     post_key=result.post.key if result.post else None, records=len(result.records),
                     channels=sorted(c.value for c in result.channels()), snapshots=len(result.snapshots),
                     seconds=result.seconds, cost_usd=result.cost_usd)
        return result

    early = getattr(res, "verdict", None)
    if early is not None and not early.served:
        return finish(early)
    url = getattr(res, "url", None)
    if not url:
        return finish(WallVerdict(False, WallType.UNSUPPORTED, "no URL found in the input"))
    ref, spec = getattr(res, "ref", None), getattr(res, "spec", None)
    if ref is None and spec is not None:
        return finish(WallVerdict(False, WallType.UNSUPPORTED, f"not a post URL for {getattr(spec, 'name', '?')}: {url}"))
    if ref is None:
        ref = web_ref(url)
    result.post = ref

    def spec_for(_: PostRef) -> Any:
        return spec

    outputs: list[ReaderOutput] = []
    winner: ReaderOutput | None = None
    for name in TEXT_CHAIN.get(ref.platform, TEXT_CHAIN["web"]):
        out = _run_reader(name, ref, ctx, reader_factory, spec_for, pacer, result)
        if out is None:
            continue
        outputs.append(out)
        if out.verdict.served:
            winner = out
            break
    if winner is None:
        return finish(pick_wall(outputs))
    for out in outputs:
        snapshots.extend(_meta_snapshots(out))

    # media: the winner's own (the player's files, or ONE GET of what the served page listed), else the platform's
    # media-capable readers in order, until one yields media that plays (page-listed first, official player last)
    sources, files = _acquire(ctx, ref, winner, outputs, reader_factory, spec_for, pacer, result)
    for out in sources[1:]:
        snapshots.extend(_meta_snapshots(out))
    records = list(winner.records)
    try:
        got, snaps = _extract(ctx, ref, winner, sources, files, result, pacer)
        records += got
        snapshots.extend(snaps)
    finally:
        for f in result.fetches:
            if f.body is not None and f.bytes > 5_000_000:
                f.body = None  # large media bodies are in the store; do not hold them in the result
    ctx.store.add_records(records)
    return finish(winner.verdict, records, winner.reader)


def _kinds(out: ReaderOutput) -> list[str]:
    return list(out.meta.get("image_kinds") or [])


def _acquire(ctx: Context, ref: PostRef, winner: ReaderOutput, outputs: list[ReaderOutput], factory: ReaderFactory,
             spec_for: Callable[[PostRef], Any], pacer: _Pacer,
             result: ReadResult) -> tuple[list[ReaderOutput], list[dict[str, Any]]]:
    """(served outputs to take media and images from, winner first; media files for extraction)."""
    opts = ctx.options
    want_av = bool(opts.get("speech") or opts.get("on_screen"))
    sources = [winner]
    files = _media_of(ctx, winner, ref, result, pacer) if want_av else []
    # A TikTok photo post's carousel is on its page only: when the winner was not the page, the page is asked once.
    need_carousel = (bool(opts.get("images")) and ref.platform == "tiktok" and winner.reader != "page"
                     and "carousel" not in _kinds(winner))
    ran = {o.reader for o in outputs}
    for name in MEDIA_CHAIN.get(ref.platform, []):
        if not ((want_av and not files) or need_carousel):
            break
        if name in ran:
            continue
        ran.add(name)
        out = _run_reader(name, ref, ctx, factory, spec_for, pacer, result, purpose="media_pass")
        if out is None or not out.verdict.served:
            continue
        sources.append(out)
        if out.reader == "page":
            need_carousel = False  # the page has said which images the post has
        if want_av and not files:
            files = _media_of(ctx, out, ref, result, pacer)
    return sources, files


def _media_of(ctx: Context, out: ReaderOutput, ref: PostRef, result: ReadResult, pacer: _Pacer) -> list[dict[str, Any]]:
    """Media files a served output carries: the player's own files, else ONE GET of the URLs its page listed."""
    if out.media:
        info = {m.get("path"): m for m in out.meta.get("media") or [] if isinstance(m, dict)}
        return [info.get(path) or _probe_file(path, out) for path in out.media]
    urls = [u for u in out.meta.get("media_urls") or [] if isinstance(u, str)]
    if not urls:
        return []
    got = _fetch_media(ctx, urls, str(out.meta.get("page_url") or ref.url), result, pacer)
    return [got] if got else []


def _run_reader(name: str, ref: PostRef, ctx: Context, factory: ReaderFactory, spec_for: Callable[[PostRef], Any],
                pacer: _Pacer, result: ReadResult, *, purpose: str = "chain") -> ReaderOutput | None:
    reader = factory(name, spec_for)
    try:
        applies = reader.applies(ref, ctx)
    except Exception as exc:
        applies = False
        ctx.log.emit("reader_error", reader=name, stage="applies", error=f"{type(exc).__name__}: {exc}"[:300])
    if not applies:
        ctx.log.emit("reader_skipped", reader=name, post_key=ref.key, purpose=purpose,
                     reason="does not apply (profile, platform or missing dependency)")
        return None
    pacer.wait(ref.url)
    t0 = time.monotonic()
    try:
        out = reader.read(ref, ctx)
    except Exception as exc:  # a reader bug is a verdict, not a crash of the read
        out = ReaderOutput(name, WallVerdict(False, WallType.UNKNOWN, f"reader error: {type(exc).__name__}: {exc}"[:300]))
        ctx.log.emit("reader_error", reader=name, stage="read", error=f"{type(exc).__name__}: {exc}"[:300])
    if not out.verdict.served:
        out.records = []  # rule 6, whatever a reader returned
    result.fetches.extend(out.fetches)
    result.gate.extend(out.gate)
    pacer.mark([u for f in out.fetches for u in (f.url, f.final_url)])
    ctx.log.emit("reader", reader=name, purpose=purpose, post_key=ref.key, served=out.verdict.served,
                 wall=out.verdict.wall.value if out.verdict.wall else None, signal=out.verdict.signal[:500],
                 visible_chars=out.verdict.visible_chars, records=len(out.records), media=len(out.media),
                 media_urls=len(out.meta.get("media_urls") or []), image_urls=len(out.meta.get("image_urls") or []),
                 judge=out.meta.get("judge"), elapsed_ms=int((time.monotonic() - t0) * 1000))
    return out


def _meta_snapshots(out: ReaderOutput) -> list[str]:
    snaps = [s for s in (out.meta.get("snapshots") or []) if isinstance(s, str)]
    if isinstance(out.meta.get("screenshot"), str):
        snaps.append(out.meta["screenshot"])
    return snaps


def _budget_exceeded(exc: BaseException) -> bool:
    return type(exc).__name__ == "BudgetExceeded"


def _extract(ctx: Context, ref: PostRef, winner: ReaderOutput, sources: list[ReaderOutput], files: list[dict[str, Any]],
             result: ReadResult, pacer: _Pacer) -> tuple[list[Record], list[str]]:
    """SPEECH / ON_SCREEN / IMAGE_TEXT (and optional FRAME / IMAGE) Records for a served read."""
    opts = ctx.options
    records: list[Record] = []
    snaps: list[str] = [f["sha256"] for f in files if f.get("sha256")]
    os.makedirs(ctx.workdir, exist_ok=True)
    page_url = str(winner.meta.get("page_url") or ref.url)
    max_s = float(opts.get("max_media_s") or 0) or None
    stopped = False
    try:
        audio = next((f for f in files if f.get("has_audio")), None)
        video = next((f for f in files if f.get("has_video")), None)
        if audio and opts.get("speech"):
            records += _safe(ctx, "asr", lambda: _asr(ctx, ref, audio, max_s))
        if video and (opts.get("on_screen") or opts.get("vlm")):
            got, frame_snaps = _safe(ctx, "frames", lambda: _on_screen(ctx, ref, video, max_s), default=([], []))
            records += got
            snaps.extend(frame_snaps)
        if opts.get("images"):
            # a carousel wherever one was found, else the first output's cover
            ordered = sorted(sources, key=lambda o: "carousel" not in _kinds(o))
            for out in ordered:
                urls = [u for u in out.meta.get("image_urls") or [] if isinstance(u, str)]
                if urls:
                    got, image_snaps = _safe(ctx, "images", lambda: _images(ctx, ref, urls, page_url, result, pacer),
                                             default=([], []))
                    records += got
                    snaps.extend(image_snaps)
                    break
    except Exception as exc:
        if not _budget_exceeded(exc):
            raise
        stopped = True
        ctx.log.emit("budget_exceeded", post_key=ref.key, error=str(exc)[:300])
    if stopped:
        ctx.log.emit("extract_stopped", post_key=ref.key, reason="budget")
    return records, snaps


def _safe(ctx: Context, tool: str, fn: Callable[[], Any], default: Any = None) -> Any:
    """One extraction step: its failure is logged and the read stays served; a budget stop propagates."""
    try:
        return fn()
    except Exception as exc:
        if _budget_exceeded(exc):
            raise
        ctx.log.emit("extract_error", tool=tool, error=f"{type(exc).__name__}: {exc}"[:400])
        return [] if default is None else default


def _probe_file(path: str, out: ReaderOutput) -> dict[str, Any]:
    from .extract.probe import probe
    from .types import sha256_bytes

    with open(path, "rb") as fh:
        sha = sha256_bytes(fh.read())
    return {"path": path, "sha256": sha, "fetched_at": out.fetches[0].fetched_at if out.fetches else "", **probe(path)}


def _fetch_media(ctx: Context, urls: list[str], referer: str, result: ReadResult, pacer: _Pacer) -> dict[str, Any] | None:
    """The page-listed stream: ONE GET per URL (alternatives in the page's order), stopping at the first that plays."""
    from .extract.probe import probe

    for url in urls[:3]:
        pacer.wait(url)
        decision, fetch = gated_fetch(ctx, url, purpose="media", headers={"Referer": referer},
                                      max_bytes=MEDIA_CAP_BYTES)
        result.gate.append(decision)
        pacer.mark([url])
        if fetch is None:
            ctx.log.emit("media_fetch", url=url[:300], ok=False, reason=f"gate:{decision.reason}")
            break
        result.fetches.append(fetch)
        sha = store_fetch(ctx, fetch) or fetch.sha256  # a refusal's body is evidence too
        ok = fetch.error is None and fetch.status in (200, 206) and bool(fetch.body)
        if not ok:
            ctx.log.emit("media_fetch", url=url[:300], ok=False, status=fetch.status, error=fetch.error, bytes=fetch.bytes)
            if fetch.status in REFUSAL_STATUSES or (fetch.error or "").startswith("gate_refused"):
                # A refusal is an answer, not an outage: the page's other addresses for the same stream are not
                # tried (rule 4: nothing is retried after 401/403/412, and another mirror would be a retry).
                ctx.log.emit("media_refused", url=url[:300], status=fetch.status, untried=len(urls[:3]) - urls.index(url) - 1)
                break
            continue
        ext = ".mp4" if "mp4" in header(fetch, "content-type") or ".mp4" in urlparse(url).path else ".media"
        os.makedirs(ctx.workdir, exist_ok=True)
        path = os.path.join(ctx.workdir, f"media-{sha[:16]}{ext}")
        with open(path, "wb") as fh:
            fh.write(fetch.body or b"")
        fetch.body = None
        info = probe(path)
        ctx.log.emit("media_fetch", url=url[:300], ok=True, status=fetch.status, bytes=fetch.bytes, sha256=sha,
                     duration_s=info.get("duration_s"), has_audio=info.get("has_audio"), has_video=info.get("has_video"))
        if info.get("has_audio") or info.get("has_video"):
            return {"path": path, "sha256": sha, "fetched_at": fetch.fetched_at, "url": url, **info}
    return None


def _asr(ctx: Context, ref: PostRef, media: dict[str, Any], max_s: float | None) -> list[Record]:
    from .extract import asr

    return asr.transcribe(media["path"], ctx, post_key=ref.key, source_sha256=media["sha256"],
                          fetched_at=media.get("fetched_at") or "", language=ctx.options.get("language"), max_s=max_s)


def _on_screen(ctx: Context, ref: PostRef, media: dict[str, Any], max_s: float | None) -> tuple[list[Record], list[str]]:
    from .extract import frames as frames_mod
    from .extract import ocr, vlm

    sampled = frames_mod.frames_for(media["path"], ctx, max_s=max_s)
    keyed: list[tuple[float, str, str]] = []
    for t, path in sampled:
        with open(path, "rb") as fh:
            sha = ctx.store.put(fh.read(), content_type="image/jpeg", source_url=f"frame:{media['sha256']}@{t}")
        keyed.append((t, path, sha))
    fetched_at = media.get("fetched_at") or ""
    records: list[Record] = []
    if ctx.options.get("on_screen"):
        records += ocr.ocr_frames(keyed, ctx, post_key=ref.key, fetched_at=fetched_at)
    if vlm.enabled(ctx):
        records += vlm.describe(keyed, ctx, kind=Channel.FRAME, post_key=ref.key, fetched_at=fetched_at)
    return records, [sha for _, _, sha in keyed]


def _images(ctx: Context, ref: PostRef, urls: list[str], referer: str, result: ReadResult,
            pacer: _Pacer) -> tuple[list[Record], list[str]]:
    from .extract import ocr, vlm

    keyed: list[tuple[int, str, str]] = []
    fetched_at = ""
    refused_hosts: set[str] = set()
    for index, url in enumerate(urls[: int(ctx.options.get("max_images") or 9)]):
        host = urlparse(url).hostname or ""
        if host in refused_hosts:
            ctx.log.emit("image_fetch", url=url[:300], ok=False, reason="host refused an earlier image; not asked again")
            continue
        pacer.wait(url)
        decision, fetch = gated_fetch(ctx, url, purpose="image", headers={"Referer": referer}, max_bytes=IMAGE_CAP_BYTES)
        result.gate.append(decision)
        pacer.mark([url])
        if fetch is None:
            continue
        result.fetches.append(fetch)
        sha = store_fetch(ctx, fetch) or fetch.sha256  # a refusal's body is evidence too
        if fetch.error or fetch.status != 200 or not fetch.body:
            ctx.log.emit("image_fetch", url=url[:300], ok=False, status=fetch.status, error=fetch.error)
            if fetch.status in REFUSAL_STATUSES or fetch.status == 429:
                refused_hosts.add(host)
            continue
        ct = header(fetch, "content-type").split(";")[0].strip().lower()
        os.makedirs(ctx.workdir, exist_ok=True)
        path = os.path.join(ctx.workdir, f"img-{index:02d}-{sha[:12]}{_IMAGE_EXT.get(ct, '.img')}")
        with open(path, "wb") as fh:
            fh.write(fetch.body)
        keyed.append((index, path, sha))
        fetched_at = fetched_at or fetch.fetched_at
    records: list[Record] = []
    if keyed and ctx.options.get("images"):
        records += ocr.ocr_images(keyed, ctx, post_key=ref.key, fetched_at=fetched_at)
    if keyed and vlm.enabled(ctx):
        records += vlm.describe(keyed, ctx, kind=Channel.IMAGE, post_key=ref.key, fetched_at=fetched_at)
    return records, [sha for _, _, sha in keyed]


def summary(result: ReadResult) -> dict[str, Any]:
    """A JSON-ready view of a ReadResult (bodies never included)."""
    from dataclasses import asdict

    per_channel: dict[str, int] = {}
    for r in result.records:
        per_channel[r.channel.value] = per_channel.get(r.channel.value, 0) + 1
    return {
        "url": result.url, "post": asdict(result.post) if result.post else None, "served": result.served,
        "wall": result.wall.value if result.wall else None, "wall_signal": result.wall_signal, "reader": result.reader,
        "records_per_channel": per_channel, "records": [r.to_json() for r in result.records],
        "snapshots": result.snapshots, "fetches": [f.to_log() for f in result.fetches],
        "gate": [asdict(g) for g in result.gate], "cost_usd": result.cost_usd, "seconds": result.seconds,
    }
