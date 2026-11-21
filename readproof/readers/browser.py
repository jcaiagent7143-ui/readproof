"""The browser reader: headless Chromium, one fresh context per read, the last resort of the chain.

Identity (rules 1-2): the user agent is Chromium's own default string — read from the launched browser, so it
still says "HeadlessChrome" — with the declared ReadProof token appended. Every read opens a new browser context
with no storage state (an empty cookie jar), locale zh-CN and a 1280x900 viewport, and closes and discards it after
the read; nothing a page sets survives into the next read. Automation is not hidden (navigator.webdriver stays
true; no stealth flags).

Design note — what is gated and what is only logged. The top-level navigation URL is checked with ctx.gate before
the browser starts, and so is any later main-frame navigation a page script starts. The other requests a page
makes (scripts, styles, images, XHR, media) are made by the page's own code, as in any browser a person uses; they
are not gated, but every one is logged as a "subrequest" event (url, resource type, status). Two exceptions are
enforced in the route handler: requests to literal private/loopback/link-local addresses or localhost are refused
(SSRF hygiene, rule 5), and, in this reader, media streams are aborted (it reads text and takes a screenshot; the
embed reader is the one that keeps media).

What it returns: the rendered page's visible innerText, document.title (CAPTION title), whatever inline state the
rendered HTML still carries (the page reader's parsers), the final URL, and a full-page PNG screenshot stored by
sha256 in meta["screenshot"] — the audit trail for gold labels, kept for walls too (a wall's screenshot is
evidence, never a Record). The innerText is always stored (sha256 in meta["inner_text"]); it becomes PAGE Records
only when the rendered page carries the post in no structure the parsers recognise (platform state, an article
body). Live on 2026-11-21 a served Douyin page's innerText was navigation, licences and eight other videos'
captions, and a served Toutiao article's was the article plus the site's hot list: text an agent must never cite
as this post's words when the post's own fields are there. The verdict comes from walls.judge on a FetchResult
built from the rendered HTML, with the main response's status.
"""

from __future__ import annotations

import glob
import ipaddress
import os
import re
import time
from typing import Any, Callable
from urllib.parse import urlparse

from ..interfaces import Context, ReaderOutput
from ..types import Channel, FetchResult, GateDecision, PostRef, WallType, WallVerdict, sha256_bytes
from .base import SpecFor, default_spec_for, judge, make_record, now_iso, refused, store_fetch, target_url

NAV_TIMEOUT_S = 25.0
SETTLE_S = 4.0
# Single-page apps render the post after their scripts fetch it: m.weibo.cn showed an empty shell (title 「微博」, no
# visible text) after the settle for 4 of 11 live posts on 2026-11-21, while load + 3 s showed the post. Those hosts,
# and any page with almost no visible text after the settle, get one more look: the load event, then up to
# SPA_EXTRA_S more for text to appear, then innerText is read again once.
SPA_HOSTS = ("m.weibo.cn",)
SPA_EXTRA_S = 6.0
SPA_MIN_CHARS = 40
VIEWPORT = {"width": 1280, "height": 900}
LOCALE = "zh-CN"
SCREENSHOT_MAX_H = 16_000
_URL_LOG_CHARS = 2000


_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def utf8_safe(text: str) -> str:
    """The text with every lone UTF-16 surrogate replaced by U+FFFD.

    A rendered DOM can hold half of a surrogate pair (a page script cut an emoji in two; live 2026-11-21 on a Toutiao
    article): Python keeps it as a lone surrogate, and encoding the page to UTF-8 for the store then raises, which used
    to turn the whole read into a reader error. Valid pairs are single code points in a Python str, so only the halves
    are touched."""
    return _LONE_SURROGATE.sub("\ufffd", text or "")


def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return False
    return True


def installed_chromium() -> str | None:
    """The newest Chromium build already in Playwright's cache (headless shell first), or None.

    Used only when the pinned build this Playwright version wants is not installed: browsers are never downloaded
    by ReadProof."""
    root = os.path.expanduser(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "~/Library/Caches/ms-playwright")
    patterns = [
        "chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell",
        "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
        "chromium-*/chrome-linux*/chrome",
    ]
    for pat in patterns:
        found = glob.glob(os.path.join(root, pat))
        if found:
            def build(p: str) -> int:
                m = re.search(r"-(\d+)/", p)
                return int(m.group(1)) if m else 0
            return max(found, key=build)
    return None


def launch_chromium(pw: Any) -> Any:
    """Launch headless Chromium: READPROOF_CHROMIUM, else Playwright's own build, else the newest installed one."""
    exe = os.environ.get("READPROOF_CHROMIUM") or None
    try:
        return pw.chromium.launch(headless=True, executable_path=exe)
    except Exception as exc:
        if exe or "Executable doesn't exist" not in str(exc):
            raise
        fallback = installed_chromium()
        if not fallback:
            raise
        return pw.chromium.launch(headless=True, executable_path=fallback)


def private_host(host: str) -> bool:
    """A literal private/loopback/link-local/reserved address or a local name (no DNS lookup: cheap, per request)."""
    h = (host or "").strip("[]").lower()
    if not h:
        return False
    if h == "localhost" or h.endswith((".localhost", ".local", ".internal", ".lan")):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified


def declared_token(ctx: Context) -> str:
    ua = ctx.profile.user_agent or ""
    if ua.startswith("ReadProof/"):
        return ua
    from .. import USER_AGENT

    return USER_AGENT


class BrowserSession:
    """One fresh Chromium context for one read. Use as a context manager; everything is closed on exit."""

    def __init__(self, ctx: Context, *, reader: str, block_media: bool) -> None:
        self.ctx = ctx
        self.reader = reader
        self.block_media = block_media
        self.gate_decisions: list[GateDecision] = []
        self.response_hooks: list[Callable[[Any], None]] = []
        self.subrequests = 0
        self.blocked = 0
        self.user_agent = ""
        self._gated: set[str] = set()
        self._aborted: set[int] = set()
        self._pw = self._browser = self.context = self.page = None
        self.main_response: Any = None
        self.nav_error: str | None = None
        self.nav_ms = 0
        self.requested_url = ""
        # Set once a page has loaded when the reader only interacts with it (the embed reader pressing play):
        # a click must not take the tab to another page.
        self.lock_navigation = False

    # lifecycle
    def __enter__(self) -> "BrowserSession":
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        try:
            self._browser = launch_chromium(self._pw)
            probe = self._browser.new_context()
            try:
                default_ua = probe.new_page().evaluate("navigator.userAgent")
            finally:
                probe.close()
            self.user_agent = f"{default_ua} {declared_token(self.ctx)}"
            self.context = self._browser.new_context(
                user_agent=self.user_agent, locale=LOCALE, viewport=VIEWPORT, storage_state=None,
                accept_downloads=False, service_workers="block",
            )
            self.context.route("**/*", self._route)
            self.context.on("response", self._on_response)
            self.context.on("requestfailed", self._on_failed)
            self.page = self.context.new_page()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc: Any) -> None:
        for closer in (self.context, self._browser):
            try:
                if closer is not None:
                    closer.close()
            except Exception:
                pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self.context = self._browser = self._pw = self.page = None

    # request plumbing
    def _log(self, **fields: Any) -> None:
        url = fields.get("url")
        if isinstance(url, str) and len(url) > _URL_LOG_CHARS:
            fields["url"] = url[:_URL_LOG_CHARS] + "…"
        self.ctx.log.emit("subrequest", reader=self.reader, **fields)

    def _route(self, route: Any) -> None:
        req = route.request
        url = req.url
        try:
            if url.startswith(("data:", "blob:", "about:")):
                route.continue_()
                return
            host = urlparse(url).hostname or ""
            if private_host(host):
                self._abort(route, req, "blocked:private_address")
                return
            if self.lock_navigation and self._is_main_navigation(req):
                self._abort(route, req, "blocked:navigation_locked")
                return
            if self._is_main_navigation(req) and url not in self._gated:
                decision = self.ctx.gate.check(url, purpose="browser")
                self.gate_decisions.append(decision)
                self._gated.add(url)
                if not decision.allowed:
                    self._abort(route, req, f"blocked:gate:{decision.reason}")
                    return
            if self.block_media and req.resource_type == "media":
                self._abort(route, req, "aborted:media")
                return
            route.continue_()
        except Exception:
            try:
                route.continue_()
            except Exception:
                pass

    def _abort(self, route: Any, req: Any, why: str) -> None:
        self.blocked += 1
        self._aborted.add(id(req))
        self._log(url=req.url, resource_type=req.resource_type, method=req.method, status=why)
        route.abort("blockedbyclient")

    def _is_main_navigation(self, req: Any) -> bool:
        try:
            return bool(req.is_navigation_request()) and self.page is not None and req.frame == self.page.main_frame
        except Exception:
            return False

    def _on_response(self, resp: Any) -> None:
        self.subrequests += 1
        try:
            req = resp.request
            self._log(url=resp.url, resource_type=req.resource_type, method=req.method, status=resp.status)
        except Exception:
            pass
        for hook in self.response_hooks:
            try:
                hook(resp)
            except Exception:
                pass

    def _on_failed(self, req: Any) -> None:
        if id(req) in self._aborted:
            return
        self.subrequests += 1
        try:
            self._log(url=req.url, resource_type=req.resource_type, method=req.method, status=None,
                      error=(req.failure or "")[:200])
        except Exception:
            pass

    # reading
    def open(self, url: str, *, nav_timeout_s: float = NAV_TIMEOUT_S, settle_s: float = SETTLE_S) -> None:
        """Navigate (the caller has already gated `url`), then let the page settle for up to settle_s."""
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        self.requested_url = url
        self._gated.add(url)
        t0 = time.monotonic()
        try:
            self.main_response = self.page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_s * 1000)
        except PlaywrightTimeout:
            self.nav_error = f"navigation timeout {nav_timeout_s:.0f}s"
        except PlaywrightError as exc:
            self.nav_error = f"navigation error: {str(exc).splitlines()[0][:200]}"
        self.nav_ms = int((time.monotonic() - t0) * 1000)
        if self.nav_error is None:
            self.settle(settle_s)

    def settle(self, seconds: float) -> None:
        from playwright.sync_api import Error as PlaywrightError

        try:
            self.page.wait_for_load_state("networkidle", timeout=seconds * 1000)
        except PlaywrightError:
            pass  # social pages keep connections open; the settle is a ceiling, not a requirement

    def wait(self, seconds: float) -> None:
        self.page.wait_for_timeout(seconds * 1000)

    def wait_for_content(self, extra_s: float = SPA_EXTRA_S, min_chars: int = SPA_MIN_CHARS) -> None:
        """The load event (at most extra_s), then up to extra_s more until the body shows min_chars of text and the
        network has gone quiet. Every wait is a ceiling: a page that never fills is simply read again as it is."""
        from playwright.sync_api import Error as PlaywrightError

        try:
            self.page.wait_for_load_state("load", timeout=extra_s * 1000)
        except PlaywrightError:
            pass
        deadline = time.monotonic() + extra_s
        js = f"() => !!document.body && (document.body.innerText || '').trim().length >= {int(min_chars)}"
        for step in ("content", "networkidle"):
            left = deadline - time.monotonic()
            if left <= 0.05:
                break
            try:
                if step == "content":
                    self.page.wait_for_function(js, timeout=left * 1000)
                else:
                    self.page.wait_for_load_state("networkidle", timeout=left * 1000)
            except PlaywrightError:
                pass

    def snapshot(self) -> tuple[str, str, str, str]:
        """(rendered html, document.title, visible innerText, final url)."""
        for attempt in range(2):
            try:
                html = self.page.content()
                title = self.page.title()
                text = self.page.evaluate("() => (document.body && document.body.innerText) || ''") or ""
                return utf8_safe(html), utf8_safe(title), utf8_safe(text), self.page.url
            except Exception:
                if attempt:
                    return "", "", "", self.page.url if self.page else self.requested_url
                self.page.wait_for_timeout(500)
        return "", "", "", self.requested_url

    def screenshot(self) -> bytes:
        try:
            height = int(self.page.evaluate(
                "() => Math.max(document.documentElement ? document.documentElement.scrollHeight : 0,"
                " document.body ? document.body.scrollHeight : 0)") or 0)
            if height > SCREENSHOT_MAX_H:
                return self.page.screenshot(type="png", full_page=True, timeout=20_000,
                                            clip={"x": 0, "y": 0, "width": VIEWPORT["width"], "height": SCREENSHOT_MAX_H})
            return self.page.screenshot(type="png", full_page=True, timeout=20_000)
        except Exception:
            try:
                return self.page.screenshot(type="png", timeout=10_000)
            except Exception:
                return b""

    def redirects(self) -> list[str]:
        """The URLs the main navigation was redirected through, after the requested one, ending at the final one."""
        chain: list[str] = []
        try:
            req = self.main_response.request if self.main_response is not None else None
            while req is not None:
                chain.insert(0, req.url)
                req = req.redirected_from
        except Exception:
            return []
        return chain[1:] if len(chain) > 1 else []

    def fetch_result(self, html: str, final_url: str) -> FetchResult:
        body = utf8_safe(html).encode("utf-8", errors="replace")
        status = None
        headers: dict[str, str] = {}
        if self.main_response is not None:
            try:
                status = self.main_response.status
                headers = dict(self.main_response.headers)
            except Exception:
                pass
        headers["content-type"] = "text/html; charset=utf-8"  # the body is the rendered DOM, serialised as UTF-8
        # A navigation that failed or never reached DOMContentLoaded is a network failure, whatever the tab shows:
        # Chromium's own error page ("This site can't be reached") must never be judged as the post.
        error = None
        if self.nav_error and (self.main_response is None or not html):
            error = self.nav_error
        elif (final_url or "").startswith("chrome-error://"):
            error = "chromium error page"
        elif not html:
            error = "no document"
        return FetchResult(
            url=self.requested_url, final_url=final_url or self.requested_url, status=status, headers=headers,
            bytes=len(body), sha256=sha256_bytes(body), fetched_at=now_iso(), elapsed_ms=self.nav_ms,
            origin=getattr(self.ctx.net, "origin", "unknown"), user_agent=self.user_agent, method="GET",
            redirects=self.redirects(), error=error, body=body,
        )


def needs_second_look(url: str, text: str) -> bool:
    """A single-page-app host, or a page whose visible text is still under SPA_MIN_CHARS after the settle."""
    host = (urlparse(url or "").hostname or "").lower()
    return host in SPA_HOSTS or len((text or "").strip()) < SPA_MIN_CHARS


def final_url_gate(ctx: Context, requested: str, final: str) -> GateDecision | None:
    """Backstop for redirects the route handler did not see: a final URL on another host is gated too."""
    if not final or urlparse(final).hostname == urlparse(requested).hostname:
        return None
    return ctx.gate.check(final, purpose="browser")


class BrowserReader:
    name = "browser"

    def __init__(self, spec_for: SpecFor | None = None) -> None:
        self._spec_for = spec_for or default_spec_for

    def applies(self, ref: PostRef, ctx: Context) -> bool:
        return bool(ctx.profile.allow_browser) and playwright_available()

    def read(self, ref: PostRef, ctx: Context) -> ReaderOutput:
        from .page import build_records, collected_meta, parse_html

        if not ctx.profile.allow_browser:
            return ReaderOutput(self.name, WallVerdict(False, WallType.POLICY, f"profile {ctx.profile.name} forbids the browser"))
        if not playwright_available():
            return ReaderOutput(self.name, WallVerdict(False, WallType.UNSUPPORTED, "playwright is not installed"))
        spec = self._spec_for(ref)
        url = target_url(ref, spec)
        decision = ctx.gate.check(url, purpose="browser")
        if not decision.allowed:
            return refused(self.name, decision)
        t0 = time.monotonic()
        second_look: dict[str, Any] | None = None
        with BrowserSession(ctx, reader=self.name, block_media=True) as s:
            s.open(url)
            html, title, text, final_url = s.snapshot()
            if s.nav_error is None and needs_second_look(final_url or url, text):
                t1 = time.monotonic()
                before = len(text.strip())
                s.wait_for_content()
                html, title, text, final_url = s.snapshot()
                second_look = {"waited_ms": int((time.monotonic() - t1) * 1000), "chars_before": before,
                               "chars_after": len(text.strip())}
            png = s.screenshot()
            fetch = s.fetch_result(html, final_url)
            ua, subrequests, blocked, gates = s.user_agent, s.subrequests, s.blocked, list(s.gate_decisions)
        shot = ctx.store.put(png, content_type="image/png", source_url=final_url) if png else None
        inner = ctx.store.put(text.encode("utf-8"), content_type="text/plain; charset=utf-8",
                              source_url=f"innerText:{final_url}") if text else None
        sha = store_fetch(ctx, fetch) or fetch.sha256
        verdict, judge_name = judge(fetch, spec)
        backstop = final_url_gate(ctx, url, final_url)
        if backstop is not None:
            gates.append(backstop)
            if not backstop.allowed:
                verdict = WallVerdict(False, WallType.POLICY, f"browser landed on a refused URL ({backstop.reason}) {final_url}")
        meta: dict[str, Any] = {
            "judge": judge_name, "final_url": final_url, "title": title, "user_agent": ua, "screenshot": shot,
            "subrequests": subrequests, "blocked": blocked, "page_url": final_url or url, "snapshot": sha,
            "inner_text": inner, "inner_text_chars": len(text), "snapshots": [x for x in (shot, inner) if x],
            "second_look": second_look,
        }
        ctx.log.emit("browser_read", url=url, final_url=final_url, status=fetch.status, user_agent=ua,
                     subrequests=subrequests, blocked=blocked, screenshot=shot, snapshot=sha, second_look=second_look,
                     elapsed_ms=int((time.monotonic() - t0) * 1000), served=verdict.served,
                     wall=verdict.wall.value if verdict.wall else None)
        out = ReaderOutput(self.name, verdict, fetches=[fetch], gate=[decision, *gates], meta=meta)
        if not verdict.served:
            return out  # rule 6: the wall's text and screenshot are evidence, never Records
        col = parse_html(html, platform=ref.platform, url=final_url or url, post_id=ref.post_id, prefix="browser",
                         include_visible=False)
        col.caption("title", title, "browser:title")
        records = build_records(col, ref, source_sha256=sha, fetched_at=fetch.fetched_at)
        if not col.found_post:  # the fallback: the rendered text, named as its own snapshot
            fallback = type(col)(base_url=col.base_url)
            fallback.page_text(text, "browser:innerText")
            records += build_records(fallback, ref, source_sha256=inner or sha, fetched_at=fetch.fetched_at)
        out.records = records
        out.meta.update(collected_meta(col))
        if not out.records:
            out.verdict = WallVerdict(False, WallType.EMPTY, f"rendered but carried no text ({verdict.signal})", verdict.visible_chars)
            for k in ("media_urls", "image_urls", "image_kinds"):
                out.meta.pop(k, None)
        return out
