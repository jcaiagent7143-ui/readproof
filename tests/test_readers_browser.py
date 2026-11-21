"""The browser and embed readers against a local server: identity, empty cookie jar per read, logging, the
screenshot audit trail, walls, and the embed player's media captured once in full through the same context.

The readers refuse private addresses (SSRF hygiene); these tests open that guard for 127.0.0.1 only.
"""

from __future__ import annotations

import http.server
import os
import re
import subprocess
import threading

import pytest

from readproof.readers import browser as browser_mod
from readproof.readers.browser import BrowserReader, playwright_available
from readproof.readers.embed import EmbedReader
from readproof.testing import AllowGate, FakeNet, make_context
from readproof.types import Channel, PostRef, Profile, WallType

from .reading_fakes import UA, FakeSpec, have

P1 = Profile(name="P1", obey_robots=False, obey_content_signal=False, user_initiated=True, allow_browser=True,
             allow_embed_media=True, user_agent=UA)


def _chromium_ok() -> bool:
    if not playwright_available():
        return False
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser_mod.launch_chromium(pw).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _chromium_ok(), reason="no launchable Chromium for Playwright")

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da6300010000"
                    "050001a5f645400000000049454e44ae426082")


class _Server(http.server.ThreadingHTTPServer):
    seen: list[dict]
    clip: bytes


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):  # noqa: N802
        self.server.seen.append({"path": self.path, "ua": self.headers.get("User-Agent", ""),
                                 "cookie": self.headers.get("Cookie"), "range": self.headers.get("Range"),
                                 "referer": self.headers.get("Referer")})
        route = self.path.split("?")[0]
        if route in ("/dy_player", "/dy_player_nodetail"):
            # Douyin's player page carries ByteDance's acrawler SDK (a generic challenge marker) and little text.
            fetch = "fetch('/aweme/v1/web/aweme/detail/?aweme_id=1').then(r => r.json());" if route == "/dy_player" else ""
            body = (f"<html><head><title>player</title><script>window.byted_acrawler = {{init(){{}}}};</script></head>"
                    f"<body><video src='/clip.mp4' preload='auto' muted></video><script>{fetch}</script></body></html>").encode()
            return self._send(200, "text/html", body)
        if route == "/aweme/v1/web/aweme/detail/":
            body = '{"aweme_detail": {"desc": "一起看功能教程 #科普", "author": {"nickname": "科技汇"}, "video": {"duration": 3000}}, "status_code": 0}'
            return self._send(200, "application/json", body.encode())
        if self.path == "/post":
            body = ("<html><head><title>本地帖子 Local post</title><meta property='og:title' content='本地帖子'></head>"
                    "<body><h1>本地帖子</h1>"
                    "<p>这是一段在浏览器里渲染出来的正文，暗号是七三九。</p><img src='/img.png'>"
                    "<script>document.body.insertAdjacentHTML('beforeend','<p>rendered by script</p>')</script>"
                    "</body></html>").encode()
            return self._send(200, "text/html; charset=utf-8", body, {"Set-Cookie": "sid=abc; Path=/"})
        if self.path == "/article_page":
            body = ("<html><head><title>文章 - 今日头条</title></head><body><nav>首页 推荐 热点</nav><article>"
                    + "".join(f"<p>第{i}段正文，讲清楚了一件事情的来龙去脉，也给出了数据和出处。</p>" for i in range(8))
                    + "</article><aside>热榜：另一条新闻的标题</aside></body></html>").encode()
            return self._send(200, "text/html; charset=utf-8", body)
        if self.path == "/surrogate":
            # A page script that cuts an emoji in half (live 2026-11-21 on a Toutiao article): a lone surrogate in the DOM.
            body = ("<html><head><title>半个表情</title><meta property='og:title' content='半个表情'></head><body>"
                    "<p>正文前半段。</p><script>document.body.insertAdjacentHTML('beforeend', '<p>表情' + "
                    "String.fromCharCode(0xD83D) + '后半段正文。</p>')</script></body></html>").encode()
            return self._send(200, "text/html; charset=utf-8", body)
        if self.path == "/spa":
            # A single-page app: an empty shell after DOMContentLoaded, the post rendered 1.5 s later by its script.
            body = ("<html><head><title>微博</title></head><body><div id='app'></div><script>setTimeout(function () {"
                    "document.getElementById('app').innerHTML = '<p>' + '这是单页应用稍后渲染出来的正文。'.repeat(16) + '</p>';"
                    "}, 1500)</script></body></html>").encode()
            return self._send(200, "text/html; charset=utf-8", body)
        if self.path == "/wall":
            body = "<html><head><title>安全验证</title></head><body><p>请完成安全验证</p></body></html>".encode()
            return self._send(200, "text/html; charset=utf-8", body)
        if self.path in ("/img.png", "/poster.png"):
            return self._send(200, "image/png", PNG)
        if self.path == "/player":
            body = (b"<html><head><title>player</title></head><body>"
                    b"<video id='v' src='/clip.mp4' poster='/poster.png' preload='none' muted playsinline></video>"
                    b"</body></html>")
            return self._send(200, "text/html", body)
        if self.path == "/clip.mp4":
            data = self.server.clip
            m = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range") or "")
            if m:
                start = int(m.group(1))
                end = min(int(m.group(2)) if m.group(2) else len(data) - 1, start + 65535, len(data) - 1)
                return self._send(206, "video/mp4", data[start:end + 1],
                                  {"Content-Range": f"bytes {start}-{end}/{len(data)}", "Accept-Ranges": "bytes"})
            return self._send(200, "video/mp4", data, {"Accept-Ranges": "bytes"})
        self._send(404, "text/plain", b"not found")

    def _send(self, status, ctype, body, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    if not have("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    clip = str(tmp_path_factory.mktemp("clip") / "clip.mp4")
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=320x180:d=3:r=25", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-movflags", "+faststart",
                    clip], check=True, capture_output=True)
    srv = _Server(("127.0.0.1", 0), _Handler)
    srv.seen = []
    with open(clip, "rb") as fh:
        srv.clip = fh.read()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture(autouse=True)
def allow_loopback(monkeypatch):
    real = browser_mod.private_host
    monkeypatch.setattr(browser_mod, "private_host", lambda h: False if h == "127.0.0.1" else real(h))


def _ctx(tmp_path):
    return make_context(P1, FakeNet({}, origin="test:local"), gate=AllowGate(P1), workdir=str(tmp_path / "w"))


def test_browser_reader_fresh_context_declared_identity_and_audit_trail(server, tmp_path):
    base = f"http://127.0.0.1:{server.server_address[1]}"
    ref = PostRef("web", "local", "desktop", f"{base}/post")
    spec = FakeSpec("web")
    ctx = _ctx(tmp_path)
    out = BrowserReader(spec_for=lambda r: spec).read(ref, ctx)
    assert out.verdict.served, out.verdict
    ua = out.meta["user_agent"]
    assert "HeadlessChrome" in ua and ua.endswith(" " + UA)
    first = [s for s in server.seen if s["path"] == "/post"]
    assert first[-1]["ua"] == ua and first[-1]["cookie"] is None
    texts = {r.channel: r.text for r in out.records}
    assert "暗号是七三九" in " ".join(r.text for r in out.records if r.channel is Channel.PAGE)
    assert "rendered by script" in " ".join(r.text for r in out.records)  # the rendered DOM, not the raw HTML
    assert Channel.CAPTION in texts
    shot = out.meta["screenshot"]
    assert shot and ctx.store.get(shot)[:8] == b"\x89PNG\r\n\x1a\n"
    assert out.fetches[0].status == 200 and out.fetches[0].user_agent == ua and out.fetches[0].origin == "test:local"
    subs = ctx.log.of("subrequest")
    assert any(e["url"].endswith("/img.png") and e["resource_type"] == "image" and e["status"] == 200 for e in subs)
    assert ctx.gate.asked[0] == f"{base}/post"
    # a second read is a new context: the cookie the first page set is gone
    BrowserReader(spec_for=lambda r: spec).read(ref, _ctx(tmp_path))
    assert [s["cookie"] for s in server.seen if s["path"] == "/post"][-1] is None


def test_browser_reader_wall_keeps_screenshot_but_no_records(server, tmp_path):
    base = f"http://127.0.0.1:{server.server_address[1]}"
    ctx = _ctx(tmp_path)
    out = BrowserReader(spec_for=lambda r: FakeSpec("web")).read(PostRef("web", "w", "desktop", f"{base}/wall"), ctx)
    assert not out.verdict.served and out.verdict.wall is WallType.CHALLENGE and out.records == []
    assert ctx.store.get(out.meta["screenshot"]) is not None and ctx.store.records == []


def test_browser_reader_respects_profile_and_gate(server, tmp_path):
    base = f"http://127.0.0.1:{server.server_address[1]}"
    no_browser = Profile(name="P2", obey_robots=False, obey_content_signal=False, user_initiated=True,
                         allow_browser=False, allow_embed_media=False, user_agent=UA)
    ctx = make_context(no_browser, FakeNet({}), workdir=str(tmp_path / "w"))
    reader = BrowserReader(spec_for=lambda r: FakeSpec("web"))
    ref = PostRef("web", "x", "desktop", f"{base}/post")
    assert not reader.applies(ref, ctx) and reader.read(ref, ctx).verdict.wall is WallType.POLICY
    ctx = make_context(P1, FakeNet({}), gate=AllowGate(P1, deny=lambda u: "robots_disallow"), workdir=str(tmp_path / "w"))
    n = len(server.seen)
    assert reader.read(ref, ctx).verdict.wall is WallType.POLICY and len(server.seen) == n  # nothing was requested


def test_embed_reader_takes_the_players_media_once_in_full(server, tmp_path):
    base = f"http://127.0.0.1:{server.server_address[1]}"
    embed = f"{base}/player"
    spec = FakeSpec("web", embed=embed)
    ctx = _ctx(tmp_path)
    reader = EmbedReader(spec_for=lambda r: spec)
    ref = PostRef("web", "clip", "desktop", f"{base}/post")
    assert reader.applies(ref, ctx)
    out = reader.read(ref, ctx)
    assert out.verdict.served, out.verdict
    assert len(out.media) == 1 and os.path.exists(out.media[0])
    m = out.meta["media"][0]
    assert m["has_audio"] and m["has_video"] and abs(m["duration_s"] - 3.0) < 0.3 and not m["partial"]
    assert ctx.store.get(m["sha256"]) == server.clip and m["bytes"] == len(server.clip)
    full = [s for s in server.seen if s["path"] == "/clip.mp4" and s["range"] is None]
    assert len(full) == 1  # the player's ranges, then ONE full request of ours
    assert full[0]["referer"] == embed and full[0]["ua"] == out.meta["user_agent"] and full[0]["cookie"] is None
    assert any(s["range"] for s in server.seen if s["path"] == "/clip.mp4")  # the player itself fetched ranges
    assert out.meta["image_urls"] == [f"{base}/poster.png"] and out.meta["image_kinds"] == ["cover"]
    assert out.meta["duration_s"] == m["duration_s"]
    assert ctx.gate.asked[:1] == [embed] and f"{base}/clip.mp4" in ctx.gate.asked


def test_embed_player_answer_overrides_a_generic_marker_only_with_its_own_detail(server, tmp_path):
    base = f"http://127.0.0.1:{server.server_address[1]}"
    ref = PostRef("douyin", "1", "desktop", f"{base}/post")
    served = EmbedReader(spec_for=lambda r: FakeSpec("douyin", embed=f"{base}/dy_player")).read(ref, _ctx(tmp_path))
    assert served.verdict.served and "overridden" in served.verdict.signal and "generic:challenge" in served.verdict.signal
    assert {(r.locator["field"], r.text, r.extractor) for r in served.records} == {
        ("caption", "一起看功能教程 #科普", "embed:douyin_detail"), ("author", "科技汇", "embed:douyin_detail")}
    assert len(served.media) == 1 and os.path.exists(served.media[0])
    ctx = _ctx(tmp_path / "second")
    walled = EmbedReader(spec_for=lambda r: FakeSpec("douyin", embed=f"{base}/dy_player_nodetail")).read(ref, ctx)
    assert not walled.verdict.served and walled.verdict.wall is WallType.CHALLENGE
    assert walled.records == [] and walled.media == []  # media without the post's detail does not lift a challenge
    assert not [e for e in ctx.log.of("subrequest") if e["resource_type"] == "media-refetch"]  # nor is it downloaded
    assert walled.meta["media_hits"] >= 1


def test_browser_reader_keeps_the_article_not_the_chrome(server, tmp_path):
    base = f"http://127.0.0.1:{server.server_address[1]}"
    ctx = _ctx(tmp_path)
    out = BrowserReader(spec_for=lambda r: FakeSpec("toutiao")).read(
        PostRef("toutiao", "a1", "desktop", f"{base}/article_page"), ctx)
    assert out.verdict.served
    pages = [r for r in out.records if r.channel is Channel.PAGE]
    assert pages and all(r.extractor == "browser:article" for r in pages)
    assert "第4段正文" in " ".join(r.text for r in pages) and not any("热榜" in r.text for r in out.records)
    inner = ctx.store.get(out.meta["inner_text"]).decode("utf-8")
    assert "热榜" in inner and out.meta["inner_text"] in out.meta["snapshots"]  # kept as evidence, not as a Record


def test_browser_reader_survives_a_lone_surrogate_in_the_dom(server, tmp_path):
    base = f"http://127.0.0.1:{server.server_address[1]}"
    ctx = _ctx(tmp_path)
    out = BrowserReader(spec_for=lambda r: FakeSpec("web")).read(PostRef("web", "s", "desktop", f"{base}/surrogate"), ctx)
    assert out.verdict.served, out.verdict
    text = " ".join(r.text for r in out.records)
    assert "后半段正文" in text and "\ufffd" in text
    ctx.store.get(out.meta["inner_text"]).decode("utf-8")  # the stored innerText is valid UTF-8
    assert not ctx.log.of("reader_error")


def test_browser_reader_gives_a_single_page_app_a_second_look(server, tmp_path):
    base = f"http://127.0.0.1:{server.server_address[1]}"
    ctx = _ctx(tmp_path)
    out = BrowserReader(spec_for=lambda r: FakeSpec("web")).read(PostRef("web", "spa", "desktop", f"{base}/spa"), ctx)
    look = out.meta["second_look"]
    assert look is not None and look["chars_before"] < 40 and look["chars_after"] >= 100, look
    assert out.verdict.served and "稍后渲染出来的正文" in " ".join(r.text for r in out.records)
