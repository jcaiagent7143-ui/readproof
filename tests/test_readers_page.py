"""The page and oEmbed readers: what a served page yields, and that a wall yields nothing."""

from __future__ import annotations

import json

from readproof.readers.oembed import OEmbedReader
from readproof.readers.page import PageReader, js_loads, js_to_json, parse_html
from readproof.testing import AllowGate, FakeNet, Scripted, make_context
from readproof.types import Channel, PostRef, WallType

from .reading_fakes import P1_NO_BROWSER, FakeSpec, wall_when

URL = "https://www.xiaohongshu.com/explore/66aa00000000000000000001"
FILLER = "<p>" + "这是一段足够长的可见正文，用来让页面不是空壳。" * 3 + "</p>"


def _read(spec: FakeSpec, url: str, body: str | bytes, *, status: int = 200, headers: dict | None = None, gate=None):
    net = FakeNet({url: Scripted(status=status, body=body, headers=headers or {"content-type": "text/html; charset=utf-8"})})
    ctx = make_context(P1_NO_BROWSER, net, gate=gate)
    ref = spec.parse(url)
    out = PageReader(spec_for=lambda r: spec).read(ref, ctx)
    return out, ctx, net


def _by(records, channel, field=None):
    return [r for r in records if r.channel is channel and (field is None or r.locator.get("field") == field)]


XHS_IMAGE_NOTE = """<html><head><title>上海咖啡探店 - 小红书</title>
<meta property="og:title" content="上海咖啡探店"><meta name="description" content="三家店的对比">
</head><body><div id="app">%s</div>
<script>window.__INITIAL_STATE__={"global":{"x":undefined},"note":{"noteDetailMap":{"66aa00000000000000000001":{
"comments":{"list":[]},"note":{"noteId":"66aa00000000000000000001","type":"normal","title":"上海咖啡探店",
"desc":"三家店的对比，第二家最好喝 #咖啡[话题]#","user":{"nickname":"小王"},"tagList":[{"name":"咖啡"}],
"imageList":[{"urlDefault":"http://sns-webpic-qc.xhscdn.com/a.jpg","infoList":[]},{"urlPre":"//sns-webpic-qc.xhscdn.com/b.jpg"}],
"video":undefined}}}},
"noteData":{"data":{"commentData":{"comments":[{"content":"第二家在哪里？"},{"content":""},{"content":"收藏了"}]}}}}</script>
</body></html>""" % FILLER


def test_xiaohongshu_image_note_served():
    spec = FakeSpec("xiaohongshu", post_id="66aa00000000000000000001")
    out, ctx, _ = _read(spec, URL, XHS_IMAGE_NOTE)
    assert out.verdict.served, out.verdict
    recs = out.records
    assert [r.text for r in _by(recs, Channel.CAPTION, "caption")] == ["三家店的对比，第二家最好喝 #咖啡[话题]#"]
    assert _by(recs, Channel.CAPTION, "author")[0].text == "小王"
    assert "上海咖啡探店" in [r.text for r in _by(recs, Channel.CAPTION, "title")]
    # the teaser meta description is inside the full caption: dropped
    assert not [r for r in recs if r.extractor == "page:meta"]
    comments = _by(recs, Channel.COMMENT)
    assert [(c.locator["rank"], c.text) for c in comments] == [(1, "第二家在哪里？"), (2, "收藏了")]
    assert out.meta["image_urls"] == ["https://sns-webpic-qc.xhscdn.com/a.jpg", "https://sns-webpic-qc.xhscdn.com/b.jpg"]
    assert out.meta["image_kinds"] == ["carousel", "carousel"]
    assert "media_urls" not in out.meta
    # every Record names the snapshot that is in the store
    sha = out.fetches[0].sha256
    assert all(r.source_sha256 == sha for r in recs) and ctx.store.get(sha) is not None
    assert all(r.post_key == "xiaohongshu:66aa00000000000000000001" for r in recs)


def test_xiaohongshu_video_note_lists_media_not_fetched():
    state = {"note": {"noteDetailMap": {"v1": {"note": {
        "type": "video", "title": "视频笔记", "desc": "描述", "user": {"nickName": "作者"},
        "imageList": [{"urlDefault": "https://img/cover.jpg"}],
        "video": {"capa": {"duration": 31}, "media": {"stream": {"h264": [
            {"height": 720, "masterUrl": "https://v/720.mp4", "backupUrls": ["https://bk/720.mp4"]},
            {"height": 1080, "masterUrl": "https://v/1080.mp4"}], "h265": [{"masterUrl": "https://v/h265.mp4"}]}}}}}}}}
    html = f"<html><head><title>视频笔记</title></head><body>{FILLER}<script>window.__INITIAL_STATE__={json.dumps(state)}</script></body></html>"
    spec = FakeSpec("xiaohongshu", post_id="v1")
    out, _, net = _read(spec, URL, html)
    assert out.verdict.served
    assert out.meta["media_urls"] == ["https://v/1080.mp4", "https://v/720.mp4", "https://bk/720.mp4"]
    assert out.meta["image_urls"] == ["https://img/cover.jpg"] and out.meta["image_kinds"] == ["cover"]
    assert out.meta["duration_s"] == 31.0
    assert net.requests == [("GET", URL)]  # media URLs are listed for extraction, never fetched by the reader


def test_bilibili_state():
    state = {"videoData": {"bvid": "BV1xx411c7mD", "title": "标题", "desc": "-", "owner": {"name": "UP主"},
                           "pic": "http://i0.hdslb.com/cover.jpg", "duration": 125,
                           "pages": [{"cid": 1, "page": 1, "part": "P1 开场", "duration": 60},
                                     {"cid": 2, "page": 2, "part": "P2 正文", "duration": 65}]}}
    html = ("<html><head><title>标题_哔哩哔哩_bilibili</title></head><body>" + FILLER +
            f"<script>window.__INITIAL_STATE__={json.dumps(state, ensure_ascii=False)};(function(){{var s;}}());</script></body></html>")
    spec = FakeSpec("bilibili", post_id="BV1xx411c7mD")
    out, _, _ = _read(spec, "https://www.bilibili.com/video/BV1xx411c7mD", html)
    assert out.verdict.served
    assert _by(out.records, Channel.CAPTION, "author")[0].text == "UP主"
    assert not _by(out.records, Channel.CAPTION, "description")  # "-" means no description
    assert [p["part"] for p in out.meta["parts"]] == ["P1 开场", "P2 正文"]
    assert out.meta["image_urls"] == ["https://i0.hdslb.com/cover.jpg"]


def test_tiktok_universal_data():
    data = {"__DEFAULT_SCOPE__": {"webapp.video-detail": {"statusCode": 0, "itemInfo": {"itemStruct": {
        "id": "7300000000000000001", "desc": "How to fold a shirt in 2 seconds #lifehack",
        "author": {"uniqueId": "folder", "nickname": "The Folder"},
        "video": {"playAddr": "https://v16-webapp.tiktok.com/play.mp4", "duration": 15,
                  "bitrateInfo": [{"PlayAddr": {"UrlList": ["https://v19.tiktokcdn.com/alt.mp4"]}}],
                  "cover": "https://p16.tiktokcdn.com/cover.jpeg"}}}}}}
    html = ("<html><head><title>TikTok</title></head><body>" + FILLER +
            f'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{json.dumps(data)}</script></body></html>')
    spec = FakeSpec("tiktok", post_id="7300000000000000001")
    out, _, _ = _read(spec, "https://www.tiktok.com/@folder/video/7300000000000000001", html)
    assert out.verdict.served
    assert _by(out.records, Channel.CAPTION, "caption")[0].text.startswith("How to fold")
    assert _by(out.records, Channel.CAPTION, "author")[0].text == "The Folder"
    assert out.meta["media_urls"] == ["https://v16-webapp.tiktok.com/play.mp4", "https://v19.tiktokcdn.com/alt.mp4"]
    assert out.meta["status_code"] == 0


def test_weibo_render_data():
    status = {"text": "今天<br />天气很好<img alt=\"[太阳]\" src=\"x.png\"> <a href=\"/n/x\">@朋友</a>",
              "user": {"screen_name": "博主"},
              "pics": [{"large": {"url": "https://wx1.sinaimg.cn/large/a.jpg"}}, {"url": "https://wx1.sinaimg.cn/orj360/b.jpg"}],
              "page_info": {"type": "video", "media_info": {"stream_url": "https://f.video.weibocdn.com/s.mp4",
                                                            "mp4_720p_mp4": "https://f.video.weibocdn.com/720.mp4", "duration": 12.5}}}
    html = ("<html><head><title>微博正文</title></head><body>" + FILLER +
            f"<script>var $render_data = [{json.dumps({'status': status}, ensure_ascii=False)}][0] || {{}};</script></body></html>")
    spec = FakeSpec("weibo", post_id="5000000000000000")
    out, _, _ = _read(spec, "https://m.weibo.cn/detail/5000000000000000", html)
    assert out.verdict.served
    assert _by(out.records, Channel.CAPTION, "caption")[0].text == "今天\n天气很好[太阳] @朋友"
    assert out.meta["media_urls"] == ["https://f.video.weibocdn.com/720.mp4", "https://f.video.weibocdn.com/s.mp4"]
    assert out.meta["image_urls"][0] == "https://wx1.sinaimg.cn/large/a.jpg"


def test_wechat_article():
    html = ("<html><head><title>文章标题</title></head><body><h1 id='activity-name'>  文章标题  </h1><a id='js_name'>公众号A</a>"
            "<div id='js_content' style='visibility:hidden'><p>第一段正文，讲了三个要点：成本、速度和可靠性，每一点都有数据。</p>"
            "<section><p>第二段：结论是 42。</p></section>"
            "<img data-src='https://mmbiz.qpic.cn/a.png'><img data-src='https://mmbiz.qpic.cn/a.png'></div>"
            "<script>var msg_title = '文章标题'.html(false); var msg_desc = htmlDecode(\"摘要 &amp; 引言\");</script></body></html>")
    spec = FakeSpec("wechat", post_id="abc")
    out, _, _ = _read(spec, "https://mp.weixin.qq.com/s/abc", html)
    assert out.verdict.served
    assert _by(out.records, Channel.CAPTION, "title")[0].text == "文章标题"
    assert _by(out.records, Channel.CAPTION, "description")[0].text == "摘要 & 引言"
    body = "\n".join(r.text for r in _by(out.records, Channel.PAGE))
    assert "第一段正文" in body and "结论是 42" in body
    assert out.meta["image_urls"] == ["https://mmbiz.qpic.cn/a.png"]


def test_jsonld_article_and_generic_web_text():
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebSite", "name": "Site"},
        {"@type": "NewsArticle", "headline": "Rates held", "description": "The bank held rates.",
         "articleBody": "The central bank held rates at 3%. It cited inflation.", "author": [{"name": "A. Writer"}]}]}
    html = (f"<html><head><title>Rates held | Site</title><script type='application/ld+json'>{json.dumps(ld)}</script>"
            "<meta property='og:description' content='The bank held rates.'></head>"
            "<body><nav>Home</nav><article><p>The central bank held rates at 3%.</p><p>A sidebar sentence about markets.</p></article></body></html>")
    spec = FakeSpec("web", post_id="w1")
    out, _, _ = _read(spec, "https://news.example.com/rates", html)
    assert out.verdict.served
    assert _by(out.records, Channel.CAPTION, "author")[0].text == "A. Writer"
    pages = _by(out.records, Channel.PAGE)
    assert any("It cited inflation" in r.text and r.extractor == "page:jsonld" for r in pages)
    assert any("sidebar sentence" in r.text and r.extractor == "page:text" for r in pages)
    assert all("offset" in r.locator for r in pages)


def test_platform_wall_yields_no_records():
    body = "<html><head><title>安全验证</title></head><body>" + FILLER + "<div>请完成安全验证 verify-marker</div></body></html>"
    spec = FakeSpec("xiaohongshu", judge=wall_when("verify-marker"))
    out, ctx, _ = _read(spec, URL, body)
    assert not out.verdict.served and out.verdict.wall is WallType.CHALLENGE
    assert out.records == [] and "media_urls" not in out.meta and "image_urls" not in out.meta
    assert ctx.store.get(out.fetches[0].sha256) is not None  # the wall is evidence in the store...
    assert ctx.store.records == []  # ...and never a Record


def test_http_404_is_deleted_and_gate_refusal_sends_nothing():
    spec = FakeSpec("web", post_id="gone")
    out, _, _ = _read(spec, "https://example.com/gone", "<html><title>Not found</title></html>", status=404)
    assert not out.verdict.served and out.verdict.wall is WallType.DELETED and out.records == []

    gate = AllowGate(P1_NO_BROWSER, deny=lambda u: "robots_disallow")
    out, _, net = _read(spec, "https://example.com/blocked", "<html>x</html>", gate=gate)
    assert out.verdict.wall is WallType.POLICY and net.requests == [] and out.fetches == []


def test_served_page_without_text_is_empty():
    spec = FakeSpec("web", post_id="e")
    out, _, _ = _read(spec, "https://example.com/e", "<html><head></head><body><div id='root'></div></body></html>")
    assert not out.verdict.served and out.records == []


def test_js_to_json_handles_platform_state():
    src = """{"a":undefined,'b':'it\\'s \\x41',c:[1,2,],d:!0,e:NaN,"g":"\\u4e2d",/* note */"h":{"i":"a}b"}}"""
    assert json.loads(js_to_json(src)) == {"a": None, "b": "it's A", "c": [1, 2], "d": True, "e": None, "g": "中",
                                          "h": {"i": "a}b"}}
    assert js_loads("not an object") is None
    # live 2026-11-21, the RedNote note page's state: constructor calls inside the data
    assert js_loads('{"originalCommentIds":new Set([]),"m":new Map([["k",1]]),"d":new Date(5),"s":"new Set(x)"}') == {
        "originalCommentIds": [], "m": [["k", 1]], "d": 5, "s": "new Set(x)"}
    assert js_loads("{new: 1, Set: new Set([2])}") == {"new": 1, "Set": [2]}  # keys named like the keywords stay


def test_parse_html_ignores_state_of_other_platforms():
    html = "<html><head><title>T</title></head><body><script>window.__INITIAL_STATE__={\"note\":{}}</script><p>hello world text</p></body></html>"
    col = parse_html(html, platform="web", url="https://e.com/")
    assert not col.found_post and any(i.channel is Channel.PAGE for i in col.items)


# --- oEmbed ------------------------------------------------------------------------------------------------

OEMBED = "https://www.youtube.com/oembed?url=x&format=json"


def _oembed(platform: str, body: str | bytes, status: int = 200):
    spec = FakeSpec(platform, oembed=OEMBED)
    net = FakeNet({OEMBED: Scripted(status=status, body=body, headers={"content-type": "application/json"})})
    ctx = make_context(P1_NO_BROWSER, net)
    ref = PostRef(platform, "abc", "desktop", "https://www.youtube.com/watch?v=abc")
    reader = OEmbedReader(spec_for=lambda r: spec)
    assert reader.applies(ref, ctx)
    return reader.read(ref, ctx), ctx


def test_oembed_served_with_title():
    out, ctx = _oembed("youtube", json.dumps({"title": "Talk title", "author_name": "Channel",
                                              "thumbnail_url": "http://i.ytimg.com/vi/abc/hqdefault.jpg"}))
    assert out.verdict.served
    assert {(r.locator["field"], r.text, r.extractor) for r in out.records} == {("title", "Talk title", "oembed"),
                                                                              ("author", "Channel", "oembed")}
    assert out.meta["image_urls"] == ["https://i.ytimg.com/vi/abc/hqdefault.jpg"]
    assert ctx.gate.asked == [OEMBED]


def test_oembed_x_blockquote_and_walls():
    html = '<blockquote class="twitter-tweet"><p lang="en">Shipping it today &amp; tomorrow</p>&mdash; Dev (@dev)</blockquote>'
    out, _ = _oembed("x", json.dumps({"author_name": "Dev", "html": html}))
    assert out.verdict.served and [r.text for r in out.records if r.locator["field"] == "caption"] == ["Shipping it today & tomorrow"]
    out, _ = _oembed("youtube", json.dumps({"author_name": "Only author"}))
    assert not out.verdict.served and out.verdict.wall is WallType.EMPTY and out.records == []
    out, _ = _oembed("youtube", "Not Found", status=404)
    assert out.verdict.wall is WallType.DELETED and out.records == []
    out, _ = _oembed("youtube", "<html>oops</html>")
    assert not out.verdict.served and out.records == []


def test_article_body_is_the_post_and_navigation_is_left_out():
    body = "".join(f"<p>正文第{i}段：央行宣布维持利率不变，并解释了通胀与就业的权衡。</p>" for i in range(4))
    html = ("<html><head><title>央行维持利率 - 今日头条</title><meta property='og:title' content='央行维持利率'></head>"
            "<body><nav>首页 推荐 财经 科技 热榜</nav>"
            f"<article class='syl-article-base'>{body}</article><aside>热榜：黄金为啥突然不香了</aside></body></html>")
    spec = FakeSpec("toutiao", post_id="7700000000000000001")
    out, _, _ = _read(spec, "https://www.toutiao.com/article/7700000000000000001/", html)
    assert out.verdict.served
    pages = _by(out.records, Channel.PAGE)
    assert pages and all(r.extractor == "page:article" for r in pages)
    text = "\n".join(r.text for r in pages)
    assert "正文第3段" in text and "热榜" not in text and "首页" not in text
