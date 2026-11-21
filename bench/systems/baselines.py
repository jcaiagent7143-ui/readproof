"""Baseline systems: what agents commonly do with a pasted link today.

closed_book     the model gets the question and the URL string only (no fetch).
naive_fetch     one httpx GET, follow_redirects=True, httpx's own default User-Agent (it names the library: honest),
                visible text via readproof.walls.visible_text.
reader_service  Jina Reader (GET https://r.jina.ai/<url>, no key, at most 15 requests a minute); its markdown.
browser_text    Playwright headless Chromium with its default user agent, a fresh context per URL,
                goto (25 s timeout) + 3 s settle, document.body.innerText.

None of them verifies anything: the model answers from whatever text came back, or says NOT_FOUND. None carries
cookies across reads, uses a proxy, a logged-in browser, or answers a challenge. The observer wall label is
readproof.walls.judge on what each fetched (for reader_service, on its markdown wrapped as a page; approximate).
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

import httpx

from readproof import walls
from readproof.types import FetchResult, sha256_bytes

from .base import (BASELINE_SYSTEM, TEXT_CAP, SysAnswer, SysRead, ask_closed_book, ask_text, first_url, now_iso)
from .pacing import BROWSER_HOLD_S, DomainScheduler, PacedTransport

NAIVE_MAX_BYTES = 10_000_000
NAIVE_TIMEOUT_S = 20.0
JINA_BASE = "https://r.jina.ai/"
JINA_TIMEOUT_S = 60.0
BROWSER_NAV_TIMEOUT_S = 25.0
BROWSER_SETTLE_S = 3.0
_JINA_WARN = re.compile(r"Target URL returned error (\d{3})", re.I)
_JINA_TITLE = re.compile(r"^Title:\s*(.*)$", re.M)
_JINA_SOURCE = re.compile(r"^URL Source:\s*(\S+)", re.M)

# At most this many Chromium instances at once across all worker threads (the harness's browser readers included,
# see bench/trackA_run.py), so a run does not thrash the machine it runs on.
CHROMIUM_SLOTS = threading.BoundedSemaphore(3)


def _guarded_transport() -> httpx.BaseTransport:
    """httpx's transport plus readproof's connect-time public-address guard (SSRF hygiene); nothing else changes."""
    from readproof import policy
    from readproof.net import GuardedTransport

    return GuardedTransport(policy.default_resolve)


def _decode(body: bytes, headers: dict[str, str]) -> str:
    from readproof.readers.base import decode_body

    fake = FetchResult(url="", final_url="", status=200, headers=headers, bytes=len(body), sha256="",
                       fetched_at="", elapsed_ms=0, origin="", user_agent="", body=body)
    return decode_body(fake)


def _observer(url: str, final_url: str, status: int | None, headers: dict[str, str], body: bytes, *,
              user_agent: str, elapsed_ms: int, origin: str, error: str | None = None,
              redirects: list[str] | None = None) -> tuple[str | None, str]:
    """readproof.walls.judge on what a baseline fetched: (wall value or None, signal)."""
    fetch = FetchResult(url=url, final_url=final_url or url, status=status, headers=headers, bytes=len(body),
                        sha256=sha256_bytes(body), fetched_at=now_iso(), elapsed_ms=elapsed_ms, origin=origin,
                        user_agent=user_agent, method="GET", redirects=list(redirects or []), error=error, body=body)
    try:
        v = walls.judge(fetch)
    except Exception as exc:  # an observer failure must not lose the read
        return "unknown", f"observer error {type(exc).__name__}: {exc}"[:300]
    return (None if v.served else (v.wall.value if v.wall else "unknown")), v.signal[:400]


def _no_url(system: str, text: str) -> SysRead:
    return SysRead(system=system, url=text, status=None, final_url=None, bytes=0, visible_chars=0, text="",
                   wall="unsupported", seconds=0.0, error="no_url_in_input", meta={"observer": "no URL in the input"})


class _TextAnswerer:
    """answer(): the model on the obtained text, no verification."""

    name = "baseline"

    def __init__(self, llm: Any) -> None:
        self.llm = llm

    def answer(self, question: str, url: str, read: SysRead | None = None) -> SysAnswer:
        t0 = time.monotonic()
        read = read if read is not None else self.read(url)  # type: ignore[attr-defined]
        if not read.text:
            return SysAnswer(self.name, question, url, None, True, "no_text", None, time.monotonic() - t0, 0,
                             trace={"read_error": read.error})
        reply, abstained, how, calls, err = ask_text(self.llm, BASELINE_SYSTEM, question, read.text,
                                                     purpose=f"{self.name}:answer")
        return SysAnswer(self.name, question, url, reply, abstained, how, None, time.monotonic() - t0, calls,
                         trace={"reply": reply, "text_chars": len(read.text)}, error=err)


class ClosedBook:
    name = "closed_book"

    def __init__(self, llm: Any) -> None:
        self.llm = llm

    def read(self, text: str) -> SysRead:
        return SysRead(self.name, text, None, None, 0, 0, "", None, 0.0, meta={"note": "closed book: no fetch"})

    def answer(self, question: str, url: str, read: SysRead | None = None) -> SysAnswer:
        t0 = time.monotonic()
        reply, abstained, how, calls, err = ask_closed_book(self.llm, question, url, purpose="closed_book:answer")
        return SysAnswer(self.name, question, url, reply, abstained, how, None, time.monotonic() - t0, calls,
                         trace={"reply": reply}, error=err)


class NaiveFetch(_TextAnswerer):
    name = "naive_fetch"

    def __init__(self, llm: Any, scheduler: DomainScheduler, *, origin: str = "residential:unknown") -> None:
        super().__init__(llm)
        self.scheduler = scheduler
        self.origin = origin

    def read(self, text: str) -> SysRead:
        url = first_url(text)
        if not url:
            return _no_url(self.name, text)
        t0 = time.monotonic()
        body = b""
        status: int | None = None
        headers: dict[str, str] = {}
        final = url
        history: list[str] = []
        error: str | None = None
        ua = ""
        # A fresh client per read: cookies a redirect chain sets live only inside this one GET, as with
        # httpx.get(url, follow_redirects=True). trust_env=False: no environment proxy is ever used.
        client = httpx.Client(follow_redirects=True, timeout=NAIVE_TIMEOUT_S, trust_env=False,
                              transport=PacedTransport(_guarded_transport(), self.scheduler))
        try:
            ua = client.headers.get("user-agent", "")
            with client.stream("GET", url) as resp:
                status = resp.status_code
                headers = dict(resp.headers)
                final = str(resp.url)
                history = [str(r.url) for r in resp.history]
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf += chunk
                    if len(buf) > NAIVE_MAX_BYTES:
                        del buf[NAIVE_MAX_BYTES:]
                        break
                body = bytes(buf)
        except httpx.HTTPError as exc:
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
        except Exception as exc:  # address guard refusals and the like
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
        finally:
            client.close()
        elapsed = time.monotonic() - t0
        html = _decode(body, headers) if body else ""
        title, vis = walls.visible_text(html) if html else ("", "")
        shown = (title + "\n" + vis).strip() if title else vis
        wall, signal = _observer(url, final, status, headers, body, user_agent=ua, elapsed_ms=int(elapsed * 1000),
                                 origin=self.origin, error=error if status is None else None, redirects=history)
        if status is None and error:
            wall = "network"
        return SysRead(self.name, text, status, final, len(body), len(vis), shown[:TEXT_CAP], wall, elapsed, error,
                       meta={"effective_url": url, "redirects": history, "user_agent": ua, "observer": signal,
                             "title": title[:300], "content_type": headers.get("content-type", "")})


class ReaderService(_TextAnswerer):
    name = "reader_service"

    def __init__(self, llm: Any, scheduler: DomainScheduler, *, origin: str = "residential:unknown") -> None:
        super().__init__(llm)
        self.scheduler = scheduler  # r.jina.ai has its own gap (4 s) in the scheduler; not a platform request
        self.origin = origin

    def read(self, text: str) -> SysRead:
        url = first_url(text)
        if not url:
            return _no_url(self.name, text)
        t0 = time.monotonic()
        jina_status: int | None = None
        body_text = ""
        error: str | None = None
        retried = False
        client = httpx.Client(follow_redirects=True, timeout=JINA_TIMEOUT_S, trust_env=False,
                              transport=PacedTransport(_guarded_transport(), self.scheduler, count=False))
        try:
            for attempt in (1, 2):
                try:
                    r = client.get(JINA_BASE + url)
                except httpx.HTTPError as exc:
                    error = f"{type(exc).__name__}: {str(exc)[:200]}"
                    break
                jina_status = r.status_code
                body_text = r.text
                error = None
                if r.status_code == 429 and attempt == 1:  # the service's own rate limit: one retry after its wait
                    retried = True
                    wait = r.headers.get("retry-after", "")
                    time.sleep(min(90.0, float(wait)) if wait.replace(".", "").isdigit() else 60.0)
                    continue
                break
        finally:
            client.close()
        elapsed = time.monotonic() - t0
        m = _JINA_WARN.search(body_text[:3000])
        target_status = int(m.group(1)) if m else None
        title_m = _JINA_TITLE.search(body_text[:2000])
        source_m = _JINA_SOURCE.search(body_text[:3000])
        title = title_m.group(1).strip() if title_m else ""
        source = source_m.group(1).strip() if source_m else url
        # Observer label: the markdown wrapped as a page, with the target's status when the service reported one.
        if jina_status is None:
            wall, signal = "network", f"reader_service transport error {error}"
        elif jina_status != 200:
            wall, signal = _observer(url, source, jina_status, {"content-type": "text/html"},
                                     _as_page(title, body_text), user_agent="r.jina.ai", elapsed_ms=int(elapsed * 1000),
                                     origin="service:r.jina.ai")
            signal = f"reader_service http {jina_status}; {signal}"
        else:
            wall, signal = _observer(url, source, target_status or 200, {"content-type": "text/html"},
                                     _as_page(title, body_text), user_agent="r.jina.ai", elapsed_ms=int(elapsed * 1000),
                                     origin="service:r.jina.ai")
        return SysRead(self.name, text, jina_status, source, len(body_text.encode("utf-8")), len(body_text),
                       body_text[:TEXT_CAP], wall, elapsed, error,
                       meta={"effective_url": url, "target_status": target_status, "title": title[:300],
                             "observer": signal, "retried_429": retried})


def _clean_surrogates(text: str) -> str:
    return (text or "").encode("utf-8", errors="replace").decode("utf-8")


def _as_page(title: str, markdown: str) -> bytes:
    import html as html_lib

    return (f"<html><head><title>{html_lib.escape(title)}</title></head><body><pre>{html_lib.escape(markdown)}</pre>"
            "</body></html>").encode("utf-8")


class BrowserText(_TextAnswerer):
    name = "browser_text"

    def __init__(self, llm: Any, scheduler: DomainScheduler, *, origin: str = "residential:unknown") -> None:
        super().__init__(llm)
        self.scheduler = scheduler
        self.origin = origin

    def read(self, text: str) -> SysRead:
        url = first_url(text)
        if not url:
            return _no_url(self.name, text)
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright

        from readproof.readers.browser import launch_chromium

        t0 = time.monotonic()
        html, title, inner, final, ua = "", "", "", url, ""
        status: int | None = None
        headers: dict[str, str] = {}
        error: str | None = None
        nav_error: str | None = None
        navs: list[str] = []
        with CHROMIUM_SLOTS:
            with sync_playwright() as pw:
                browser = launch_chromium(pw)
                try:
                    context = browser.new_context()  # default user agent, empty storage: a fresh context per URL
                    page = context.new_page()
                    ua = page.evaluate("navigator.userAgent")

                    def on_request(req: Any) -> None:
                        try:
                            if req.is_navigation_request() and req.frame == page.main_frame:
                                navs.append(req.url)
                        except Exception:
                            pass

                    main_docs: list[Any] = []

                    def on_response(r: Any) -> None:
                        try:
                            if r.request.is_navigation_request() and r.frame == page.main_frame:
                                main_docs.append(r)
                        except Exception:
                            pass

                    page.on("request", on_request)
                    page.on("response", on_response)
                    self.scheduler.reserve(url, hold_s=BROWSER_HOLD_S)
                    resp = None
                    try:
                        resp = page.goto(url, timeout=BROWSER_NAV_TIMEOUT_S * 1000)
                    except PlaywrightTimeout:
                        nav_error = f"navigation timeout {BROWSER_NAV_TIMEOUT_S:.0f}s"
                    except PlaywrightError as exc:
                        nav_error = f"navigation error: {str(exc).splitlines()[0][:200]}"
                    try:
                        page.wait_for_timeout(BROWSER_SETTLE_S * 1000)
                    except Exception:
                        pass
                    if resp is None and main_docs:  # goto timed out after the document itself had arrived
                        resp = main_docs[-1]
                    if resp is not None:
                        try:
                            status = resp.status
                            headers = dict(resp.headers)
                        except Exception:
                            pass
                    for attempt in range(2):
                        try:
                            html = page.content()
                            title = page.title()
                            inner = page.evaluate("() => (document.body && document.body.innerText) || ''") or ""
                            final = page.url
                            break
                        except Exception as exc:
                            error = f"snapshot error: {str(exc)[:200]}"
                            try:
                                page.wait_for_timeout(500)
                            except Exception:
                                pass
                    context.close()
                finally:
                    browser.close()
        # Redirect hops and script navigations inside this one top-level load: counted, not paced one by one.
        if len(navs) > 1:
            self.scheduler.count(url, len(navs) - 1)
        elapsed = time.monotonic() - t0
        # A rendered DOM can hold a lone UTF-16 surrogate (half an emoji cut by the page's own script); encoding it
        # strictly raises, so it is replaced (live 2026-11-21, a Toutiao article).
        html = _clean_surrogates(html)
        title = _clean_surrogates(title)
        inner = _clean_surrogates(inner)
        body = html.encode("utf-8")
        headers["content-type"] = "text/html; charset=utf-8"
        fetch_error = nav_error if (resp_missing := status is None) and not html else None
        if final.startswith("chrome-error://"):
            fetch_error = "chromium error page"
        wall, signal = _observer(url, final, status, headers, body, user_agent=ua, elapsed_ms=int(elapsed * 1000),
                                 origin=self.origin, error=fetch_error, redirects=navs[1:])
        if resp_missing and not html:
            wall = "network"
        inner = re.sub(r"[ \t\r\f\v]+", " ", inner)
        inner = re.sub(r"\n\s*\n+", "\n", inner).strip()
        shown = (title + "\n" + inner).strip() if title else inner
        return SysRead(self.name, text, status, final, len(body), len(inner), shown[:TEXT_CAP], wall, elapsed,
                       error or nav_error,
                       meta={"effective_url": url, "user_agent": ua, "observer": signal, "title": title[:300],
                             "navigations": navs[:10]})
