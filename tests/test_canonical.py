"""canonical.extract_url and canonical.resolve (DESIGN §4 steps 1–4), offline with FakeNet.

What must hold: a short link is expanded one hop at a time (HEAD, redirects off; GET when HEAD gives >= 400
or no Location); the gate is asked before every request; a hop a rewrite makes unnecessary is never fetched;
a login interstitial wrapping a post is unwrapped without being fetched; an unknown or case-flipped code
that lands on the home page is a soft_404. The Douyin home-page redirect is the one observed live on
2026-11-21 (v.douyin.com/<unknown>/ -> 302 https://www.douyin.com, ReadProof UA).
"""

from __future__ import annotations

from urllib.parse import quote

import pytest

from readproof import USER_AGENT
from readproof.canonical import MAX_HOPS, extract_url, resolve
from readproof.platforms import by_name
from readproof.testing import AllowGate, FakeNet, Scripted, make_context
from readproof.types import Profile, WallType

PROFILE = Profile(name="P1", obey_robots=False, obey_content_signal=False, user_initiated=True,
                  allow_browser=True, allow_embed_media=True, user_agent=USER_AGENT)

DY = "7675706162171448622"
NOTE = "6ab51f8f000000001a032ae4"
TOUTIAO = "7709705250554266155"


class OrderedGate(AllowGate):
    def __init__(self, events: list, deny=None) -> None:
        super().__init__(PROFILE, deny)
        self.events = events

    def check(self, url: str, *, purpose: str = "read"):
        self.events.append(("gate", url))
        return super().check(url, purpose=purpose)


class OrderedNet(FakeNet):
    def __init__(self, routes: dict, events: list) -> None:
        super().__init__(routes, user_agent=USER_AGENT)
        self.events = events

    def fetch(self, url: str, **kw):
        self.events.append(("net", kw.get("method", "GET"), url))
        return super().fetch(url, **kw)


def run(text: str, routes: dict, deny=None):
    events: list = []
    net = OrderedNet(routes, events)
    gate = OrderedGate(events, deny)
    ctx = make_context(PROFILE, net, gate)
    return resolve(text, ctx), net, gate, events, ctx


def redirect(location: str, status: int = 302) -> Scripted:
    return Scripted(status=status, headers={"Location": location})


# --- extract_url ---------------------------------------------------------------------------------------------

EXTRACT = [
    ("7.43 复制打开抖音，看看【某某的作品】标题 #话题 https://v.douyin.com/VeXgdE_QwTE/ 复制此链接，打开Dou音搜索，直接观看视频！",
     "https://v.douyin.com/VeXgdE_QwTE/"),
    ("8.94 L@w.Fh 07/18 bnD:/ 复制打开抖音，看看【作者的作品】 https://v.douyin.com/iRNBh6qT/复制此链接", "https://v.douyin.com/iRNBh6qT/"),
    ("【标题 - 作者 | 小红书 - 你的生活指南】 😆 http://xhslink.com/a/AbCdEf 复制本条信息，打开【小红书】App查看精彩内容！",
     "http://xhslink.com/a/AbCdEf"),
    ("http://xhslink.com/a/AbC😆", "http://xhslink.com/a/AbC"),
    ("详情见ｈｔｔｐｓ：／／ｗｗｗ．ｅｘａｍｐｌｅ．ｃｏｍ／ｘ，点这里", "https://www.example.com/x"),
    ("链接：https://a.com/x？", "https://a.com/x"),
    ("（https://a.com/x）。", "https://a.com/x"),
    ("【https://a.com/x】", "https://a.com/x"),
    ("「https://a.com/x」、", "https://a.com/x"),
    ("see https://en.wikipedia.org/wiki/Foo_(bar).", "https://en.wikipedia.org/wiki/Foo_(bar)"),
    ("[the post](https://a.com/x)", "https://a.com/x"),
    ("快来看～https://a.com/x～", "https://a.com/x"),
    ("  https://x.com/jack/status/20  \n", "https://x.com/jack/status/20"),
    ("HTTPS://Example.com/A", "https://Example.com/A"),
    ("first https://a.com/1 then https://b.com/2", "https://a.com/1"),
    ("标题 - 今日头条 https://m.toutiao.com/is/iABcd12x/", "https://m.toutiao.com/is/iABcd12x/"),
    ("【标题-哔哩哔哩】 https://b23.tv/Oieigam", "https://b23.tv/Oieigam"),
    ("看 b23.tv/AbC123 吧", "https://b23.tv/AbC123"),
    ("打开 www.xiaohongshu.com/explore/69de07d8000000002103b4fd 看看", "https://www.xiaohongshu.com/explore/69de07d8000000002103b4fd"),
    ("https://www.xiaohongshu.com/explore/69de07d8000000002103b4fd?xsec_token=ABx+y/LLc=&xsec_source=pc_feed",
     "https://www.xiaohongshu.com/explore/69de07d8000000002103b4fd?xsec_token=ABx+y/LLc=&xsec_source=pc_feed"),
    ("no link here", None),
    ("", None),
    ("https://", None),
]


@pytest.mark.parametrize("text,expected", EXTRACT)
def test_extract_url(text, expected):
    assert extract_url(text) == expected


# --- resolve: no request needed ------------------------------------------------------------------------------


def test_a_post_url_is_resolved_without_any_request():
    res, net, gate, events, _ = run(f"https://m.toutiao.com/group/{TOUTIAO}/?share_token=x", {})
    assert res.resolved and res.verdict is None
    assert res.url == f"https://www.toutiao.com/article/{TOUTIAO}/"
    assert (res.ref.platform, res.ref.post_id, res.ref.form) == ("toutiao", TOUTIAO, "group")
    assert events == [] and res.fetches == [] and res.gate == []


def test_youtu_be_needs_no_expansion():
    res, net, *_ = run("https://youtu.be/dQw4w9WgXcQ?si=abc", {})
    assert res.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ" and net.requests == []


def test_a_login_interstitial_pasted_as_is_is_unwrapped_without_a_request():
    login = "https://www.xiaohongshu.com/login?redirectPath=" + quote(
        f"http://www.xiaohongshu.com/discovery/item/{NOTE}?xsec_source=app_share&xsec_token=CBabc%3D", safe="")
    res, net, *_ = run(login, {login: Scripted(200, "<html>login page</html>")})
    assert res.resolved and res.ref.form == "login_wrapped" and res.ref.extra["xsec_token"] == "CBabc="
    assert res.url == f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=CBabc=&xsec_source=pc_share"
    assert net.requests == []


def test_web_and_linkedin_posts_are_identified_not_fetched():
    res, net, *_ = run("read this: https://example.com/a?utm_source=x", {})
    assert res.ref.platform == "web" and res.url == "https://example.com/a" and net.requests == []
    res, net, *_ = run("https://www.linkedin.com/posts/someone_t-activity-7260000000000000000-AbCd", {})
    assert res.ref.platform == "linkedin" and net.requests == []


def test_no_url_is_unsupported():
    res, net, *_ = run("这条视频太好笑了", {})
    assert res.verdict.wall is WallType.UNSUPPORTED and res.verdict.signal.startswith("resolve:no-url")
    assert res.url is None and net.requests == []


def test_a_platform_page_that_is_not_a_post_is_unsupported_without_a_request():
    res, net, *_ = run("https://www.douyin.com/user/MS4wLjABAAAA0hrPqt3M7MUOZ9eo55AdMwLA81kkobjkG7lExwmze8aW9n5563rMZQ4DAfMREcvE", {})
    assert res.verdict.wall is WallType.UNSUPPORTED and "page_kind=profile" in res.verdict.signal
    assert net.requests == []


# --- resolve: short links --------------------------------------------------------------------------------------


def test_a_share_text_short_link_is_expanded_with_one_head():
    short = "https://v.douyin.com/VeXgdE_QwTE/"
    target = f"https://www.iesdouyin.com/share/video/{DY}/?region=CN&mid=1&u_code=x&did=1"
    text = f"7.32 复制打开抖音，看看【某某的作品】 {short} 复制此链接"
    res, net, gate, events, ctx = run(text, {short: redirect(target)})
    assert res.resolved
    assert (res.ref.platform, res.ref.post_id, res.ref.form) == ("douyin", DY, "share_page")
    assert res.url == f"https://www.douyin.com/video/{DY}"
    assert events == [("gate", short), ("net", "HEAD", short)]
    assert [f.method for f in res.fetches] == ["HEAD"] and len(res.gate) == 1 and res.gate[0].allowed
    assert res.input == text
    summary = ctx.log.of("resolve")
    assert len(summary) == 1 and summary[0]["post_key"] == f"douyin:{DY}" and summary[0]["requests"] == 1


def test_the_gate_is_asked_before_every_request_of_every_hop():
    chain = {
        "https://t.co/AbCdEf1234": redirect("https://vm.tiktok.com/ZMabcdef/", 301),
        # This shortener refuses HEAD: the hop is asked again with GET, and the gate again before it.
        "https://vm.tiktok.com/ZMabcdef/": [Scripted(405), redirect("https://www.tiktok.com/@scout2015/video/6718335390845095173?_r=1")],
    }
    res, net, gate, events, _ = run("https://t.co/AbCdEf1234", chain)
    assert res.resolved and res.ref.post_id == "6718335390845095173"
    assert res.url == "https://www.tiktok.com/@scout2015/video/6718335390845095173"
    assert events == [
        ("gate", "https://t.co/AbCdEf1234"), ("net", "HEAD", "https://t.co/AbCdEf1234"),
        ("gate", "https://vm.tiktok.com/ZMabcdef/"), ("net", "HEAD", "https://vm.tiktok.com/ZMabcdef/"),
        ("gate", "https://vm.tiktok.com/ZMabcdef/"), ("net", "GET", "https://vm.tiktok.com/ZMabcdef/"),
    ]
    # Every request was preceded by its own gate decision, and redirects were never left to the client.
    for i, event in enumerate(events):
        if event[0] == "net":
            assert events[i - 1] == ("gate", event[2])


def test_head_without_a_location_falls_back_to_get():
    short = "https://b23.tv/Oieigam"
    res, net, *_ = run(short, {short: [Scripted(200), redirect("https://www.bilibili.com/video/BV1yLH967Ehy/?share_source=copy")]})
    assert res.resolved and res.ref.post_id == "BV1yLH967Ehy"
    assert net.requests == [("HEAD", short), ("GET", short)]


def test_a_disallowed_hop_is_never_fetched_when_the_rewrite_exists():
    """Toutiao's short link points at /group/<id>/, which robots.txt disallows (live 2026-11-21): the id is
    read off the Location header and the read goes to /article/<id>/ — /group/ is neither asked nor fetched."""
    short = "https://m.toutiao.com/is/iABcd12x/"
    group = f"https://m.toutiao.com/group/{TOUTIAO}/?upstream_biz=toutiao_pc&share_token=x"

    def robots(url: str) -> str | None:
        return "robots_disallow" if "/group/" in url or "/item/" in url else None

    res, net, gate, events, _ = run(short, {short: redirect(group), group: Scripted(200, "<html>should never be read</html>")}, robots)
    assert res.resolved and res.verdict is None
    assert res.url == f"https://www.toutiao.com/article/{TOUTIAO}/"
    assert res.ref.form == "group"
    assert net.requests == [("HEAD", short)]
    assert gate.asked == [short]
    assert all("/group/" not in url for _, url in net.requests)


def test_a_refused_short_link_is_policy_and_no_request_is_sent():
    short = "https://xhslink.com/a/AbCdEfGh"
    res, net, gate, *_ = run(short, {short: redirect(f"https://www.xiaohongshu.com/explore/{NOTE}")},
                             lambda url: "robots_disallow" if "xhslink" in url else None)
    assert res.verdict.wall is WallType.POLICY and not res.resolved
    assert "reason=robots_disallow" in res.verdict.signal and "hop=1" in res.verdict.signal
    assert net.requests == [] and gate.asked == [short] and not res.gate[0].allowed


def test_a_refused_later_hop_without_a_rewrite_is_policy():
    first = "https://t.co/AbCdEf1234"
    res, net, *_ = run(first, {first: redirect("https://vm.tiktok.com/ZMabcdef/", 301)},
                       lambda url: "profile" if "vm.tiktok.com" in url else None)
    assert res.verdict.wall is WallType.POLICY and "hop=2" in res.verdict.signal
    assert net.requests == [("HEAD", first)]


def test_a_case_flipped_short_code_is_a_soft_404():
    """The control form from forms(): one letter of a real code with its case flipped. Douyin sends an unknown
    code to its home page — the 302 below is the response captured live on 2026-11-21."""
    spec = by_name("douyin")
    forms = dict(spec.forms(spec.parse(f"https://www.douyin.com/video/{DY}"), short="https://v.douyin.com/VeXgdE_QwTE/"))
    flipped = forms["short_case_flipped"]
    assert flipped == "https://v.douyin.com/veXgdE_QwTE/"
    live_302 = Scripted(302, b"", headers={"server": "Tengine", "content-type": "text/html; charset=utf-8",
                                            "location": "https://www.douyin.com"})
    res, net, *_ = run(flipped, {flipped: live_302})
    assert res.verdict.wall is WallType.SOFT_404 and not res.resolved
    assert "page_kind=home" in res.verdict.signal and "location=https://www.douyin.com" in res.verdict.signal
    assert res.url == "https://www.douyin.com" and res.ref is None and res.spec.name == "douyin"
    assert net.requests == [("HEAD", flipped)], "the home page itself is never fetched"


def test_a_dead_xhslink_that_opens_the_feed_is_a_soft_404():
    short = "https://xhslink.com/a/Y2u5z8FSv5c1"
    res, *_ = run(short, {short: redirect("https://www.xiaohongshu.com/explore?exSource=null")})
    assert res.verdict.wall is WallType.SOFT_404 and "page_kind=listing" in res.verdict.signal


def test_a_login_interstitial_in_a_redirect_is_unwrapped_and_never_fetched():
    """LinkDigest 2026-10-28: an xhslink.cn share 302'd to /login?redirectPath=<the note>, and fetching that
    login page timed out — losing a note the first redirect had already named."""
    short = "https://xhslink.cn/o/90GV2AViNUV"
    note = (f"http://www.xiaohongshu.com/discovery/item/{NOTE}?app_platform=android"
            "&xsec_source=app_share&type=normal&xsec_token=CBabc%3D")
    login = "https://www.xiaohongshu.com/login?redirectPath=" + quote(note, safe="")
    res, net, gate, *_ = run(short, {short: redirect(login), login: Scripted(error="ConnectTimeout: handshake timed out")})
    assert res.resolved and res.ref.form == "login_wrapped" and res.ref.post_id == NOTE
    assert res.ref.extra["xsec_token"] == "CBabc=", "double-encoded through the interstitial, decoded exactly once"
    assert res.url == f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=CBabc=&xsec_source=pc_share"
    assert net.requests == [("HEAD", short)] and gate.asked == [short]


def test_the_live_rednote_login_location_is_unwrapped_too():
    """Live 2026-11-21: rednote.com answered the ReadProof UA with this Location for a tokenless note."""
    short = "https://xhslink.com/a/AbCdEfGh"
    live = ("https://www.rednote.com/login?redirectPath=http%3A%2F%2Fwww.rednote.com%2Fexplore%2F"
            "69de07d8000000002103b4fd%3FexSource%3Dnull")
    res, net, *_ = run(short, {short: redirect(live)})
    assert res.resolved and res.ref.post_id == "69de07d8000000002103b4fd" and res.ref.form == "login_wrapped"
    assert res.url == "https://www.xiaohongshu.com/explore/69de07d8000000002103b4fd"
    assert net.requests == [("HEAD", short)]


def test_a_dead_share_behind_the_login_interstitial_is_a_soft_404():
    """LinkDigest 2026-11-12: xhslink.com/m/… ended on /login?redirectPath=…/explore?exSource=null."""
    short = "https://xhslink.com/m/6LLm1koswe"
    login = "https://www.xiaohongshu.com/login?redirectPath=http%3A%2F%2Fwww.rednote.com%2Fexplore%3FexSource%3Dnull"
    res, net, *_ = run(short, {short: redirect(login)})
    assert res.verdict.wall is WallType.SOFT_404 and "page_kind=listing" in res.verdict.signal
    assert net.requests == [("HEAD", short)]


def test_a_short_link_to_a_profile_is_not_a_post():
    short = "https://v.douyin.com/zoxA27X8wyo/"
    res, *_ = run(short, {short: redirect("https://www.iesdouyin.com/share/user/MS4wLjABAAAAKKX6BLaLg3TTzsvjbh8R63NnPawIQp9nirYg9uYJb_w?iid=x")})
    assert res.verdict.wall is WallType.UNSUPPORTED and "page_kind=profile" in res.verdict.signal


def test_a_short_link_to_a_tiktok_shop_page_is_not_a_post():
    short = "https://vm.tiktok.com/ZN9fqLTAr7M54-IvjOg/"
    res, *_ = run(short, {short: redirect("https://www.tiktok.com/view/product/1729000000000000000?_r=1")})
    assert res.verdict.wall is WallType.UNSUPPORTED and "page_kind=product" in res.verdict.signal


def test_a_transport_error_on_the_short_link_is_network():
    short = "https://xhslink.cn/o/5IdxaqmZULc"
    res, net, *_ = run(short, {short: Scripted(error="ConnectTimeout: The handshake operation timed out")})
    assert res.verdict.wall is WallType.NETWORK and "handshake" in res.verdict.signal
    assert net.requests == [("HEAD", short)]


def test_an_unknown_short_link_that_answers_404_is_deleted():
    short = "https://b23.tv/NoSuchCode"
    res, net, *_ = run(short, {short: Scripted(404, "not found")})
    assert res.verdict.wall is WallType.DELETED
    assert net.requests == [("HEAD", short), ("GET", short)]


def test_a_short_link_that_answers_with_a_page_of_its_own_is_a_soft_404():
    short = "https://t.cn/A6abcdEF"
    page = "<html><head><title>新浪短链接</title></head><body><p>" + "你即将访问一个外部页面，请注意安全。" * 5 + "</p></body></html>"
    res, net, _, _, ctx = run(short, {short: [Scripted(200), Scripted(200, page, headers={"content-type": "text/html"})]})
    assert res.verdict.wall is WallType.SOFT_404 and res.verdict.signal.startswith("resolve:no-redirect")
    assert net.requests == [("HEAD", short), ("GET", short)]
    assert res.fetches[-1].sha256 in ctx.store.blobs, "the body that decided the verdict is kept as evidence"


def test_a_relative_location_is_joined_and_the_next_short_hop_followed():
    first = "https://xhslink.com/a/AbCdEfGh"
    res, net, *_ = run(first, {first: redirect("/a/ZyXwVu"),
                               "https://xhslink.com/a/ZyXwVu": redirect(f"https://www.xiaohongshu.com/discovery/item/{NOTE}?xsec_token=CB=")})
    assert res.resolved and res.ref.post_id == NOTE
    assert net.requests == [("HEAD", first), ("HEAD", "https://xhslink.com/a/ZyXwVu")]


def test_a_redirect_loop_stops_after_max_hops():
    a, b = "https://t.co/LoopA", "https://t.co/LoopB"
    res, net, gate, *_ = run(a, {a: redirect(b, 301), b: redirect(a, 301)})
    assert res.verdict.wall is WallType.UNKNOWN and res.verdict.signal.startswith("resolve:too-many-hops")
    assert len(net.requests) == MAX_HOPS == 8 and len(gate.asked) == MAX_HOPS


def test_an_app_deep_link_location_is_unsupported():
    short = "https://v.douyin.com/AbCdEfG/"
    res, *_ = run(short, {short: redirect(f"snssdk1128://aweme/detail/{DY}")})
    assert res.verdict.wall is WallType.UNSUPPORTED and "non-web-location" in res.verdict.signal


def test_every_hop_is_logged():
    short = "https://v.douyin.com/VeXgdE_QwTE/"
    res, _, _, _, ctx = run(short, {short: redirect(f"https://www.iesdouyin.com/share/video/{DY}/")})
    hops = ctx.log.of("resolve.hop")
    assert len(hops) == 1 and hops[0]["method"] == "HEAD" and hops[0]["status"] == 302
    assert hops[0]["location"].startswith("https://www.iesdouyin.com/share/video/")
