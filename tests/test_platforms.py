"""Platform specs: parse, canonical, forms, embed and oEmbed for the link shapes people really paste.

URL shapes come from LinkDigest's production resolver and its tests (2026-09 → 2026-11). Ids are well-formed;
the Weibo and Bilibili id pairs were verified live by LinkDigest, the rest are synthetic.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

import pytest

from readproof.canonical import extract_url
from readproof.platforms import REGISTRY, by_name, for_url
from readproof.platforms import bilibili, weibo, xiaohongshu
from readproof.platforms.web import normalise

DY = "7664299952662777098"
NOTE = "69de07d8000000002103b4fd"
BV = "BV1JE411g7XF"
YT = "dQw4w9WgXcQ"
TT = "6718335390845095173"
TOUTIAO = "7709705250554266155"
MID, BID = "5351369302084798", "RlylI8KlM"

# LinkDigest fixtures: a real app share that landed on the login interstitial, its rednote twin, and the
# /404/sec_ wall that nests redirectPath inside its `source` value (tests/test_xhs_login_redirect.py).
XHS_LOGIN = ("https://www.xiaohongshu.com/login?redirectPath=http%3A%2F%2Fwww.xiaohongshu.com%2Fdiscovery%2Fitem%2F"
             "6aa180590000000028000dcc%3Fxsec_source%3Dapp_share%26type%3Dnormal%26xsec_token%3DCBFIPHMRiTVCtDNCWr5D2D-uDjQR-wN4FeadK6hRZ6dFo%253D%26share_id%3Da2a6")
XHS_REDNOTE_LOGIN = ("https://www.rednote.com/login?redirectPath=http%3A%2F%2Fwww.rednote.com%2Fdiscovery%2Fitem%2F"
                     "5b691fbe910cf67d445794f6%3Fapp_platform%3Dandroid%26xsec_source%3Dapp_share%26type%3Dnormal"
                     "%26xsec_token%3DCBmXyt5EYQBBLE7pF9c1Yx4Ern5QgC_tAup5zIEcO2dmw%253D%26xhsshare%3DCopyLink")
XHS_SEC_WALL = ("https://www.xiaohongshu.com/404?source=/404/sec_MtwTvTbS?redirectPath=https%3A%2F%2Fwww.xiaohongshu.com%2Fdiscovery%2Fitem%2F"
                "64805ffc000000001303574f%3Fapp_platform%3Dios%26xsec_source%3Dapp_share%26xsec_token%3DCBO8AkBH8BftrCFvjmyHvU0G2da6x1nI0a5jR2k6uWWc8%253D")
XHS_SEC_300031 = (f"https://www.xiaohongshu.com/404?source=/404/sec_OAWUWluk?redirectPath=https://www.xiaohongshu.com/explore/"
                  f"{NOTE}?xsec_token=CB=&xsec_source=app_share&error_code=300031"
                  "&error_msg=%E5%BD%93%E5%89%8D%E7%AC%94%E8%AE%B0%E6%9A%82%E6%97%B6%E6%97%A0%E6%B3%95%E6%B5%8F%E8%A7%88&uuid=x&verifyMsg=")
# The live 2026-11-21 Location (rednote.com bounced the ReadProof UA to its login page, the note inside).
XHS_REDNOTE_LOGIN_LIVE = f"https://www.rednote.com/login?redirectPath=http%3A%2F%2Fwww.rednote.com%2Fexplore%2F{NOTE}%3FexSource%3Dnull"

# (url, platform, post_id, form, expected extra subset)
PARSE = [
    # douyin
    (f"https://www.douyin.com/video/{DY}", "douyin", DY, "desktop", {"kind": "video"}),
    (f"https://www.douyin.com/note/{DY}?x=1", "douyin", DY, "desktop", {"kind": "note"}),
    (f"https://www.douyin.com/slides/{DY}", "douyin", DY, "desktop", {"kind": "note"}),
    (f"https://www.iesdouyin.com/share/video/{DY}/?region=CN&mid=7664&u_code=abc&did=1&iid=1&with_sec_did=1",
     "douyin", DY, "share_page", {"kind": "video"}),
    ("https://www.iesdouyin.com/share/note/7680883233300000000/?from=web", "douyin", "7680883233300000000", "share_page", {"kind": "note"}),
    ("https://www.douyin.com/user/MS4wLjABAAAA0hrPqt3M7MUOZ9eo55AdMwLA81kkobjkG7lExwmze8aW9n5563rMZQ4DAfMREcvE"
     "?from_tab_name=main&modal_id=7683878394538364194&vid=7683878394538364194", "douyin", "7683878394538364194", "modal", {"via": "modal_id"}),
    ("https://www.douyin.com/discover?modal_id=7683878394538364194", "douyin", "7683878394538364194", "modal", {}),
    ("https://www.douyin.com/jingxuan?modal_id=7685253896516363626", "douyin", "7685253896516363626", "modal", {}),
    ("https://www.douyin.com/?vid=7683878394538364194", "douyin", "7683878394538364194", "modal", {"via": "vid"}),
    (f"https://open.douyin.com/player/video?vid={DY}&autoplay=0", "douyin", DY, "embed", {}),
    # xiaohongshu
    (f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=ABx+y/LLc=&xsec_source=pc_feed", "xiaohongshu", NOTE, "explore",
     {"xsec_token": "ABx+y/LLc=", "xsec_source": "pc_feed", "site": "xiaohongshu"}),
    ("https://www.xiaohongshu.com/explore/6a6bf662000000002402de7e?xsec_token=ABxLLc%3D", "xiaohongshu",
     "6a6bf662000000002402de7e", "explore", {"xsec_token": "ABxLLc="}),
    ("https://www.xiaohongshu.com/discovery/item/6a6bf662000000002402de7e?app_platform=ios&app_version=8.60&share_from_user_hidden=true"
     "&xsec_source=app_share&type=normal&xsec_token=CBabc%3D&author_share=1&xhsshare=CopyLink&shareRedId=x&apptime=1&share_id=y",
     "xiaohongshu", "6a6bf662000000002402de7e", "discovery_item", {"xsec_token": "CBabc=", "xsec_source": "app_share"}),
    ("https://www.xiaohongshu.com/search_result/699d64130000000015039f95?xsec_token=ABnfcGUHvDrBYsYPUQGELsaIY2NRJTQgilpD1WyK3txF0="
     "&xsec_source=pc_search&exSource=null", "xiaohongshu", "699d64130000000015039f95", "search_result",
     {"xsec_token": "ABnfcGUHvDrBYsYPUQGELsaIY2NRJTQgilpD1WyK3txF0="}),
    (f"https://www.xiaohongshu.com/user/profile/5b691fbe910cf67d445794f6/{NOTE}", "xiaohongshu", NOTE, "token_stripped",
     {"path_kind": "profile_note"}),
    ("https://www.rednote.com/discovery/item/6aa3bdd50000000019027723?xsec_token=AB=&xsec_source=pc_feed", "xiaohongshu",
     "6aa3bdd50000000019027723", "rednote_discovery_item", {"site": "rednote", "xsec_token": "AB="}),
    (f"https://www.rednote.com/explore/{NOTE}", "xiaohongshu", NOTE, "token_stripped", {"site": "rednote"}),
    (f"https://www.xiaohongshu.com/explore/{NOTE}", "xiaohongshu", NOTE, "token_stripped", {"site": "xiaohongshu"}),
    (XHS_LOGIN, "xiaohongshu", "6aa180590000000028000dcc", "login_wrapped",
     {"xsec_token": "CBFIPHMRiTVCtDNCWr5D2D-uDjQR-wN4FeadK6hRZ6dFo="}),
    (XHS_REDNOTE_LOGIN, "xiaohongshu", "5b691fbe910cf67d445794f6", "login_wrapped",
     {"xsec_token": "CBmXyt5EYQBBLE7pF9c1Yx4Ern5QgC_tAup5zIEcO2dmw=", "site": "rednote"}),
    (XHS_REDNOTE_LOGIN_LIVE, "xiaohongshu", NOTE, "login_wrapped", {"site": "rednote"}),
    (XHS_SEC_WALL, "xiaohongshu", "64805ffc000000001303574f", "sec_404_wrapped",
     {"xsec_token": "CBO8AkBH8BftrCFvjmyHvU0G2da6x1nI0a5jR2k6uWWc8="}),
    (XHS_SEC_300031, "xiaohongshu", NOTE, "sec_404_wrapped", {"xsec_token": "CB=", "error_code": "300031"}),
    # bilibili
    (f"https://www.bilibili.com/video/{BV}/?p=2&share_source=copy_web&vd_source=abc", "bilibili", BV, "part", {"part": 2}),
    (f"https://m.bilibili.com/video/{BV}?p=17", "bilibili", BV, "mobile", {"part": 17}),
    (f"https://www.bilibili.com/video/{BV}/", "bilibili", BV, "desktop", {"part": 1, "aid": 94519857}),
    (f"https://www.bilibili.com/video/{BV}?p=0", "bilibili", BV, "desktop", {"part": 1}),
    (f"https://www.bilibili.com/video/{BV}?p=abc", "bilibili", BV, "desktop", {"part": 1}),
    ("https://www.bilibili.com/video/av117614726617753", "bilibili", "BV1wRsV6gEeF", "av", {"aid": 117614726617753}),
    ("https://m.bilibili.com/video/av170001?p=2", "bilibili", "BV17x411w7KC", "av", {"part": 2}),
    (f"https://www.bilibili.com/zh-Hans/video/{BV}/", "bilibili", BV, "lang_prefix", {"prefix": "/zh-Hans"}),
    (f"https://www.bilibili.com/s/video/{BV}", "bilibili", BV, "lang_prefix", {"prefix": "/s"}),
    (f"https://www.bilibili.com/list/watchlater?bvid={BV}&oid=94519857", "bilibili", BV, "list_bvid", {}),
    (f"https://player.bilibili.com/player.html?bvid={BV}&p=2&autoplay=0", "bilibili", BV, "embed", {"part": 2}),
    ("https://player.bilibili.com/player.html?aid=170001", "bilibili", "BV17x411w7KC", "embed", {}),
    # youtube
    (f"https://www.youtube.com/watch?v={YT}&list=PL1&index=2", "youtube", YT, "desktop", {"list": "PL1"}),
    (f"https://m.youtube.com/watch?v={YT}&feature=share", "youtube", YT, "mobile", {}),
    (f"https://youtu.be/{YT}", "youtube", YT, "youtu_be", {}),
    (f"https://youtu.be/{YT}?si=Ab1Cd2Ef3Gh4Ij5K", "youtube", YT, "si", {"si": "Ab1Cd2Ef3Gh4Ij5K"}),
    ("https://www.youtube.com/shorts/aBcDeFgHiJk?feature=share", "youtube", "aBcDeFgHiJk", "shorts", {}),
    ("https://www.youtube.com/live/aBcDeFgHiJk?si=x", "youtube", "aBcDeFgHiJk", "live", {}),
    (f"https://www.youtube.com/embed/{YT}?start=10", "youtube", YT, "embed", {}),
    (f"https://www.youtube-nocookie.com/embed/{YT}", "youtube", YT, "embed", {}),
    (f"https://music.youtube.com/watch?v={YT}", "youtube", YT, "music", {}),
    (f"https://www.youtube.com/attribution_link?u=%2Fwatch%3Fv%3D{YT}%26feature%3Dshare", "youtube", YT, "desktop", {}),
    (f"https://www.youtube.com/watch?v={YT}&t=42s", "youtube", YT, "desktop", {"t": "42s"}),
    # tiktok
    (f"https://www.tiktok.com/@scout2015/video/{TT}", "tiktok", TT, "desktop", {"user": "scout2015", "kind": "video"}),
    (f"https://www.tiktok.com/@scout2015/video/{TT}?is_from_webapp=1&sender_device=pc", "tiktok", TT, "share_param", {"user": "scout2015"}),
    ("https://www.tiktok.com/@some.user_1/photo/7430000000000000000", "tiktok", "7430000000000000000", "photo",
     {"user": "some.user_1", "kind": "photo"}),
    (f"https://m.tiktok.com/v/{TT}.html", "tiktok", TT, "mobile", {}),
    (f"https://www.tiktok.com/embed/v2/{TT}", "tiktok", TT, "embed", {}),
    (f"https://www.tiktok.com/player/v1/{TT}", "tiktok", TT, "embed", {}),
    # weibo
    (f"https://weibo.com/1642904381/{BID}", "weibo", BID, "desktop", {"mid": MID, "uid": "1642904381"}),
    (f"https://weibo.com/1642904381/{BID}?refer_flag=1001030103_", "weibo", BID, "desktop", {"mid": MID}),
    (f"https://weibo.com/1642904381/{MID}", "weibo", BID, "desktop", {"mid": MID}),
    (f"https://m.weibo.cn/detail/{MID}", "weibo", BID, "mobile_detail", {"mid": MID}),
    (f"https://m.weibo.cn/status/{BID}", "weibo", BID, "mobile_status", {"mid": MID}),
    (f"https://weibo.com/status/{BID}", "weibo", BID, "desktop_status", {"mid": MID}),
    ("https://weibo.com/detail/5351093540225587", "weibo", "RlraW0WGv", "desktop_detail", {"mid": "5351093540225587"}),
    ("https://weibo.com/?layerid=5365031959003209", "weibo", "RrhMfBM93", "layer", {"mid": "5365031959003209"}),
    ("https://video.weibo.com/show?fid=1034:5365030863175693", "weibo", "video:1034:5365030863175693", "video_page",
     {"oid": "1034:5365030863175693"}),
    ("https://h5.video.weibo.com/show/1034:5365030863175693", "weibo", "video:1034:5365030863175693", "video_page", {}),
    ("https://weibo.com/tv/show/1034:5365030863175693", "weibo", "video:1034:5365030863175693", "video_page", {}),
    ("https://m.weibo.cn/s/video/show?object_id=1034%3A5365030863175693", "weibo", "video:1034:5365030863175693", "video_page", {}),
    # toutiao
    (f"https://m.toutiao.com/group/{TOUTIAO}/", "toutiao", TOUTIAO, "group", {"kind": "article"}),
    (f"https://m.toutiao.com/group/{TOUTIAO}/?upstream_biz=toutiao_pc&share_token=abc", "toutiao", TOUTIAO, "group", {}),
    (f"https://www.toutiao.com/item/{TOUTIAO}/", "toutiao", TOUTIAO, "item", {}),
    (f"https://www.toutiao.com/article/{TOUTIAO}/", "toutiao", TOUTIAO, "article", {}),
    (f"https://www.toutiao.com/video/{TOUTIAO}/", "toutiao", TOUTIAO, "video", {"kind": "video"}),
    (f"https://www.toutiao.com/w/{TOUTIAO}/", "toutiao", TOUTIAO, "weitoutiao", {"kind": "weitoutiao"}),
    (f"https://www.toutiao.com/a{TOUTIAO}/", "toutiao", TOUTIAO, "a_path", {}),
    (f"https://www.toutiao.com/i{TOUTIAO}/", "toutiao", TOUTIAO, "i_path", {}),
    (f"https://m.toutiao.com/i{TOUTIAO}/", "toutiao", TOUTIAO, "mobile_i", {}),
    (f"https://m.toutiao.com/?group_id={TOUTIAO}", "toutiao", TOUTIAO, "query_id", {}),
    # x
    ("https://x.com/jack/status/20", "x", "20", "x", {"user": "jack"}),
    ("https://x.com/jack/status/20?s=20", "x", "20", "share_param", {"user": "jack"}),
    ("https://twitter.com/jack/status/20", "x", "20", "twitter", {"user": "jack"}),
    ("https://mobile.twitter.com/jack/status/20", "x", "20", "mobile", {"user": "jack"}),
    ("https://x.com/someone/status/1234567890123456789/photo/1", "x", "1234567890123456789", "x", {"user": "someone"}),
    ("https://twitter.com/someone/statuses/1234567890123456789", "x", "1234567890123456789", "twitter", {}),
    ("https://x.com/i/web/status/1234567890123456789", "x", "1234567890123456789", "web_status", {}),
    ("https://platform.twitter.com/embed/Tweet.html?id=1234567890123456789", "x", "1234567890123456789", "embed", {}),
    # wechat
    ("https://mp.weixin.qq.com/s/AbCdEfGhIjKlMnOpQrStUv", "wechat", "AbCdEfGhIjKlMnOpQrStUv", "s_path", {}),
    ("https://mp.weixin.qq.com/s?__biz=MzA3MDU4NTYzMw==&mid=2650000001&idx=1&sn=abcdef0123456789&chksm=8a0b&scene=21#wechat_redirect",
     "wechat", "MzA3MDU4NTYzMw==:2650000001:1", "long_query", {"sn": "abcdef0123456789"}),
    ("https://mp.weixin.qq.com/s?__biz=MzA3MDU4NTYzMw%3D%3D&mid=2650000001&idx=1&sn=abcdef0123456789",
     "wechat", "MzA3MDU4NTYzMw==:2650000001:1", "long_query", {}),
    ("https://mp.weixin.qq.com/mp/appmsg/show?__biz=MzA3MDU4NTYzMw==&appmsgid=10000001&itemidx=1&sign=abc",
     "wechat", "MzA3MDU4NTYzMw==:10000001:1", "long_query", {}),
    # linkedin (parsed so the gate's refusal names the post; never fetched)
    ("https://www.linkedin.com/posts/someone_title-activity-7260000000000000000-AbCd", "linkedin", "7260000000000000000", "post", {}),
    ("https://www.linkedin.com/feed/update/urn:li:activity:7260000000000000000/", "linkedin", "7260000000000000000", "activity", {}),
    ("https://www.linkedin.com/feed/update/urn%3Ali%3Ashare%3A7260000000000000001", "linkedin", "7260000000000000001", "activity", {}),
    ("https://www.linkedin.com/pulse/some-article-title-someone/", "linkedin", "pulse:some-article-title-someone", "pulse", {}),
]


@pytest.mark.parametrize("url,platform,post_id,form,extra", PARSE, ids=[f"{p[1]}-{p[3]}-{i}" for i, p in enumerate(PARSE)])
def test_parse(url, platform, post_id, form, extra):
    spec = for_url(url)
    assert spec is not None and spec.name == platform
    ref = spec.parse(url)
    assert ref is not None, url
    assert (ref.platform, ref.post_id, ref.form) == (platform, post_id, form)
    assert ref.url
    for key, value in extra.items():
        assert ref.extra.get(key) == value, (key, ref.extra)


NOT_POSTS = [
    ("https://v.douyin.com/VeXgdE_QwTE/", "douyin", None),  # a short link: its id is behind the redirect
    ("https://www.douyin.com/user/MS4wLjABAAAA0hrPqt3M7MUOZ9eo55AdMwLA81kkobjkG7lExwmze8aW9n5563rMZQ4DAfMREcvE", "douyin", "profile"),
    ("https://www.iesdouyin.com/share/user/MS4wLjABAAAAKKX6BLaLg3TTzsvjbh8R63NnPawIQp9nirYg9uYJb_w?iid=x", "douyin", "profile"),
    ("https://www.douyin.com/?modal_id=12", "douyin", "home"),
    ("https://www.douyin.com/search/agents", "douyin", "search"),
    ("https://www.douyin.com/article/Tt44KdHVDPRa9", "douyin", "article"),
    ("https://www.douyin.com/shipin/7380000000000000000", "douyin", "topic"),
    ("https://www.iesdouyin.com/share/music/7300000000000000000/", "douyin", "music"),
    ("https://live.douyin.com/123456789", "douyin", "live"),
    ("https://www.douyin.com", "douyin", "home"),
    ("https://xhslink.com/o/1WiQ1QI6Uc0", "xiaohongshu", None),
    ("https://xhslink.cn/o/AxnRePgIokn", "xiaohongshu", None),
    ("https://www.xiaohongshu.com/explore", "xiaohongshu", "listing"),
    ("https://www.xiaohongshu.com/explore?exSource=null", "xiaohongshu", "listing"),
    ("https://www.xiaohongshu.com/explore/test", "xiaohongshu", None),
    ("https://www.xiaohongshu.com/user/profile/5b691fbe910cf67d445794f6", "xiaohongshu", "profile"),
    ("https://www.xiaohongshu.com/login?redirectPath=https%3A%2F%2Fevil.example%2F", "xiaohongshu", "login"),
    ("https://www.xiaohongshu.com/login", "xiaohongshu", "login"),
    ("https://www.rednote.com/login?redirectPath=http://www.rednote.com/explore?exSource=null", "xiaohongshu", "listing"),
    ("https://b23.tv/Oieigam", "bilibili", None),
    ("https://www.bilibili.com/video/", "bilibili", "video_without_id"),
    ("https://space.bilibili.com/123456", "bilibili", "profile"),
    ("https://www.bilibili.com/bangumi/play/ep123", "bilibili", "bangumi"),
    ("https://www.youtube.com/@somechannel", "youtube", "profile"),
    ("https://www.youtube.com/watch?v=short", "youtube", None),
    ("https://www.youtube.com/results?search_query=x", "youtube", "search"),
    ("https://vm.tiktok.com/ZMabcdef/", "tiktok", None),
    ("https://vt.tiktok.com/ZSabc123/", "tiktok", None),
    ("https://www.tiktok.com/t/ZTRabc123/", "tiktok", None),
    ("https://www.tiktok.com/@scout2015", "tiktok", "profile"),
    ("https://www.tiktok.com/view/product/1729000000000000000", "tiktok", "product"),
    ("https://weibo.com/u/1642904381", "weibo", "profile"),
    ("https://weibo.com/1642904381", "weibo", "profile"),
    ("https://weibo.com/", "weibo", "home"),
    ("https://t.cn/A6abcdEF", "weibo", None),
    ("https://www.toutiao.com/", "toutiao", "home"),
    ("https://www.toutiao.com/c/user/token/MS4wLjABAAAA/", "toutiao", "profile"),
    ("https://www.toutiao.com/article/123/", "toutiao", None),
    ("https://m.toutiao.com/is/iABcd12x/", "toutiao", None),
    ("https://t.co/AbCdEf1234", "x", None),
    ("https://x.com/jack", "x", "profile"),
    ("https://x.com/home", "x", "home"),
    ("https://x.com/i/flow/login", "x", "login"),
    ("https://mp.weixin.qq.com/", "wechat", "home"),
    ("https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz=MzA3MDU4NTYzMw==", "wechat", "profile"),
    ("https://www.linkedin.com/in/someone/", "linkedin", "profile"),
]


@pytest.mark.parametrize("url,platform,kind", NOT_POSTS)
def test_what_names_no_post_is_not_parsed_as_one(url, platform, kind):
    spec = for_url(url)
    assert spec is not None and spec.name == platform
    assert spec.parse(url) is None
    if kind is not None:
        assert spec.page_kind(url) == kind


SHORT_LINKS = [
    "https://v.douyin.com/VeXgdE_QwTE/", "https://xhslink.com/a/AbCdEf", "https://xhslink.cn/o/AxnRePgIokn",
    "https://b23.tv/Oieigam", "https://vm.tiktok.com/ZMabcdef/", "https://vt.tiktok.com/ZSabc123/",
    "https://www.tiktok.com/t/ZTRabc123/", "https://t.co/AbCdEf1234", "https://m.toutiao.com/is/iABcd12x/", "https://t.cn/A6abcdEF",
]


@pytest.mark.parametrize("url", SHORT_LINKS)
def test_short_links_are_short(url):
    spec = for_url(url)
    assert spec.is_short(url) and spec.parse(url) is None


@pytest.mark.parametrize("url", [f"https://youtu.be/{YT}", f"https://www.douyin.com/video/{DY}", "https://x.com/jack/status/20"])
def test_post_addresses_are_not_short(url):
    assert not for_url(url).is_short(url)


# --- canonical ---------------------------------------------------------------------------------------------

CANONICAL = [
    (f"https://www.iesdouyin.com/share/video/{DY}/?region=CN&mid=1", f"https://www.douyin.com/video/{DY}"),
    (f"https://www.iesdouyin.com/share/note/{DY}/", f"https://www.douyin.com/note/{DY}"),
    ("https://www.douyin.com/discover?modal_id=7683878394538364194", "https://www.douyin.com/video/7683878394538364194"),
    ("https://www.xiaohongshu.com/discovery/item/6a6bf662000000002402de7e?xsec_source=app_share&xsec_token=CBabc%3D&share_id=y",
     "https://www.xiaohongshu.com/explore/6a6bf662000000002402de7e?xsec_token=CBabc=&xsec_source=pc_share"),
    (f"https://www.rednote.com/explore/{NOTE}", f"https://www.xiaohongshu.com/explore/{NOTE}"),
    (XHS_LOGIN, "https://www.xiaohongshu.com/explore/6aa180590000000028000dcc?xsec_token=CBFIPHMRiTVCtDNCWr5D2D-uDjQR-wN4FeadK6hRZ6dFo=&xsec_source=pc_share"),
    (f"https://www.bilibili.com/zh-Hans/video/{BV}/?spm_id_from=333.1387", f"https://www.bilibili.com/video/{BV}/"),
    (f"https://m.bilibili.com/video/{BV}?p=2&share_source=copy", f"https://www.bilibili.com/video/{BV}/?p=2"),
    ("https://www.bilibili.com/video/av170001", "https://www.bilibili.com/video/BV17x411w7KC/"),
    (f"https://youtu.be/{YT}?si=x&t=3", f"https://www.youtube.com/watch?v={YT}"),
    (f"https://www.youtube.com/shorts/{YT}", f"https://www.youtube.com/watch?v={YT}"),
    (f"https://www.tiktok.com/@scout2015/video/{TT}?is_from_webapp=1", f"https://www.tiktok.com/@scout2015/video/{TT}"),
    (f"https://m.tiktok.com/v/{TT}.html", f"https://m.tiktok.com/v/{TT}.html"),
    (f"https://weibo.com/1642904381/{BID}", f"https://m.weibo.cn/detail/{MID}"),
    (f"https://m.weibo.cn/status/{BID}", f"https://m.weibo.cn/detail/{MID}"),
    ("https://weibo.com/tv/show/1034:5365030863175693", "https://h5.video.weibo.com/show/1034:5365030863175693"),
    (f"https://m.toutiao.com/group/{TOUTIAO}/?share_token=x", f"https://www.toutiao.com/article/{TOUTIAO}/"),
    (f"https://www.toutiao.com/item/{TOUTIAO}/", f"https://www.toutiao.com/article/{TOUTIAO}/"),
    (f"https://www.toutiao.com/w/{TOUTIAO}/", f"https://www.toutiao.com/article/{TOUTIAO}/"),
    ("https://mobile.twitter.com/jack/status/20?s=20", "https://x.com/jack/status/20"),
    ("https://x.com/i/web/status/20", "https://x.com/i/web/status/20"),
    ("https://mp.weixin.qq.com/s?__biz=MzA3MDU4NTYzMw==&mid=2650000001&idx=1&sn=abc&chksm=x&scene=21#wechat_redirect",
     "https://mp.weixin.qq.com/s?__biz=MzA3MDU4NTYzMw==&mid=2650000001&idx=1&sn=abc"),
    ("https://www.linkedin.com/posts/someone_title-activity-7260000000000000000-AbCd",
     "https://www.linkedin.com/feed/update/urn:li:activity:7260000000000000000/"),
    ("https://Example.com:443/a/b/?utm_source=x&b=2&a=1#frag", "https://example.com/a/b?a=1&b=2"),
]


@pytest.mark.parametrize("url,expected", CANONICAL)
def test_canonical(url, expected):
    spec = for_url(url)
    assert spec.canonical(spec.parse(url)) == expected


def test_toutiao_canonical_never_needs_a_robots_disallowed_hop():
    """robots.txt (2026-11-21) disallows /group/ and /item/; every form is read at /article/<id>/."""
    spec = by_name("toutiao")
    for url, platform, *_ in PARSE:
        if platform == "toutiao":
            canonical = spec.canonical(spec.parse(url))
            assert canonical == f"https://www.toutiao.com/article/{TOUTIAO}/"
            assert "/group/" not in canonical and "/item/" not in canonical


def test_bilibili_canonical_never_carries_a_language_segment():
    """robots.txt (2026-11-21) disallows /zh-Hant/, /en/, /ja/ … for every agent."""
    spec = by_name("bilibili")
    for prefix in ("/en", "/zh-Hant", "/ja", "/zh-Hans", "/s"):
        url = f"https://www.bilibili.com{prefix}/video/{BV}/"
        assert spec.canonical(spec.parse(url)) == f"https://www.bilibili.com/video/{BV}/"


# --- forms -------------------------------------------------------------------------------------------------

FORMS = [
    (f"https://www.douyin.com/video/{DY}", {"short": "https://v.douyin.com/VeXgdE_QwTE/", "author": "某某", "title": "标题"}),
    (f"https://www.douyin.com/video/{DY}", {}),
    (f"https://www.xiaohongshu.com/discovery/item/{NOTE}?xsec_token=CBFIPHMRiTVCtDNCWr5D2D-uDjQR-wN4FeadK6hRZ6dFo=&xsec_source=app_share",
     {"short": "http://xhslink.com/a/AbCdEfGh", "title": "标题", "author": "作者"}),
    (f"https://www.xiaohongshu.com/explore/{NOTE}", {}),
    (f"https://www.bilibili.com/video/{BV}/?p=2", {"short": "https://b23.tv/Oieigam", "title": "李宏毅机器学习"}),
    (f"https://www.bilibili.com/video/{BV}/", {}),
    (f"https://www.youtube.com/watch?v={YT}", {"short": f"https://youtu.be/{YT}?si=Ab1Cd2Ef3Gh4Ij5K", "title": "A video"}),
    (f"https://www.youtube.com/watch?v={YT}", {}),
    (f"https://www.tiktok.com/@scout2015/video/{TT}", {"short": "https://vm.tiktok.com/ZMabcdef/"}),
    (f"https://m.tiktok.com/v/{TT}.html", {}),
    (f"https://weibo.com/1642904381/{BID}", {"short": "https://t.cn/A6abcdEF", "author": "博主"}),
    ("https://video.weibo.com/show?fid=1034:5365030863175693", {}),
    (f"https://m.toutiao.com/group/{TOUTIAO}/", {"short": "https://m.toutiao.com/is/iABcd12x/", "title": "标题"}),
    (f"https://www.toutiao.com/video/{TOUTIAO}/", {}),
    ("https://x.com/jack/status/20", {"short": "https://t.co/AbCdEf1234"}),
    ("https://x.com/i/web/status/20", {}),
    ("https://mp.weixin.qq.com/s/AbCdEfGhIjKlMnOpQrStUv", {}),
    ("https://mp.weixin.qq.com/s?__biz=MzA3MDU4NTYzMw==&mid=2650000001&idx=1&sn=abc", {}),
    ("https://www.linkedin.com/feed/update/urn:li:activity:7260000000000000000/", {}),
    ("https://example.com/a?utm_source=x", {"title": "A page"}),
]


@pytest.mark.parametrize("url,known", FORMS, ids=[f"{for_url(u).name}-{bool(k.get('short'))}" for u, k in FORMS])
def test_forms_all_name_the_same_post(url, known):
    spec = for_url(url)
    ref = spec.parse(url)
    forms = spec.forms(ref, **known)
    names = [name for name, _ in forms]
    assert len(names) == len(set(names)), names
    assert forms, "every post has at least one form"
    for name, value in forms:
        if name in ("short", "short_case_flipped"):
            other = for_url(value)
            assert other is not None and (other.is_short(value) or other.name == "youtube"), (name, value)
            continue
        target = extract_url(value) if name == "share_text" else value
        assert target, (name, value)
        other = for_url(target)
        got = other.parse(target) if not other.is_short(target) else None
        if got is None:  # the share text carries the short link
            assert target == known.get("short"), (name, value)
            continue
        assert (got.platform, got.post_id) == (ref.platform, ref.post_id), (name, value)
    if spec.name != "linkedin":
        assert "share_text" in names


@pytest.mark.parametrize("url,known", [f for f in FORMS if f[1].get("short")])
def test_the_short_link_and_its_case_flipped_control(url, known):
    spec = for_url(url)
    forms = dict(spec.forms(spec.parse(url), **known))
    short, flipped = forms["short"], forms["short_case_flipped"]
    assert short == known["short"]
    assert len(short) == len(flipped) and short != flipped
    diffs = [(a, b) for a, b in zip(short, flipped) if a != b]
    assert len(diffs) == 1 and diffs[0][0].swapcase() == diffs[0][1], diffs
    assert known["short"] in forms["share_text"]


@pytest.mark.parametrize("url,known", [f for f in FORMS if not f[1].get("short")])
def test_no_short_link_is_ever_invented(url, known):
    spec = for_url(url)
    forms = spec.forms(spec.parse(url), **known)
    names = {name for name, _ in forms}
    assert not names & {"short", "short_case_flipped"}
    shortener_hosts = {h for s in REGISTRY for h in getattr(s, "short_hosts", ())}
    for _, value in forms:
        assert not any(f"//{h}/" in value for h in shortener_hosts), value


def test_share_texts_are_in_each_apps_copy_link_style():
    dy = dict(by_name("douyin").forms(by_name("douyin").parse(f"https://www.douyin.com/video/{DY}"),
                                      short="https://v.douyin.com/VeXgdE_QwTE/", author="某某"))["share_text"]
    assert dy.startswith("7.43 复制打开抖音，看看【某某的作品】") and "https://v.douyin.com/VeXgdE_QwTE/ 复制此链接" in dy
    xhs_spec = by_name("xiaohongshu")
    xhs = dict(xhs_spec.forms(xhs_spec.parse(f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=AB="),
                              short="http://xhslink.com/a/AbCdEfGh", title="标题", author="作者"))["share_text"]
    assert xhs.startswith("【标题 - 作者 | 小红书") and xhs.endswith("http://xhslink.com/a/AbCdEfGh 复制本条信息，打开【小红书】App查看精彩内容！")
    bili_spec = by_name("bilibili")
    bili = dict(bili_spec.forms(bili_spec.parse(f"https://www.bilibili.com/video/{BV}/"), short="https://b23.tv/Oieigam",
                                title="标题"))["share_text"]
    assert bili == "【标题-哔哩哔哩】 https://b23.tv/Oieigam"
    for share_text, link in ((dy, "https://v.douyin.com/VeXgdE_QwTE/"), (xhs, "http://xhslink.com/a/AbCdEfGh"),
                             (bili, "https://b23.tv/Oieigam")):
        assert extract_url(share_text) == link


def test_xiaohongshu_token_forms():
    spec = by_name("xiaohongshu")
    token = "CBFIPHMRiTVCtDNCWr5D2D-uDjQR-wN4FeadK6hRZ6dFo="
    forms = dict(spec.forms(spec.parse(f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token={token}"), expired_token="CBold="))
    assert f"xsec_token={token}" in forms["explore"] and "%3D" not in forms["explore"], "the token is written literally"
    assert forms["token_stripped"] == f"https://www.xiaohongshu.com/explore/{NOTE}"
    altered = spec.parse(forms["token_altered"]).extra["xsec_token"]
    assert altered != token and len(altered) == len(token) and sum(a != b for a, b in zip(altered, token)) == 1
    assert forms["rednote_explore"].startswith("https://www.rednote.com/explore/")
    assert forms["token_expired"].endswith("xsec_token=CBold=&xsec_source=pc_share")
    wrapped = spec.parse(forms["login_wrapped"])
    assert wrapped.form == "login_wrapped" and wrapped.extra["xsec_token"] == token
    assert {"explore", "discovery_item", "rednote_explore", "rednote_discovery_item", "token_stripped", "token_altered",
            "login_wrapped", "share_text"} <= set(forms)


def test_xiaohongshu_literal_token_is_idempotent():
    url = f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=AB%3D&xsec_source=pc_share&q=a%20b"
    once = xiaohongshu.literal_token(url)
    assert once == f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=AB=&xsec_source=pc_share&q=a%20b"
    assert xiaohongshu.literal_token(once) == once


def test_xiaohongshu_unwrap_refuses_a_foreign_redirect_path():
    spec = by_name("xiaohongshu")
    assert spec.wrapped("https://www.xiaohongshu.com/login?redirectPath=https%3A%2F%2Fevil.example%2Fexplore%2F" + NOTE) is None
    assert spec.parse("https://www.rednote.com/login?redirectPath=https%3A%2F%2Fevil.example%2F") is None


def test_bilibili_forms_cover_bv_av_part_and_mobile():
    spec = by_name("bilibili")
    forms = dict(spec.forms(spec.parse(f"https://www.bilibili.com/video/{BV}/?p=2")))
    assert forms["desktop"] == f"https://www.bilibili.com/video/{BV}/?p=2"
    assert forms["mobile"] == f"https://m.bilibili.com/video/{BV}?p=2"
    assert forms["av"] == "https://www.bilibili.com/video/av94519857/?p=2"
    assert forms["part"] == f"https://www.bilibili.com/video/{BV}/?p=2"
    assert forms["share_param"] == f"https://www.bilibili.com/video/{BV}/?p=2&share_source=copy_web"
    one = dict(spec.forms(spec.parse(f"https://www.bilibili.com/video/{BV}/")))
    assert one["part"] == f"https://www.bilibili.com/video/{BV}/?p=1" and "?p=" not in one["desktop"]


def test_weibo_forms_cross_bid_and_mid():
    spec = by_name("weibo")
    forms = dict(spec.forms(spec.parse(f"https://m.weibo.cn/detail/{MID}"), uid="1642904381"))
    assert forms["desktop"] == f"https://weibo.com/1642904381/{BID}"
    assert forms["mobile_detail"] == f"https://m.weibo.cn/detail/{MID}"
    assert forms["mobile_status"] == f"https://m.weibo.cn/status/{BID}"


def test_youtube_si_form_only_with_the_share_buttons_value():
    spec = by_name("youtube")
    ref = spec.parse(f"https://www.youtube.com/watch?v={YT}")
    assert "si" not in dict(spec.forms(ref))
    assert dict(spec.forms(ref, si="Ab1Cd2Ef3Gh4Ij5K"))["si"] == f"https://youtu.be/{YT}?si=Ab1Cd2Ef3Gh4Ij5K"


def test_linkedin_has_one_form_and_no_reader_surface():
    spec = by_name("linkedin")
    ref = spec.parse("https://www.linkedin.com/posts/someone_title-activity-7260000000000000000-AbCd")
    assert spec.forms(ref) == [("desktop", "https://www.linkedin.com/feed/update/urn:li:activity:7260000000000000000/")]
    assert spec.embed_url(ref) is None and spec.oembed_url(ref.url) is None


# --- embed and oEmbed ----------------------------------------------------------------------------------------

EMBED = [
    (f"https://www.douyin.com/video/{DY}", f"https://open.douyin.com/player/video?vid={DY}&autoplay=0"),
    (f"https://www.bilibili.com/video/{BV}/", f"https://player.bilibili.com/player.html?bvid={BV}&autoplay=0"),
    (f"https://www.bilibili.com/video/{BV}/?p=3", f"https://player.bilibili.com/player.html?bvid={BV}&p=3&autoplay=0"),
    (f"https://youtu.be/{YT}", f"https://www.youtube-nocookie.com/embed/{YT}"),
    (f"https://www.tiktok.com/@scout2015/video/{TT}", f"https://www.tiktok.com/player/v1/{TT}?autoplay=1&muted=1"),
    ("https://x.com/jack/status/20", "https://platform.twitter.com/embed/Tweet.html?id=20"),
    (f"https://www.xiaohongshu.com/explore/{NOTE}", None),
    (f"https://m.weibo.cn/detail/{MID}", None),
    (f"https://www.toutiao.com/article/{TOUTIAO}/", None),
    ("https://mp.weixin.qq.com/s/AbCdEfGhIjKlMnOpQrStUv", None),
    ("https://example.com/a", None),
]


@pytest.mark.parametrize("url,expected", EMBED)
def test_embed_url(url, expected):
    spec = for_url(url)
    assert spec.embed_url(spec.parse(url)) == expected


OEMBED = [
    (f"https://youtu.be/{YT}?si=x", f"https://www.youtube.com/oembed?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3D{YT}&format=json"),
    (f"https://www.tiktok.com/@scout2015/video/{TT}?is_from_webapp=1",
     f"https://www.tiktok.com/oembed?url=https%3A%2F%2Fwww.tiktok.com%2F%40scout2015%2Fvideo%2F{TT}"),
    ("https://x.com/jack/status/20?s=20", "https://publish.twitter.com/oembed?url=https%3A%2F%2Ftwitter.com%2Fjack%2Fstatus%2F20"),
    (f"https://www.douyin.com/video/{DY}", None),
    (f"https://www.bilibili.com/video/{BV}/", None),
    (f"https://www.xiaohongshu.com/explore/{NOTE}", None),
    ("https://x.com/i/web/status/20", None),
]


@pytest.mark.parametrize("url,expected", OEMBED)
def test_oembed_url(url, expected):
    assert for_url(url).oembed_url(url) == expected


# --- registry ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("url,name", [
    ("https://www.douyin.com/video/1", "douyin"), ("https://v.douyin.com/abc/", "douyin"), ("https://www.iesdouyin.com/x", "douyin"),
    ("https://www.xiaohongshu.com/explore/x", "xiaohongshu"), ("https://www.rednote.com/x", "xiaohongshu"),
    ("http://xhslink.com/a/x", "xiaohongshu"), ("https://www.xhslink.cn/o/abc", "xiaohongshu"),
    ("https://b23.tv/x", "bilibili"), ("https://player.bilibili.com/player.html", "bilibili"),
    ("https://youtu.be/x", "youtube"), ("https://www.youtube-nocookie.com/embed/x", "youtube"),
    ("https://vm.tiktok.com/x/", "tiktok"), ("https://t.cn/x", "weibo"), ("https://m.weibo.cn/detail/1", "weibo"),
    ("https://m.toutiao.com/is/x/", "toutiao"), ("https://t.co/x", "x"), ("https://mobile.twitter.com/a/status/1", "x"),
    ("https://mp.weixin.qq.com/s/x", "wechat"), ("https://cn.linkedin.com/in/x", "linkedin"),
    ("https://example.cn/o/abc", "web"), ("https://notxhslink.cn/o/abc", "web"), ("https://fakedouyin.com/video/1", "web"),
    ("https://box.com/jack/status/20", "web"), ("https://lnkd.in/abc", "web"), ("https://weixin.qq.com/sph/abc", "web"),
])
def test_for_url_picks_the_owner(url, name):
    assert for_url(url).name == name


@pytest.mark.parametrize("value", ["ftp://example.com/x", "not a url", "", "mailto:a@b.c", "https://"])
def test_for_url_is_none_for_what_is_not_a_web_address(value):
    assert for_url(value) is None


def test_registry_order_and_names():
    names = [spec.name for spec in REGISTRY]
    assert names[-1] == "web" and len(names) == len(set(names))
    assert set(names) == {"douyin", "xiaohongshu", "bilibili", "youtube", "tiktok", "weibo", "toutiao", "x", "wechat",
                          "linkedin", "web"}
    for name in names:
        assert by_name(name).name == name
    with pytest.raises(KeyError):
        by_name("myspace")


@pytest.mark.parametrize("spec", REGISTRY, ids=[s.name for s in REGISTRY])
def test_every_spec_implements_the_protocol(spec):
    for method in ("match", "parse", "canonical", "forms", "embed_url", "oembed_url", "judge"):
        assert callable(getattr(spec, method)), method
    assert isinstance(spec.name, str) and spec.name


# --- id conversions ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("aid,bv", [(170001, "BV17x411w7KC"), (111298867365120, "BV1L9Uoa9EUx"), (117614726617753, "BV1wRsV6gEeF")])
def test_bilibili_av_and_bv_are_one_id(aid, bv):
    assert bilibili.av_to_bv(aid) == bv and bilibili.bv_to_av(bv) == aid


def test_bilibili_out_of_range_ids():
    assert bilibili.av_to_bv(0) is None and bilibili.av_to_bv(1 << 51) is None
    assert bilibili.bv_to_av("BV1000000000") is None  # '0' is not in Bilibili's alphabet


@pytest.mark.parametrize("mid,bid", [("5351093540225587", "RlraW0WGv"), ("5351369302084798", "RlylI8KlM")])
def test_weibo_mid_and_bid_are_one_id(mid, bid):
    """Both pairs were checked live by LinkDigest (2026-11-01); the padded group is the easy part to miss."""
    assert weibo.mid_to_bid(mid) == bid and weibo.bid_to_mid(bid) == mid


def test_web_post_id_is_the_sha1_of_the_normalised_url():
    import hashlib

    spec = by_name("web")
    a = spec.parse("https://Example.com/a/b/?utm_source=x&b=2&a=1#frag")
    b = spec.parse("https://example.com:443/a/b?a=1&b=2&fbclid=zz")
    assert a.post_id == b.post_id == hashlib.sha1(b"https://example.com/a/b?a=1&b=2").hexdigest()
    assert spec.parse("https://example.com/a/c").post_id != a.post_id
    assert normalise("http://example.com") == "http://example.com/"
    assert re.fullmatch(r"[0-9a-f]{40}", a.post_id)


def test_tokens_in_query_values_keep_their_base64_plus():
    spec = by_name("xiaohongshu")
    ref = spec.parse(f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=ABx+y/LLc=")
    assert ref.extra["xsec_token"] == "ABx+y/LLc=" and unquote(spec.canonical(ref)).count("+") == 1
