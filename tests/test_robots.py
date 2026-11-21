"""RFC 9309 parser/matcher and the per-origin cache (readproof/robots.py). Offline.

Real files in tests/fixtures/robots/ were fetched once on 2026-11-21 with the declared ReadProof UA
(3 requests, 3 s apart) and are kept byte-for-byte; their hashes are pinned below.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from readproof.robots import (
    OK,
    UNAVAILABLE,
    UNREACHABLE,
    RobotsCache,
    RobotsTxt,
    matches,
    normalize,
    product_token,
)
from readproof.testing import FakeNet, Scripted

FIXTURES = Path(__file__).parent / "fixtures" / "robots"
FIXTURE_SHA256 = {
    "weibo.com.txt": "15b0db632716dd8564dda06ed3db8ed48cd35722eb65ac87eed134f82e9eb85e",
    "www.tiktok.com.txt": "835705a2ecb862f49a813b3aa4061e0edfefbb8318316b371f8fa2dba4018802",
    "www.xiaohongshu.com.txt": "d6ddcb993e8d4875574fed42ffe1ae1982dc1465e7219417f6e2deabc9843885",
}

ME = "readproof"


def fixture(name: str) -> RobotsTxt:
    return RobotsTxt.parse_bytes((FIXTURES / name).read_bytes())


def allowed(rt: RobotsTxt, agent: str, path: str) -> bool:
    return rt.decide(agent, path)[0]


# --- RFC 9309 §5.1 "Simple Example" --------------------------------------------------------------------------

RFC_5_1 = """\
User-Agent: *
Disallow: *.gif$
Disallow: /example/
Allow: /publications/

User-Agent: foobot
Disallow:/
Allow:/example/page.html
Allow:/example/allowed.gif

User-Agent: barbot
User-Agent: bazbot
Disallow: /example/page.html

User-Agent: quxbot

EOF
"""


def test_rfc_5_1_star_group_applies_to_unnamed_agents():
    rt = RobotsTxt.parse(RFC_5_1)
    assert rt.decide(ME, "/publications/a") == (True, "*", "Allow: /publications/")
    assert rt.decide(ME, "/example/x") == (False, "*", "Disallow: /example/")
    assert rt.decide(ME, "/images/cat.gif") == (False, "*", "Disallow: *.gif$")
    assert allowed(rt, ME, "/images/cat.gif?size=2")  # "$" anchors the end: the query defeats it
    assert rt.decide(ME, "/other") == (True, "*", None)


def test_rfc_5_1_foobot_without_optional_spaces():
    rt = RobotsTxt.parse(RFC_5_1)
    assert rt.decide("foobot", "/example/page.html") == (True, "foobot", "Allow: /example/page.html")
    assert allowed(rt, "foobot", "/example/allowed.gif")
    assert rt.decide("foobot", "/example/other") == (False, "foobot", "Disallow: /")
    assert not allowed(rt, "foobot", "/publications/a")  # its own group, not "*"


def test_rfc_5_1_group_with_two_agents():
    rt = RobotsTxt.parse(RFC_5_1)
    for bot in ("barbot", "bazbot"):
        assert rt.decide(bot, "/example/page.html") == (False, bot, "Disallow: /example/page.html")
        assert allowed(rt, bot, "/example/other")
        assert allowed(rt, bot, "/images/cat.gif")


def test_rfc_5_1_empty_group_at_end_allows_everything():
    rt = RobotsTxt.parse(RFC_5_1)
    assert rt.decide("quxbot", "/example/x") == (True, "quxbot", None)  # does not fall back to "*"
    assert allowed(rt, "quxbot", "/images/cat.gif")


# --- RFC 9309 §5.2 "Longest Match" ---------------------------------------------------------------------------

def test_rfc_5_2_longest_match():
    rt = RobotsTxt.parse("User-Agent: foobot\nAllow: /example/page/\nDisallow: /example/page/disallowed.gif\n")
    assert rt.decide("foobot", "/example/page/disallowed.gif") == (False, "foobot",
                                                                   "Disallow: /example/page/disallowed.gif")
    assert rt.decide("foobot", "/example/page/other.gif") == (True, "foobot", "Allow: /example/page/")


# --- RFC 9309 §2.2.2 / §2.2.3 --------------------------------------------------------------------------------

def test_rfc_2_2_3_special_characters():
    rt = RobotsTxt.parse("User-agent: *\nDisallow: /\n"
                         "allow: / # comment in line\n"
                         "# comment on its own line\n")
    assert rt.decide(ME, "/x") == (True, "*", "Allow: /")  # equal length: allow wins, comment stripped
    rt = RobotsTxt.parse("User-agent: *\nDisallow: /\nallow: /this/path/exactly$\n")
    assert allowed(rt, ME, "/this/path/exactly")
    assert not allowed(rt, ME, "/this/path/exactly/more")  # "$" ends the pattern
    rt = RobotsTxt.parse("User-agent: *\nDisallow: /\nallow: /this/*/exactly\n")
    assert allowed(rt, ME, "/this/a/b/exactly")  # "*" spans any characters, "/" included
    assert allowed(rt, ME, "/this/x/exactly/more")  # no "$": a prefix match
    assert not allowed(rt, ME, "/this/exactly")  # the two slashes around "*" are literal


def test_rfc_2_2_3_percent_encoded_special_characters_match_literally():
    rt = RobotsTxt.parse("User-agent: *\nDisallow: /path/file-with-a-%2A.html\nDisallow: /path/foo-%24\n")
    assert not allowed(rt, ME, "/path/file-with-a-*.html")
    assert allowed(rt, ME, "/path/file-with-a-b.html")  # %2A is a literal "*", not a wildcard
    assert not allowed(rt, ME, "/path/foo-$")


@pytest.mark.parametrize("rule,path", [
    ("/foo/bar?baz=quz", "/foo/bar?baz=quz"),
    ("/foo/bar/ツ", "/foo/bar/%E3%83%84"),  # non-ASCII is UTF-8 percent-encoded
    ("/foo/bar/%E3%83%84", "/foo/bar/ツ"),
    ("/foo/bar/%e3%83%84", "/foo/bar/%E3%83%84"),  # hex case is not a difference
    ("/foo/bar/baz", "/foo/bar/%62%61%7A"),  # encoded unreserved octets are decoded
    ("/foo/bar/%62%61%7A", "/foo/bar/baz"),
    ("/foo/bar?baz=https%3A%2F%2Ffoo.bar", "/foo/bar?baz=https%3a%2f%2ffoo.bar"),  # reserved stay encoded
])
def test_rfc_2_2_2_percent_encoding_table(rule, path):
    rt = RobotsTxt.parse(f"User-agent: *\nDisallow: {rule}\n")
    assert not allowed(rt, ME, path)


def test_encoded_slash_is_not_a_slash():
    rt = RobotsTxt.parse("User-agent: *\nDisallow: /a/b\n")
    assert not allowed(rt, ME, "/a/b")
    assert allowed(rt, ME, "/a%2Fb")  # "/" is reserved: %2F must not be decoded


def test_robots_txt_itself_is_always_allowed():
    rt = RobotsTxt.parse("User-agent: *\nDisallow: /\n")
    assert rt.decide(ME, "/robots.txt") == (True, "*", "implicit allow: /robots.txt")
    assert not allowed(rt, ME, "/robots.txt.bak")


# --- group selection -----------------------------------------------------------------------------------------

def test_group_matching_is_case_insensitive_on_the_product_token():
    rt = RobotsTxt.parse("User-agent: ReadProof\nDisallow: /mine\n\nUser-agent: *\nDisallow: /\n")
    for ua in ("readproof", "READPROOF", "ReadProof/0.1.0 (+https://github.com/jcaiagent7143-ui/readproof)"):
        assert rt.decide(ua, "/x") == (True, "readproof", None)
        assert rt.decide(ua, "/mine/1") == (False, "readproof", "Disallow: /mine")
    rt = RobotsTxt.parse("User-agent: READPROOF/2.0\nDisallow: /\n")
    assert not allowed(rt, ME, "/x")  # the version after "/" is not part of the token


def test_an_unnamed_bot_obeys_star():
    rt = RobotsTxt.parse("User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nDisallow: /private\n")
    assert rt.decide(ME, "/public") == (True, "*", None)
    assert rt.decide(ME, "/private/1") == (False, "*", "Disallow: /private")
    assert rt.decide("gptbot", "/public") == (False, "gptbot", "Disallow: /")


def test_no_matching_group_and_no_star_allows_everything():
    rt = RobotsTxt.parse("User-agent: GPTBot\nDisallow: /\n")
    assert rt.decide(ME, "/anything") == (True, None, None)
    assert RobotsTxt.parse("").decide(ME, "/") == (True, None, None)
    assert RobotsTxt.parse("Disallow: /\n").decide(ME, "/x") == (True, None, None)  # rule outside any group


def test_groups_for_the_same_agent_are_combined():
    rt = RobotsTxt.parse("User-agent: readproof\nDisallow: /a\n\nUser-agent: *\nDisallow: /\n\n"
                         "User-agent: ReadProof\nDisallow: /b\n")
    assert not allowed(rt, ME, "/a/1")
    assert not allowed(rt, ME, "/b/1")
    assert allowed(rt, ME, "/c")


def test_star_groups_are_combined_too():
    rt = RobotsTxt.parse("User-agent: *\nDisallow: /a\n\nUser-agent: other\nDisallow: /\n\n"
                         "User-agent: *\nDisallow: /b\n")
    assert not allowed(rt, ME, "/a") and not allowed(rt, ME, "/b") and allowed(rt, ME, "/c")


def test_user_agent_lines_separated_by_other_records_share_a_group():
    rt = RobotsTxt.parse("User-agent: a\nContent-Signal: ai-train=no\nCrawl-delay: 5\n"
                         "User-agent: b\nDisallow: /\nUser-agent: c\nDisallow: /c\n")
    assert not allowed(rt, "a", "/x") and not allowed(rt, "b", "/x")
    assert allowed(rt, "c", "/x") and not allowed(rt, "c", "/c")  # a user-agent after a rule starts a group
    assert rt.signals_for("b") == {"ai-train": "no"}


# --- rules ---------------------------------------------------------------------------------------------------

def test_longest_match_wins_regardless_of_order():
    for text in ("User-agent: *\nAllow: /a/b\nDisallow: /a\n", "User-agent: *\nDisallow: /a\nAllow: /a/b\n"):
        rt = RobotsTxt.parse(text)
        assert rt.decide(ME, "/a/b/c") == (True, "*", "Allow: /a/b")
        assert rt.decide(ME, "/a/x") == (False, "*", "Disallow: /a")


def test_tie_goes_to_allow():
    rt = RobotsTxt.parse("User-agent: *\nDisallow: /page\nAllow: /page\n")
    assert rt.decide(ME, "/page") == (True, "*", "Allow: /page")
    rt = RobotsTxt.parse("User-agent: *\nDisallow: /*.htm\nAllow: /a*htm\n")  # both 6 octets, both match
    assert rt.decide(ME, "/a1.htm") == (True, "*", "Allow: /a*htm")


def test_dollar_anchor():
    rt = RobotsTxt.parse("User-agent: *\nDisallow: /*.php$\nDisallow: /exact$\n")
    assert not allowed(rt, ME, "/index.php")
    assert not allowed(rt, ME, "/a/b/c.php")
    assert allowed(rt, ME, "/index.php?x=1")
    assert allowed(rt, ME, "/index.php5")
    assert not allowed(rt, ME, "/exact")
    assert allowed(rt, ME, "/exactly")


def test_dollar_in_the_middle_is_literal():
    rt = RobotsTxt.parse("User-agent: *\nDisallow: /a$b\n")
    assert not allowed(rt, ME, "/a$b/c")
    assert allowed(rt, ME, "/a")


def test_star_wildcard():
    rt = RobotsTxt.parse("User-agent: *\nDisallow: /*/private/\nDisallow: */directory/\nDisallow: /search?\n")
    assert not allowed(rt, ME, "/u/1/private/x")
    assert allowed(rt, ME, "/private/x")  # "/*/" needs a segment before
    assert not allowed(rt, ME, "/shop/directory/")
    assert not allowed(rt, ME, "/search?q=a")
    assert allowed(rt, ME, "/search")
    assert allowed(rt, ME, "/searching")


def test_empty_disallow_allows():
    rt = RobotsTxt.parse("User-agent: *\nDisallow:\n")
    assert rt.decide(ME, "/anything") == (True, "*", None)
    rt = RobotsTxt.parse("User-agent: *\nDisallow: /x\nAllow:\n")
    assert not allowed(rt, ME, "/x")  # an empty Allow matches nothing either


def test_path_matching_is_case_sensitive():
    rt = RobotsTxt.parse("User-agent: *\nDisallow: /Private\n")
    assert not allowed(rt, ME, "/Private/1")
    assert allowed(rt, ME, "/private/1")


def test_decide_accepts_a_full_url_and_bare_paths():
    rt = RobotsTxt.parse("User-agent: *\nDisallow: /p\n")
    assert not allowed(rt, ME, "https://example.com/p/1?x=2")
    assert not allowed(rt, ME, "p/1")
    assert allowed(rt, ME, "")  # "" means "/": not under /p


def test_lenient_syntax():
    text = "﻿USER-AGENT: *\r\nDissallow: /typo\r\nDisallow /nocolon\r\nUser agent: spaced\r\nDisallow: /\r\n"
    rt = RobotsTxt.parse(text)
    assert not allowed(rt, ME, "/typo/1")
    assert not allowed(rt, ME, "/nocolon")
    assert not allowed(rt, "spaced", "/x")
    rt = RobotsTxt.parse("User-agent: *\nDisallow: private/\n")  # missing leading slash: honour the intent
    assert not allowed(rt, ME, "/private/x")


def test_hostile_pattern_is_fast():
    rt = RobotsTxt.parse("User-agent: *\nDisallow: /" + "*a" * 2000 + "b\n")
    t0 = time.monotonic()
    assert allowed(rt, ME, "/" + "a" * 5000)
    assert time.monotonic() - t0 < 2.0


def test_body_is_capped_at_500_kib():
    head = b"User-agent: *\nDisallow: /early\n"
    pad = b"# " + b"x" * 1000 + b"\n"
    body = head + pad * 520 + b"Disallow: /late\n"
    rt = RobotsTxt.parse_bytes(body)
    assert not allowed(rt, ME, "/early")
    assert allowed(rt, ME, "/late")  # past the 500 KiB parsing limit


def test_sitemaps_are_recorded():
    rt = RobotsTxt.parse("Sitemap: https://a.example/s.xml\nUser-agent: *\nDisallow:\nSitemap: https://a.example/t.xml\n")
    assert rt.sitemaps == ["https://a.example/s.xml", "https://a.example/t.xml"]


# --- helpers -------------------------------------------------------------------------------------------------

def test_product_token():
    assert product_token("ReadProof/0.1.0 (+https://github.com/jcaiagent7143-ui/readproof)") == "readproof"
    assert product_token("Sogou web spider") == "sogou"
    assert product_token("meta-externalagent") == "meta-externalagent"
    assert product_token("*") == ""


def test_normalize_and_matches():
    assert normalize("/a b/ツ/%7e/*/$", pattern=False) == "/a%20b/%E3%83%84/~/%2A/%24"
    assert normalize("/a*b$", pattern=True) == "/a*b$"
    assert matches("/abc", "/a") and matches("/abc", "/a*c$") and not matches("/abcd", "/a*c$")
    assert matches("/", "*") and matches("/x", "/*") and matches("", "")


# --- Content-Signal and Content-Usage ------------------------------------------------------------------------

def test_content_signal_cloudflare_format_in_the_star_group():
    rt = RobotsTxt.parse("User-Agent: *\nContent-Signal: search=yes,ai-train=no\nAllow: /\n")
    assert rt.content_signal == {"search": "yes", "ai-train": "no"}
    assert rt.signals_for(ME) == {"search": "yes", "ai-train": "no"}
    assert rt.global_content_signal == {}
    assert rt.group_content_signal == {"*": {"search": "yes", "ai-train": "no"}}


def test_content_signal_global_and_per_group():
    rt = RobotsTxt.parse("Content-Signal: search=yes, ai-input=no, ai-train=no\n\n"
                         "User-agent: ReadProof\nContent-Signal: ai-input=yes\nAllow: /\n\n"
                         "User-agent: *\nContent-Signal: ai-train=yes\nDisallow:\n")
    assert rt.global_content_signal == {"search": "yes", "ai-input": "no", "ai-train": "no"}
    assert rt.signals_for(ME) == {"search": "yes", "ai-input": "yes", "ai-train": "no"}  # group overrides global
    assert rt.signals_for("otherbot") == {"search": "yes", "ai-input": "no", "ai-train": "yes"}
    assert rt.content_signal == rt.signals_for("otherbot")
    assert rt.group_content_signal["readproof"] == {"ai-input": "yes"}


def test_content_signal_spacing_and_case():
    rt = RobotsTxt.parse("User-agent: *\ncontent-signal:  Search = YES , AI-Input=No\n")
    assert rt.content_signal == {"search": "yes", "ai-input": "no"}


def test_path_scoped_signals_apply_only_under_their_path():
    rt = RobotsTxt.parse("User-agent: *\nContent-Usage: /ai-ok/ train-ai=y\nContent-Usage: train-ai=n\n"
                         "Content-Signal: /news/ ai-input=no\nAllow: /\n")
    assert rt.signals_for(ME, "/ai-ok/x", kind="content-usage") == {"train-ai": "y"}  # longer path wins
    assert rt.signals_for(ME, "/other", kind="content-usage") == {"train-ai": "n"}
    assert rt.content_usage == {"train-ai": "n"}
    assert rt.signals_for(ME, "/news/1") == {"ai-input": "no"}
    assert rt.signals_for(ME, "/sport/1") == {}


def test_signal_lines_do_not_change_allow_disallow():
    a = RobotsTxt.parse("User-agent: *\nDisallow: /x\nAllow: /x/y\n")
    b = RobotsTxt.parse("User-agent: *\nContent-Signal: ai-input=no\nDisallow: /x\nContent-Usage: train-ai=n\n"
                        "Allow: /x/y\n")
    for p in ("/x", "/x/y", "/x/z", "/q"):
        assert a.decide(ME, p) == b.decide(ME, p)


# --- shaped like weibo.com: named AI agents get a path, "*" gets nothing ------------------------------------

WEIBO_SHAPED = """\
User-Agent: Baiduspider
User-Agent: Googlebot
Content-Signal: search=yes, ai-input=yes, ai-train=no
Disallow:

User-Agent: ChatGPT-User
User-Agent: DeepSeekBot
User-Agent: Perplexity-User
Content-Signal: search=yes, ai-input=yes, ai-train=no
Allow: /2/detail/
Allow: /ttarticle/p/show
Disallow: /

User-Agent: *
Content-Signal: search=yes, ai-input=yes, ai-train=no
Disallow: /
Allow: /ads.txt
"""


def test_weibo_shaped_file():
    rt = RobotsTxt.parse(WEIBO_SHAPED)
    assert rt.decide(ME, "/2/detail/4987654321") == (False, "*", "Disallow: /")
    assert rt.decide(ME, "/ads.txt") == (True, "*", "Allow: /ads.txt")
    assert rt.decide("ChatGPT-User", "/2/detail/4987654321") == (True, "chatgpt-user", "Allow: /2/detail/")
    assert rt.decide("chatgpt-user", "/1234567890/AbCdEf") == (False, "chatgpt-user", "Disallow: /")
    assert rt.decide("Googlebot", "/1234567890/AbCdEf") == (True, "googlebot", None)
    signals = {"search": "yes", "ai-input": "yes", "ai-train": "no"}
    assert rt.content_signal == signals and rt.signals_for(ME) == signals


# --- shaped like tiktok.com: named AI agents refused, "*" allowed ------------------------------------------

TIKTOK_SHAPED = """\
User-agent: GPTBot
User-agent: ClaudeBot
User-agent: PerplexityBot
User-agent: Bytespider
Disallow: /

User-agent: *
Allow: /foryou
Allow: /discover
Disallow: /embed/v2
Disallow: /search?
Disallow: /discover/trending/detail/
Disallow: */directory/
"""


def test_tiktok_shaped_file():
    rt = RobotsTxt.parse(TIKTOK_SHAPED)
    video = "/@scout2015/video/6718335390845095173"
    for bot in ("GPTBot", "claudebot", "PERPLEXITYBOT", "Bytespider"):
        assert rt.decide(bot, video) == (False, product_token(bot), "Disallow: /")
    assert rt.decide(ME, video) == (True, "*", None)
    assert rt.decide(ME, "/embed/v2/6718335390845095173") == (False, "*", "Disallow: /embed/v2")
    assert rt.decide(ME, "/discover/trending/detail/x") == (False, "*", "Disallow: /discover/trending/detail/")
    assert rt.decide(ME, "/discover/cats") == (True, "*", "Allow: /discover")
    assert not allowed(rt, ME, "/search?q=cats") and allowed(rt, ME, "/search")
    assert rt.content_signal == {}


# --- the real files ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(FIXTURE_SHA256))
def test_fixtures_are_the_files_fetched(name):
    assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == FIXTURE_SHA256[name]


def test_real_weibo_robots():
    rt = fixture("weibo.com.txt")
    assert rt.decide(ME, "/1234567890/AbCdEfGhI") == (False, "*", "Disallow: /")
    assert rt.decide(ME, "/2/detail/4987654321") == (False, "*", "Disallow: /")
    assert rt.decide(ME, "/ads.txt") == (True, "*", "Allow: /ads.txt")
    assert rt.decide("ChatGPT-User", "/2/detail/4987654321") == (True, "chatgpt-user", "Allow: /2/detail/")
    assert rt.decide("ChatGPT-User", "/u/1234567890") == (False, "chatgpt-user", "Disallow: /")
    assert allowed(rt, "DeepSeek", "/ttarticle/p/show?id=1")  # "User-Agent: DeepSeek Chat" -> token "deepseek"
    assert rt.decide("Googlebot", "/1234567890/AbCdEfGhI") == (True, "googlebot", None)  # empty Disallow
    assert rt.decide("Google-adstxt", "/app-ads.txt") == (True, "google-adstxt", "Allow: /app-ads.txt")
    assert rt.content_signal == {"search": "yes", "ai-input": "yes", "ai-train": "no"}
    assert rt.signals_for(ME, "/2/detail/1")["ai-input"] == "yes"
    assert rt.sitemaps == ["https://weibo.com/sitemap.xml"]


def test_real_tiktok_robots():
    rt = fixture("www.tiktok.com.txt")
    video = "/@scout2015/video/6718335390845095173"
    assert rt.decide(ME, video) == (True, "*", None)
    assert rt.decide(ME, "/embed/v2/6718335390845095173") == (False, "*", "Disallow: /embed/v2")
    assert rt.decide(ME, "/search?q=cats") == (False, "*", "Disallow: /search?")
    assert rt.decide(ME, "/discover/trending/detail/x") == (False, "*", "Disallow: /discover/trending/detail/")
    for bot in ("GPTBot", "ClaudeBot", "Claude-User", "ChatGPT-User", "Perplexity-User", "Bytespider"):
        assert rt.decide(bot, video) == (False, bot.lower(), "Disallow: /")
    assert rt.decide("Bingbot", "/discover") == (False, "bingbot", "Disallow: /discover")
    assert rt.content_signal == {}


def test_real_xiaohongshu_robots():
    rt = fixture("www.xiaohongshu.com.txt")
    for path in ("/explore/64f1a2b3000000001e03c4d5", "/discovery/item/64f1a2b3000000001e03c4d5", "/"):
        assert rt.decide(ME, path) == (False, "*", "Disallow: /")
    assert rt.decide(ME, "/robots.txt")[0]
    assert rt.decide("Baiduspider", "/") == (True, "baiduspider", "Allow: /$")
    assert rt.decide("Baiduspider", "/explore/64f1a2b3") == (True, "baiduspider", "Allow: /explore")
    assert rt.decide("Baiduspider", "/discovery/item/64f1") == (False, "baiduspider", "Disallow: /")
    assert rt.decide("Googlebot", "/worldcup26") == (True, "googlebot", "Allow: /worldcup26")
    assert rt.decide("Googlebot", "/explore/64f1a2b3") == (False, "googlebot", "Disallow: /")
    assert rt.sitemaps == ["https://www.xiaohongshu.com/sitemap.xml"]


# --- RobotsCache: status semantics (RFC 9309 §2.3.1), redirects, caching -----------------------------------

class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def cache_for(routes: dict, **kw) -> tuple[RobotsCache, FakeNet]:
    fake = FakeNet(routes)
    return RobotsCache(lambda u: fake.fetch(u, purpose="robots"), **kw), fake


def test_2xx_is_parsed():
    cache, _ = cache_for({"https://a.example/robots.txt": Scripted(200, "User-agent: *\nDisallow: /x\n")})
    rt, status = cache.for_url("https://a.example/x/1")
    assert status == OK and not rt.decide(ME, "/x/1")[0]


@pytest.mark.parametrize("code", [400, 401, 403, 404, 410, 451])
def test_4xx_is_unavailable(code):
    cache, _ = cache_for({"https://a.example/robots.txt": Scripted(code, "nope")})
    assert cache.for_url("https://a.example/x") == (None, UNAVAILABLE)


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_5xx_and_429_are_unreachable(code):
    cache, _ = cache_for({"https://a.example/robots.txt": Scripted(code, "busy")})
    assert cache.for_url("https://a.example/x") == (None, UNREACHABLE)


def test_network_error_is_unreachable():
    cache, _ = cache_for({"https://a.example/robots.txt": Scripted(error="ConnectError: timed out")})
    assert cache.for_url("https://a.example/x") == (None, UNREACHABLE)


def test_redirects_are_followed_up_to_five_even_across_hosts():
    # robots.txt -> r1 -> r2 -> r3 -> r4 -> another host: exactly five redirects
    routes = {f"https://a.example/r{i}": Scripted(301, "", {"Location": f"/r{i + 1}"}) for i in range(1, 4)}
    routes["https://a.example/robots.txt"] = Scripted(302, "", {"Location": "/r1"})
    routes["https://a.example/r4"] = Scripted(308, "", {"location": "https://cdn.example/robots-a.txt"})
    routes["https://cdn.example/robots-a.txt"] = Scripted(200, "User-agent: *\nDisallow: /x\n")
    cache, fake = cache_for(routes)
    rt, status = cache.for_url("https://a.example/x")
    assert status == OK and not rt.decide(ME, "/x")[0]  # applied to the initial authority
    assert len(fake.requests) == 6  # the file, four more hops on a.example, the target on cdn.example


def test_more_than_five_redirects_is_unavailable():
    routes = {"https://a.example/robots.txt": Scripted(302, "", {"Location": "/r1"})}
    routes.update({f"https://a.example/r{i}": Scripted(302, "", {"Location": f"/r{i + 1}"}) for i in range(1, 10)})
    cache, fake = cache_for(routes)
    assert cache.for_url("https://a.example/") == (None, UNAVAILABLE)
    assert len(fake.requests) == 6  # the file and five redirects, no more


def test_redirect_without_location_is_unavailable():
    cache, _ = cache_for({"https://a.example/robots.txt": Scripted(301, "")})
    assert cache.for_url("https://a.example/") == (None, UNAVAILABLE)


def test_cache_hits_within_ttl_and_refetches_after():
    clock = Clock()
    cache, fake = cache_for({"https://a.example/robots.txt": Scripted(200, "User-agent: *\nDisallow:\n")},
                            ttl_s=100, clock=clock)
    for _ in range(3):
        assert cache.for_url("https://a.example/p")[1] == OK
    assert len(fake.requests) == 1
    clock.t += 101
    cache.for_url("https://a.example/q")
    assert len(fake.requests) == 2


def test_unreachable_is_cached_briefly():
    clock = Clock()
    cache, fake = cache_for({"https://a.example/robots.txt": Scripted(503)}, ttl_s=86400,
                            unreachable_ttl_s=600, clock=clock)
    cache.for_url("https://a.example/")
    clock.t += 300
    cache.for_url("https://a.example/")
    assert len(fake.requests) == 1
    clock.t += 301
    cache.for_url("https://a.example/")
    assert len(fake.requests) == 2


def test_fetching_names_only_the_cache_s_own_requests():
    seen = []

    def fetch(u):
        seen.append((u, cache.fetching(u), cache.fetching("https://a.example/post/1")))
        return FakeNet({u: Scripted(200, "User-agent: *\nDisallow:\n")}).fetch(u)

    cache = RobotsCache(fetch)
    cache.for_url("https://a.example/post/1")
    assert seen == [("https://a.example/robots.txt", True, False)]
    assert not cache.fetching("https://a.example/robots.txt")  # only while it is being fetched


def test_a_lookup_that_needs_itself_is_unreachable_not_endless():
    calls = []

    def fetch(u):  # a fetch that (wrongly) asks the cache about the origin being loaded
        calls.append(u)
        assert cache.for_url("https://a.example/elsewhere") == (None, UNREACHABLE)
        return FakeNet({u: Scripted(200, "User-agent: *\nDisallow: /x\n")}).fetch(u)

    cache = RobotsCache(fetch)
    rt, status = cache.for_url("https://a.example/x")
    assert status == OK and not rt.decide(ME, "/x")[0] and calls == ["https://a.example/robots.txt"]

    hops = []

    def chain(u):  # every robots.txt needs yet another host's robots.txt
        hops.append(u)
        cache2.for_url(f"https://h{len(hops)}.example/x")
        return FakeNet({}).fetch(u)

    cache2 = RobotsCache(chain)
    cache2.for_url("https://start.example/")
    assert len(hops) <= 4


def test_cache_key_is_scheme_host_port():
    ok = Scripted(200, "User-agent: *\nDisallow:\n")
    routes = {"https://a.example/robots.txt": ok, "http://a.example/robots.txt": ok,
              "https://a.example:8443/robots.txt": ok, "https://b.example/robots.txt": ok}
    cache, fake = cache_for(routes)
    for u in ("https://a.example/1", "https://A.EXAMPLE:443/2", "http://a.example/3", "https://a.example:8443/4",
              "https://b.example/5", "https://a.example/6?x=1"):
        assert cache.for_url(u)[1] == OK
    assert sorted(u for _, u in fake.requests) == sorted(routes)  # one fetch per origin
    assert RobotsCache.robots_url("https://A.example:443/p?q=1#f") == "https://a.example/robots.txt"
    assert RobotsCache.robots_url("http://[2606:4700::1]:8080/x") == "http://[2606:4700::1]:8080/robots.txt"
