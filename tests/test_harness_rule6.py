"""Rule 6 through the whole read path: a wall never becomes a Record, whatever a reader returns.

Also pinned here: the reader chain (first served reader wins), the most specific wall is reported, a reader that
crashes is a verdict and not a crash, and page-listed media is fetched with ONE GET through ctx.net, then read
offline end to end (speech, on-screen text, cover text) from generated media.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

import pytest

from readproof.harness import Harness, read_in
from readproof.interfaces import ReaderOutput
from readproof.testing import FakeNet, MemoryLog, MemoryStore, NullBudget, Scripted, make_context
from readproof.types import Channel, PostRef, Record, WallType, WallVerdict

from .reading_fakes import (CJK_FONT, P1_NO_BROWSER, FakeResolution, FakeSpec, have, make_talking_video,
                            render_text_png, resolver_for, wall_when)

URL = "https://www.xiaohongshu.com/explore/66aa00000000000000000001"
WALL_TEXT = "请完成安全验证后继续访问，拖动滑块完成拼图"
WALL_PAGE = f"<html><head><title>安全验证</title></head><body><div>{WALL_TEXT}</div><p>{'说明文字' * 30}</p></body></html>"
SERVED_PAGE = ("<html><head><title>上海咖啡探店</title><meta property='og:title' content='上海咖啡探店'>"
               "<meta property='og:description' content='三家店的对比'></head>"
               "<body><p>" + "这是正文内容，足够长。" * 6 + "</p></body></html>")


def harness_with(routes: dict, spec: FakeSpec, url: str = URL, **kw) -> tuple[Harness, FakeNet]:
    net = FakeNet(routes)
    h = Harness(P1_NO_BROWSER, root=kw.pop("root", "/tmp/readproof-test-runs"), net=net, gate=kw.pop("gate", None) or _allow(),
                log=MemoryLog(), store=MemoryStore(), budget=NullBudget(), resolver=resolver_for(spec, url),
                options={"min_host_interval_s": 0, **kw.pop("options", {})}, **kw)
    return h, net


def _allow():
    from readproof.testing import AllowGate

    return AllowGate(P1_NO_BROWSER)


def test_scripted_wall_page_never_becomes_a_record(tmp_path):
    spec = FakeSpec("xiaohongshu", post_id="66aa00000000000000000001", judge=wall_when("拖动滑块"))
    h, net = harness_with({URL: Scripted(body=WALL_PAGE)}, spec, root=str(tmp_path))
    res = h.read(f"【探店】 {URL} 复制本条信息")
    assert res.served is False and res.wall is WallType.CHALLENGE
    assert res.records == [] and h.store.records == []
    assert "page:" in res.wall_signal and "拖动滑块" in res.wall_signal
    assert res.reader is None and res.post is not None and res.post.key == "xiaohongshu:66aa00000000000000000001"
    # the wall's body is evidence: stored by hash, listed as a snapshot, never as text in a Record
    assert res.snapshots and h.store.get(res.snapshots[0]) is not None
    assert not any(WALL_TEXT in r.text for r in h.store.records)
    assert net.requests == [("GET", URL)]  # the browser is not allowed under this profile: no other reader ran
    ends = h.log.of("read_end")
    assert ends and ends[-1]["served"] is False and ends[-1]["records"] == 0


def test_most_specific_wall_wins_and_every_reader_is_named(tmp_path):
    oembed = "https://www.youtube.com/oembed?url=x&format=json"
    page = "https://www.youtube.com/watch?v=abc"
    spec = FakeSpec("youtube", post_id="abc", oembed=oembed)
    h, net = harness_with({oembed: Scripted(status=401, body="Unauthorized"), page: Scripted(status=404, body="gone")},
                          spec, url=page, root=str(tmp_path))
    res = h.read(page)
    assert not res.served and res.wall is WallType.DELETED and res.records == []
    assert res.wall_signal.startswith("page:") and "oembed=unknown" in res.wall_signal
    # YouTube reads the watch page first; a generic 404 is not a platform rule, so oEmbed is still asked
    assert [u for _, u in net.requests] == [page, oembed]


class _LyingReader:
    """A buggy reader: not served, yet it hands back a Record made from the wall."""

    name = "page"

    def applies(self, ref, ctx):
        return True

    def read(self, ref: PostRef, ctx) -> ReaderOutput:
        r = Record(record_id="r_lie", post_key=ref.key, channel=Channel.PAGE, locator={"offset": 0}, text=WALL_TEXT,
                   source_sha256="0" * 64, extractor="lie", fetched_at="t")
        return ReaderOutput("page", WallVerdict(False, WallType.CHALLENGE, "lying reader"), records=[r])


class _CrashingReader:
    name = "oembed"

    def applies(self, ref, ctx):
        return True

    def read(self, ref, ctx):
        raise RuntimeError("boom")


def test_harness_drops_records_of_unserved_readers_and_survives_crashes(tmp_path):
    spec = FakeSpec("youtube", post_id="abc")
    readers = {"oembed": _CrashingReader(), "page": _LyingReader()}
    ctx = make_context(P1_NO_BROWSER, FakeNet({}), workdir=str(tmp_path / "w"), min_host_interval_s=0)
    res = read_in(ctx, "https://www.youtube.com/watch?v=abc", resolver=resolver_for(spec, "https://www.youtube.com/watch?v=abc"),
                  reader_factory=lambda name, spec_for: readers.get(name) or _CrashingReader())
    assert not res.served and res.wall is WallType.CHALLENGE and res.records == [] and ctx.store.records == []
    assert any(e["reader"] == "oembed" for e in ctx.log.of("reader_error"))


def test_served_page_records_are_stored_and_named(tmp_path):
    spec = FakeSpec("web", post_id="w1")
    h, _ = harness_with({URL: Scripted(body=SERVED_PAGE)}, spec, root=str(tmp_path))
    res = h.read(URL)
    assert res.served and res.reader == "page" and res.wall is None
    assert res.records and h.store.records == res.records
    assert {r.channel for r in res.records} >= {Channel.CAPTION, Channel.PAGE}
    assert all(r.source_sha256 in res.snapshots for r in res.records)


def test_resolution_refusal_sends_nothing(tmp_path):
    spec = FakeSpec("linkedin", post_id="1")

    def refuse(text, ctx):
        return FakeResolution(input=text, url="https://www.linkedin.com/feed/update/urn:li:activity:1/", ref=None,
                              spec=spec, verdict=WallVerdict(False, WallType.POLICY, "resolve:policy reason=terms"))

    net = FakeNet({})
    h = Harness(P1_NO_BROWSER, root=str(tmp_path), net=net, gate=_allow(), log=MemoryLog(), store=MemoryStore(),
                budget=NullBudget(), resolver=refuse)
    res = h.read("https://www.linkedin.com/feed/update/urn:li:activity:1/")
    assert not res.served and res.wall is WallType.POLICY and net.requests == [] and res.records == []


class _HeaderNet(FakeNet):
    def __init__(self, routes):
        super().__init__(routes)
        self.headers: list[dict] = []

    def fetch(self, url, **kw):
        self.headers.append(dict(kw.get("headers") or {}))
        return super().fetch(url, **kw)


def _xhs_video_page(media_url: str, cover_url: str) -> str:
    import json

    state = {"note": {"noteDetailMap": {"66aa00000000000000000001": {"note": {
        "type": "video", "title": "暗号视频", "desc": "看到最后", "user": {"nickname": "作者"},
        "imageList": [{"urlDefault": cover_url}],
        "video": {"media": {"stream": {"h264": [{"height": 540, "masterUrl": media_url}]}}}}}}}}
    return ("<html><head><title>暗号视频 - 小红书</title></head><body><p>" + "正文" * 30 + "</p>"
            f"<script>window.__INITIAL_STATE__={json.dumps(state, ensure_ascii=False)}</script></body></html>")


@pytest.mark.skipif(not (have("ffmpeg", "say") and CJK_FONT), reason="needs ffmpeg, macOS say and a CJK font")
def test_page_listed_media_one_get_then_speech_and_on_screen(tmp_path):
    from readproof.extract import asr

    video = str(tmp_path / "talk.mp4")
    make_talking_video(video, text_lines=["暗号 七三九", "Secret K7Q2"], speech="The secret code is seven three nine.")
    cover = str(tmp_path / "cover.png")
    render_text_png(cover, ["封面标题 Cover"])
    media_url, cover_url = "https://sns-video.xhscdn.com/v/1.mp4", "https://sns-webpic.xhscdn.com/c.png"
    spec = FakeSpec("xiaohongshu", post_id="66aa00000000000000000001")
    net = _HeaderNet({
        URL: Scripted(body=_xhs_video_page(media_url, cover_url)),
        media_url: Scripted(body=Path(video).read_bytes(), headers={"content-type": "video/mp4"}),
        cover_url: Scripted(body=Path(cover).read_bytes(), headers={"content-type": "image/png"}),
    })
    h = Harness(P1_NO_BROWSER, root=str(tmp_path / "runs"), net=net, gate=_allow(), log=MemoryLog(), store=MemoryStore(),
                budget=NullBudget(), resolver=resolver_for(spec, URL), options={"min_host_interval_s": 0})
    res = h.read(URL)
    assert res.served and res.reader == "page"
    assert [u for _, u in net.requests].count(media_url) == 1  # ONE GET
    assert net.headers[[u for _, u in net.requests].index(media_url)].get("Referer") == URL
    media_sha = next(f.sha256 for f in res.fetches if f.url == media_url)
    assert h.store.get(media_sha) is not None and media_sha in res.snapshots
    channels = res.channels()
    if asr.available():
        speech = " ".join(r.text for r in res.records if r.channel is Channel.SPEECH).lower()
        assert "secret code" in speech
        assert all(r.source_sha256 == media_sha for r in res.records if r.channel is Channel.SPEECH)
    if platform.system() == "Darwin" and os.path.exists("/usr/bin/swiftc"):
        on_screen = [r for r in res.records if r.channel is Channel.ON_SCREEN]
        assert on_screen and "K7Q2" in on_screen[0].text and on_screen[0].locator == {"t": 0.0}
        assert on_screen[0].source_sha256 in res.snapshots and h.store.get(on_screen[0].source_sha256) is not None
        assert any(r.channel is Channel.IMAGE_TEXT and "Cover" in r.text for r in res.records)
    assert Channel.CAPTION in channels
    assert not os.path.exists(os.path.join(str(tmp_path / "runs"), "work"))  or not os.listdir(str(tmp_path / "runs" / "work"))


def test_refused_media_keeps_the_read_served_without_speech(tmp_path):
    media_url = "https://sns-video.xhscdn.com/v/1.mp4"
    spec = FakeSpec("xiaohongshu", post_id="66aa00000000000000000001")
    h, net = harness_with({URL: Scripted(body=_xhs_video_page(media_url, "https://img/c.png")),
                           media_url: Scripted(status=403, body="Forbidden")}, spec, root=str(tmp_path),
                          options={"images": False})
    res = h.read(URL)
    assert res.served and Channel.SPEECH not in res.channels() and res.records
    assert [u for _, u in net.requests].count(media_url) == 1
    assert any(e.get("ok") is False and e.get("status") == 403 for e in h.log.of("media_fetch"))


def test_real_resolver_and_spec_when_installed(tmp_path):
    """Integration with the platform builder's modules: a 200 OK 「页面不见了」 page is a wall, not text."""
    pytest.importorskip("readproof.canonical")
    from readproof import canonical

    gone = "<html><head><title>小红书</title></head><body><div>你访问的页面不见了</div>" + "<p>推荐内容</p>" * 30 + "</body></html>"
    note = "https://www.xiaohongshu.com/explore/66aa00000000000000000001?xsec_token=ABC&xsec_source=pc_share"
    net = FakeNet({note: Scripted(body=gone)})
    h = Harness(P1_NO_BROWSER, root=str(tmp_path), net=net, gate=_allow(), log=MemoryLog(), store=MemoryStore(),
                budget=NullBudget(), resolver=canonical.resolve, options={"min_host_interval_s": 0})
    res = h.read(note)
    assert not res.served and res.records == [] and h.store.records == []
    assert res.wall in (WallType.DELETED, WallType.TOKEN) and "页面不见了" in res.wall_signal


def test_a_refusal_stops_the_alternatives_and_the_host(tmp_path):
    """Rule 4: after 403 nothing is retried, and another mirror of the same stream would be a retry."""
    import json

    first, backup = "https://v16.example-cdn.com/a.mp4", "https://v19.example-cdn.com/a.mp4"
    imgs = [f"https://img.example-cdn.com/{i}.jpg" for i in range(3)]
    state = {"note": {"noteDetailMap": {"66aa00000000000000000001": {"note": {
        "type": "normal", "title": "图文", "desc": "三张图", "imageList": [{"urlDefault": u} for u in imgs]}}}}}
    page = (f"<html><head><title>图文 - 小红书</title></head><body><p>{'正文' * 30}</p>"
            f"<script>window.__INITIAL_STATE__={json.dumps(state, ensure_ascii=False)}</script></body></html>")
    spec = FakeSpec("xiaohongshu", post_id="66aa00000000000000000001")
    h, net = harness_with({URL: Scripted(body=page), imgs[0]: Scripted(status=403, body="no")}, spec, root=str(tmp_path))
    res = h.read(URL)
    asked = [u for _, u in net.requests]
    assert res.served and asked.count(imgs[0]) == 1 and imgs[1] not in asked and imgs[2] not in asked
    assert all(h.store.get(sha) is not None for sha in res.snapshots)  # every listed snapshot, refusals included

    from readproof.harness import _fetch_media, _Pacer
    from readproof.types import ReadResult

    ctx = make_context(P1_NO_BROWSER, FakeNet({first: Scripted(status=403, body="Forbidden"),
                                               backup: Scripted(body=b"\x00" * 10)}), workdir=str(tmp_path / "w"))
    r = ReadResult(url="x", post=None, served=True, wall=None, wall_signal="")
    assert _fetch_media(ctx, [first, backup], "https://page.example.com/", r, _Pacer(0)) is None
    assert [u for _, u in ctx.net.requests] == [first]
    assert ctx.log.of("media_refused")[0]["untried"] == 1


def test_cli_prints_the_wall_and_the_channels(tmp_path, monkeypatch, capsys):
    import json as _json

    import readproof.harness as harness_mod
    from readproof import cli

    spec = FakeSpec("xiaohongshu", post_id="66aa00000000000000000001", judge=wall_when("拖动滑块"))
    seen = {}

    class _Fake(Harness):
        def __init__(self, profile, *, root, origin, options):
            seen["args"] = (profile, origin, options)
            super().__init__(P1_NO_BROWSER, root=str(tmp_path), net=FakeNet({URL: Scripted(body=WALL_PAGE)}),
                             gate=_allow(), log=MemoryLog(), store=MemoryStore(), budget=NullBudget(),
                             resolver=resolver_for(spec, URL), options={**options, "min_host_interval_s": 0})

    monkeypatch.setattr(harness_mod, "Harness", _Fake)
    code = cli.main(["read", URL, "--no-speech", "--origin", "residential:MY"])
    out = capsys.readouterr().out
    assert code == 1 and "served    no, wall challenge" in out and "records   none" in out and "snapshots 1" in out
    assert seen["args"][0] == "P1" and seen["args"][1] == "residential:MY" and seen["args"][2]["speech"] is False

    spec2 = FakeSpec("web", post_id="w1")
    monkeypatch.setattr(harness_mod, "Harness", lambda profile, **kw: Harness(
        P1_NO_BROWSER, root=str(tmp_path), net=FakeNet({URL: Scripted(body=SERVED_PAGE)}), gate=_allow(),
        log=MemoryLog(), store=MemoryStore(), budget=NullBudget(), resolver=resolver_for(spec2, URL),
        options={**kw["options"], "min_host_interval_s": 0}))
    assert cli.main(["read", URL, "--json"]) == 0
    data = _json.loads(capsys.readouterr().out)
    assert data["served"] and data["reader"] == "page" and data["records_per_channel"]["caption"] >= 1
    assert all("body" not in f for f in data["fetches"])
