"""Fixes found by the Track A / Track K runs of 2026-11-21, one offline test (or more) each.

1  a rendered DOM holding a lone UTF-16 surrogate no longer turns a browser read into a reader error
2  rednote.com/en/… note addresses parse; tokenless explore / discovery_item / rednote forms have their own names
3  weibo.com/status/<bid> is not offered as a link form; Weibo's generic 404 page is not a deletion
4  WeChat's account-migration notice is a deletion
5  X's suspended-account interstitial is a deletion
6  a post-level wall from a platform rule stops the reader chain; access-path walls fall through; the generic
   "served" rule needs positive evidence of a post
7  single-page apps get one second look (see also tests/test_readers_browser.py for the live-browser version)
8  YouTube is read watch-page first (title + description), oEmbed as the fallback
9  Weibo articles (ttarticle, comos) parse and are read by the page reader, then the browser
"""

from __future__ import annotations

import json

import pytest

from readproof import canonical, walls
from readproof.harness import Harness, TEXT_CHAIN, definitive, read_in
from readproof.interfaces import ReaderOutput
from readproof.platforms import by_name
from readproof.profiles import P0, P1
from readproof.readers.browser import BrowserSession, needs_second_look, utf8_safe
from readproof.readers.embed import parse_douyin_player
from readproof.testing import AllowGate, FakeNet, MemoryLog, MemoryStore, NullBudget, Scripted, make_context
from readproof.types import Channel, FetchResult, PostRef, WallType, WallVerdict, sha256_bytes

from .reading_fakes import P1_NO_BROWSER, FakeSpec, resolver_for


def _page(url: str, html: str, status: int = 200) -> FetchResult:
    body = html.encode("utf-8")
    return FetchResult(url=url, final_url=url, status=status, headers={"content-type": "text/html; charset=utf-8"},
                       bytes=len(body), sha256=sha256_bytes(body), fetched_at="", elapsed_ms=1, origin="t",
                       user_agent="t", body=body)


# --- 1: lone surrogates ---------------------------------------------------------------------------------------


def test_utf8_safe_replaces_only_lone_surrogates():
    text = "前 \ud83d 后 \U0001F600"
    safe = utf8_safe(text)
    assert safe == "前 � 后 \U0001F600"
    safe.encode("utf-8")  # does not raise


def test_browser_fetch_result_encodes_a_dom_with_a_lone_surrogate(tmp_path):
    ctx = make_context(P1, FakeNet({}), workdir=str(tmp_path))
    session = BrowserSession(ctx, reader="browser", block_media=True)  # not entered: no Chromium needed
    session.requested_url = "https://www.toutiao.com/article/1/"
    fetch = session.fetch_result("<html><body><p>半个表情\ud83d在这里</p></body></html>", session.requested_url)
    assert fetch.error is None and "�".encode("utf-8") in fetch.body
    assert fetch.sha256 == sha256_bytes(fetch.body)


# --- 2: Xiaohongshu / rednote -----------------------------------------------------------------------------------

NOTE = "69ccec66000000002202a360"


@pytest.mark.parametrize("url,form,locale", [
    (f"https://www.rednote.com/en/discovery/item/{NOTE}", "rednote_discovery_token_stripped", "en"),
    (f"https://www.rednote.com/en/explore/{NOTE}", "rednote_token_stripped", "en"),
    (f"https://www.rednote.com/discovery/item/{NOTE}", "rednote_discovery_token_stripped", None),
    (f"https://www.xiaohongshu.com/discovery/item/{NOTE}", "discovery_token_stripped", None),
    (f"https://www.xiaohongshu.com/explore/{NOTE}", "token_stripped", None),
])
def test_xiaohongshu_locale_paths_and_tokenless_form_names(url, form, locale):
    spec = by_name("xiaohongshu")
    ref = spec.parse(url)
    assert ref is not None and ref.post_id == NOTE and ref.form == form
    assert ref.extra.get("locale") == locale
    assert spec.canonical(ref) == f"https://www.xiaohongshu.com/explore/{NOTE}"


def test_xiaohongshu_tokenless_forms_round_trip_and_a_locale_listing_is_not_a_note():
    spec = by_name("xiaohongshu")
    ref = spec.parse(f"https://www.xiaohongshu.com/explore/{NOTE}")
    forms = [(n, u) for n, u in spec.forms(ref) if n != "share_text"]
    assert len({n for n, _ in forms}) == len(forms) == 3
    for name, url in forms:
        assert spec.parse(url).form == name, (name, url)
    assert spec.parse("https://www.rednote.com/en/explore") is None
    assert spec.page_kind("https://www.rednote.com/en/explore") == "listing"


def test_rednote_en_link_resolves_offline_to_the_note():
    ctx = make_context(P1, FakeNet({}), workdir="/tmp/readproof-test")
    res = canonical.resolve(f"https://www.rednote.com/en/discovery/item/{NOTE}", ctx)
    assert res.resolved and res.url == f"https://www.xiaohongshu.com/explore/{NOTE}" and res.fetches == []


# --- 3: Weibo forms and its generic 404 page -------------------------------------------------------------------


def test_weibo_forms_leave_out_weibo_com_status_but_still_parse_it():
    spec = by_name("weibo")
    ref = spec.parse("https://weibo.com/2/detail/5344861387493267")
    forms = dict(spec.forms(ref))
    assert "desktop_status" not in forms and not any("weibo.com/status/" in u for u in forms.values())
    assert set(forms) == {"mobile_detail", "mobile_status", "share_text"}
    assert "https://m.weibo.cn/detail/5344861387493267" in forms["share_text"]
    pasted = spec.parse("https://weibo.com/status/RiP34vrl9")  # people paste it; it still names the post
    assert pasted is not None and pasted.extra["mid"] == "5344861387493267"


def test_weibo_generic_404_page_is_not_a_deletion():
    html = ("<html><head><title>404错误</title></head><body><div>404错误</div>"
            "<p>抱歉，这个页面已经被外星人绑架了</p><a>返回首页</a></body></html>")
    v = walls.judge(_page("https://weibo.com/status/RiP34vrl9", html), by_name("weibo"))
    assert not v.served and v.wall is not WallType.DELETED
    assert v.signal.startswith("generic:no-post-evidence")


# --- 4, 5: WeChat migration notice and X suspended account -------------------------------------------------------


def test_wechat_account_migration_notice_is_deleted():
    html = ("<html><head><title>账号已迁移</title></head><body><h2>账号已迁移</h2><p>该公众号已迁移</p>"
            "<p>原账号迁移时未将文章素材同步至新账号，该链接已不可访问。</p><a>公众号迁移说明</a></body></html>")
    v = walls.judge(_page("https://mp.weixin.qq.com/s/OQ1HxnAuWkzgI4H_7CykSw", html), by_name("wechat"))
    assert v.wall is WallType.DELETED and v.signal.startswith("wechat:removed") and "该公众号已迁移" in v.signal


@pytest.mark.parametrize("text", ["This Post is from a suspended account. Learn more",
                                  "Account suspended X suspends accounts which violate the X Rules."])
def test_x_suspended_account_pages_are_deleted(text):
    chrome = "<span>New to X? Sign up now to get your own personalized timeline!</span>" * 4
    html = (f"<html><head><title>Post / X</title><meta property='og:title' content='Post'/></head>"
            f"<body><div>{text}</div>{chrome}</body></html>")
    v = walls.judge(_page("https://x.com/someone/status/1992989895850430908", html), by_name("x"))
    assert v.wall is WallType.DELETED and v.signal.startswith("x:gone"), v.signal


# --- 6: definitive walls stop the chain; the generic served rule needs evidence ----------------------------------


class _Reader:
    """A fake reader that returns a fixed verdict and records that it ran."""

    def __init__(self, name: str, verdict: WallVerdict, ran: list[str]) -> None:
        self.name, self.verdict, self.ran = name, verdict, ran

    def applies(self, ref, ctx) -> bool:
        return True

    def read(self, ref: PostRef, ctx) -> ReaderOutput:
        self.ran.append(self.name)
        out = ReaderOutput(self.name, self.verdict)
        if self.verdict.served:
            from readproof.types import Record

            out.records = [Record(record_id="r_1", post_key=ref.key, channel=Channel.CAPTION, locator={"field": "title"},
                                  text="placeholder page text", source_sha256="0" * 64, extractor="fake",
                                  fetched_at="t")]
        return out


def _chain(tmp_path, profile, platform: str, verdicts: dict[str, WallVerdict]):
    ran: list[str] = []
    readers = {name: _Reader(name, v, ran) for name, v in verdicts.items()}
    spec = FakeSpec(platform, post_id="7597416406057280753")
    url = "https://www.douyin.com/video/7597416406057280753"
    ctx = make_context(profile, FakeNet({}), gate=AllowGate(profile), workdir=str(tmp_path / "w"),
                       min_host_interval_s=0, speech=False, on_screen=False, images=False)
    res = read_in(ctx, url, resolver=resolver_for(spec, url), reader_factory=lambda name, spec_for: readers[name])
    return res, ran, ctx


SERVED_PLACEHOLDER = WallVerdict(True, None, "served status=200 bytes=1222257 visible_chars=1566 evidence=json-ld", 1566)


@pytest.mark.parametrize("profile", [P0, P1], ids=["P0", "P1"])
def test_douyin_player_self_see_ends_the_read_as_self_visible(tmp_path, profile):
    info = parse_douyin_player({"filter_detail": {"filter_reason": "status_self_see",
                                                  "detail_msg": "因作品权限或已被删除，无法观看，去看看其他作品吧"}})
    assert info is not None and info.wall.wall is WallType.SELF_VISIBLE
    assert definitive(ReaderOutput("embed", info.wall))
    res, ran, ctx = _chain(tmp_path, profile, "douyin", {"embed": info.wall, "browser": SERVED_PLACEHOLDER})
    assert TEXT_CHAIN["douyin"] == ["embed", "browser"]
    assert ran == ["embed"], "the browser must not run after the player's own post-level answer"
    assert not res.served and res.wall is WallType.SELF_VISIBLE and res.records == []
    assert ctx.log.of("chain_stopped") and ctx.log.of("chain_stopped")[0]["wall"] == "self_visible"


@pytest.mark.parametrize("wall,signal", [
    (WallType.DELETED, "douyin:gone status=200 bytes=900 visible_chars=30 marker=作品不存在"),
    (WallType.APP_ONLY, "douyin:player-cannot-play status=200 bytes=900 visible_chars=30"),
    (WallType.REGION, "youtube:region status=200 bytes=900 visible_chars=30"),
    (WallType.SELF_VISIBLE, "bilibili player code 62012 稿件仅UP主自己可见"),
])
def test_every_post_level_platform_wall_stops_the_chain(tmp_path, wall, signal):
    res, ran, _ = _chain(tmp_path, P1, "douyin", {"embed": WallVerdict(False, wall, signal), "browser": SERVED_PLACEHOLDER})
    assert ran == ["embed"] and not res.served and res.wall is wall


@pytest.mark.parametrize("wall,signal", [
    (WallType.CHALLENGE, "douyin:verify-page status=200 bytes=72914 visible_chars=0"),
    (WallType.LOGIN, "x:login-interstitial status=302 bytes=0 visible_chars=0"),
    (WallType.TOKEN, "xhs:page-gone-tokenless status=200 bytes=1 visible_chars=1"),
    (WallType.UNKNOWN, "douyin player declined filter_reason=core_dep"),
    (WallType.EMPTY, "generic:no-post-evidence status=200 bytes=1 visible_chars=1"),
    (WallType.DELETED, "generic:http-404 status=404 bytes=4 visible_chars=4"),  # generic, not a platform rule
    (WallType.POLICY, "gate refused (robots_disallow) https://www.douyin.com/video/1"),
    (WallType.NETWORK, "generic:transport status=None bytes=0 visible_chars=0"),
    (WallType.RATE_LIMITED, "generic:http-429 status=429 bytes=0 visible_chars=0"),
])
def test_access_path_and_generic_walls_fall_through(tmp_path, wall, signal):
    res, ran, _ = _chain(tmp_path, P1, "douyin", {"embed": WallVerdict(False, wall, signal), "browser": SERVED_PLACEHOLDER})
    assert ran == ["embed", "browser"] and res.served


def test_an_oembed_deletion_is_not_definitive():
    assert not definitive(ReaderOutput("oembed", WallVerdict(False, WallType.DELETED, "oembed http 404 (no such public post)")))
    assert not definitive(ReaderOutput("page", WallVerdict(True, None, "served")))


@pytest.mark.parametrize("extra,served", [
    ("", False),                                                               # a notice page: text, no evidence
    ("<meta property='og:title' content='账号已迁移说明'>", True),               # og:title names something
    ("<meta property='og:title' content='Post'>", False),                       # a placeholder og:title
    ("<script type='application/ld+json'>{\"@type\":\"Article\"}</script>", True),
    ("<script>window.__INITIAL_STATE__={}</script>", True),                     # a platform's inline state
])
def test_generic_served_needs_positive_evidence(extra, served):
    html = (f"<html><head><title>通知</title>{extra}</head><body><p>"
            + "这是一条通知，内容不长，但也不是空页面。" * 4 + "</p></body></html>")
    v = walls.judge(_page("https://example.com/notice", html))
    assert v.served is served, v.signal
    if not served:
        assert v.wall is WallType.EMPTY and v.signal.startswith("generic:no-post-evidence")


def test_generic_served_with_enough_text_and_the_evidence_is_in_the_signal():
    long_text = "这是一篇正常的文章，有足够多的可见文字。" * 12
    v = walls.judge(_page("https://example.com/a", f"<html><head><title>文章</title></head><body><p>{long_text}</p></body></html>"))
    assert v.served and f"evidence=visible_chars>={walls.POST_EVIDENCE_CHARS}" in v.signal


# --- 7: the second look ---------------------------------------------------------------------------------------


def test_second_look_rule():
    assert needs_second_look("https://m.weibo.cn/detail/5344861387493267", "微博 " * 50)  # an SPA host, always
    assert needs_second_look("https://www.douyin.com/video/1", "  抖音  ")  # under 40 characters after the settle
    assert not needs_second_look("https://www.douyin.com/video/1", "正文" * 30)


# --- 8: YouTube page first -----------------------------------------------------------------------------------

WATCH = "https://www.youtube.com/watch?v=abcdefghijk"
OEMBED = "https://www.youtube.com/oembed?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dabcdefghijk&format=json"


def _yt_page(status: str = "OK", reason: str = "") -> str:
    player = {"playabilityStatus": {"status": status, "reason": reason},
              "videoDetails": {"videoId": "abcdefghijk", "title": "Canary talk", "author": "Channel",
                               "shortDescription": "The code in the description is C-4K7QX.", "lengthSeconds": "61"}}
    return (f"<html><head><title>Canary talk - YouTube</title></head><body>"
            f"<script>var ytInitialPlayerResponse = {json.dumps(player)};</script></body></html>")


def _yt_harness(tmp_path, routes):
    net = FakeNet(routes)
    h = Harness(P1_NO_BROWSER, root=str(tmp_path), net=net, gate=AllowGate(P1_NO_BROWSER), log=MemoryLog(),
                store=MemoryStore(), budget=NullBudget(), resolver=canonical.resolve,
                options={"min_host_interval_s": 0, "speech": False, "on_screen": False, "images": False})
    return h, net


def test_youtube_reads_the_watch_page_first_and_keeps_the_description(tmp_path):
    assert TEXT_CHAIN["youtube"][:2] == ["page", "oembed"]
    h, net = _yt_harness(tmp_path, {WATCH: Scripted(body=_yt_page())})
    res = h.read(WATCH)
    assert res.served and res.reader == "page"
    caps = {(r.locator["field"], r.extractor): r.text for r in res.records if r.channel is Channel.CAPTION}
    assert caps[("title", "page:yt_player")] == "Canary talk"
    assert "C-4K7QX" in caps[("description", "page:yt_player")]  # the description oEmbed never carried
    assert [u for _, u in net.requests] == [WATCH]  # oEmbed is not asked when the page serves


def test_youtube_falls_back_to_oembed_when_the_page_is_walled(tmp_path):
    bot = _yt_page("LOGIN_REQUIRED", "Sign in to confirm you're not a bot")
    oembed = json.dumps({"title": "Canary talk", "author_name": "Channel", "type": "video"})
    h, net = _yt_harness(tmp_path, {WATCH: Scripted(body=bot), OEMBED: Scripted(body=oembed,
                                                                                headers={"content-type": "application/json"})})
    res = h.read(WATCH)
    assert res.served and res.reader == "oembed"
    assert [u for _, u in net.requests] == [WATCH, OEMBED]


# --- 9: Weibo articles --------------------------------------------------------------------------------------


@pytest.mark.parametrize("url,post_id,form,canon", [
    ("https://weibo.com/ttarticle/p/show?id=2309405187215650586882", "2309405187215650586882", "ttarticle",
     "https://weibo.com/ttarticle/p/show?id=2309405187215650586882"),
    ("https://weibo.com/ttarticle/x/m/geoshow/id/2309405366244819206168?domain=auto", "2309405366244819206168",
     "ttarticle_geoshow", "https://weibo.com/ttarticle/x/m/geoshow/id/2309405366244819206168"),
    ("https://weibo.com/2/detail/comos:nizmwas9943793", "comos:nizmwas9943793", "comos",
     "https://weibo.com/2/detail/comos:nizmwas9943793"),
])
def test_weibo_articles_parse_as_articles(url, post_id, form, canon):
    spec = by_name("weibo")
    ref = spec.parse(url)
    assert ref is not None and ref.post_id == post_id and ref.form == form and ref.extra["kind"] == "article"
    assert spec.canonical(ref) == canon
    forms = dict(spec.forms(ref))
    assert forms[form] == canon and canon in forms["share_text"]
    assert spec.parse("https://weibo.com/ttarticle/p/show?id=not-an-id") is None


def test_weibo_articles_are_read_by_the_page_then_the_browser(tmp_path):
    ran: list[str] = []
    walled = WallVerdict(False, WallType.CHALLENGE, "weibo:visitor-system status=200 bytes=9606 visible_chars=0")
    readers = {"page": _Reader("page", walled, ran), "browser": _Reader("browser", SERVED_PLACEHOLDER, ran)}
    url = "https://weibo.com/ttarticle/p/show?id=2309405187215650586882"
    ctx = make_context(P1, FakeNet({}), gate=AllowGate(P1), workdir=str(tmp_path / "w"), min_host_interval_s=0,
                       speech=False, on_screen=False, images=False)
    res = read_in(ctx, url, resolver=canonical.resolve, reader_factory=lambda name, spec_for: readers[name])
    assert ran == ["page", "browser"] and res.served and res.post.post_id == "2309405187215650586882"
