"""walls.judge: a 200 OK block page is never served, and every verdict names its evidence (DESIGN §1 rule 6, §5).

Fixtures (tests/fixtures/walls/): `*.real.html` are live captures made on 2026-11-21 with the ReadProof UA,
no cookies, from a residential address, trimmed to what the signature needs (see each case's "source");
everything else is a synthetic minimal page for one wall type. `cases.json` lists every case with the
verdict it must get: wall type, the rule that fires (prefix of the signal) and the marker the signal carries.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from readproof import walls
from readproof.platforms import by_name
from readproof.types import FetchResult, WallType, sha256_bytes

FIXTURES = Path(__file__).parent / "fixtures" / "walls"
CASES = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))


def fetch_for(case: dict) -> FetchResult:
    body = (FIXTURES / case["file"]).read_bytes() if "file" in case else case.get("body", "").encode("utf-8")
    return FetchResult(
        url=case["url"], final_url=case.get("final_url") or case["url"], status=case.get("status"),
        headers=dict(case.get("headers") or {}), bytes=case.get("bytes") or len(body), sha256=sha256_bytes(body),
        fetched_at="2026-11-21T08:00:00+00:00", elapsed_ms=1, origin="test:fixture", user_agent="ReadProof/test",
        error=case.get("error"), body=body,
    )


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_fixture_verdicts(case):
    spec = by_name(case["spec"]) if case.get("spec") else None
    verdict = walls.judge(fetch_for(case), spec)
    expect = case["expect"]
    if expect["wall"] is None:
        assert verdict.served and verdict.wall is None, verdict.signal
    else:
        assert not verdict.served, verdict.signal
        assert verdict.wall is WallType(expect["wall"]), verdict.signal
    assert verdict.signal.startswith(expect["rule"]), verdict.signal
    # The evidence travels with the verdict: status, bytes and visible characters, and the marker that matched.
    for part in ("status=", "bytes=", "visible_chars="):
        assert part in verdict.signal, verdict.signal
    if expect.get("marker"):
        assert expect["marker"] in verdict.signal, verdict.signal
    if expect.get("wraps"):
        assert f"wraps={expect['wraps']}" in verdict.signal
    if "visible_chars" in expect:
        assert verdict.visible_chars == expect["visible_chars"]
        assert f"visible_chars={expect['visible_chars']}" in verdict.signal


def test_every_wall_type_a_page_can_cause_has_a_fixture():
    """POLICY is the gate's (tests/test_canonical.py); every other type is pinned here by at least one page."""
    covered = {c["expect"]["wall"] for c in CASES}
    for wall in WallType:
        if wall is WallType.POLICY:
            continue
        assert wall.value in covered, f"no fixture for {wall.value}"
    assert None in covered


def test_real_captures_keep_their_original_size_in_the_signal():
    case = next(c for c in CASES if c["id"] == "douyin-bytedance-verify-live")
    verdict = walls.judge(fetch_for(case))
    assert "bytes=72914" in verdict.signal and "visible_chars=0" in verdict.signal


def test_the_bytedance_page_is_a_challenge_even_without_its_markers():
    """The DESIGN signature is size plus emptiness: ~72 KB, an empty <body>, nothing visible."""
    body = b"<html><head></head><body></body><script>" + b"x" * 72_000 + b"</script></html>"
    fetch = FetchResult(url="https://www.douyin.com/video/7400000000000000001", final_url="https://www.douyin.com/video/7400000000000000001",
                        status=200, headers={"content-type": "text/html"}, bytes=len(body), sha256=sha256_bytes(body),
                        fetched_at="", elapsed_ms=1, origin="test", user_agent="t", body=body)
    verdict = walls.judge(fetch)
    assert verdict.wall is WallType.CHALLENGE and "empty-body-large-script" in verdict.signal


def test_a_served_tiktok_post_with_only_an_app_shell_title_is_served_not_empty():
    """Live 2026-11-21: the served page's only visible text was "TikTok - Make Your Day"."""
    case = next(c for c in CASES if c["id"] == "tiktok-served")
    fetch = fetch_for(case)
    assert walls.judge(fetch).served
    assert walls.judge(fetch, by_name("web")).wall is WallType.EMPTY  # what a platform-blind judge would say


def test_platform_rules_run_before_generic_ones():
    """A served Xiaohongshu note has almost no visible text; without its platform rule it reads as empty."""
    case = next(c for c in CASES if c["id"] == "xhs-note-payload")
    fetch = fetch_for(case)
    assert walls.judge(fetch).served
    assert walls.judge(fetch, by_name("web")).wall is WallType.EMPTY


def test_sign_in_text_on_a_served_x_page_is_not_a_login_wall():
    case = next(c for c in CASES if c["id"] == "x-served-with-login-sidebar")
    assert walls.judge(fetch_for(case)).served


def test_challenge_words_inside_a_long_article_are_not_a_challenge():
    text = "<p>" + "这篇文章讲的是如何设计验证码和安全验证系统，" * 60 + "</p>"
    body = f"<html><head><title>验证码设计指南</title></head><body>{text}</body></html>".encode()
    fetch = FetchResult(url="https://example.com/a", final_url="https://example.com/a", status=200, headers={},
                        bytes=len(body), sha256=sha256_bytes(body), fetched_at="", elapsed_ms=1, origin="t",
                        user_agent="t", body=body)
    assert walls.judge(fetch).served


def _page(body: str, url: str = "https://example.com/a", status: int = 200, **kw) -> FetchResult:
    raw = body.encode("utf-8")
    return FetchResult(url=url, final_url=kw.get("final_url", url), status=status, headers=kw.get("headers", {}),
                       bytes=len(raw), sha256=sha256_bytes(raw), fetched_at="", elapsed_ms=1, origin="t",
                       user_agent="t", body=raw)


def test_a_short_page_titled_not_found_is_deleted_not_empty():
    for title in ("404 Not Found", "Page not found", "页面不存在"):
        verdict = walls.judge(_page(f"<html><head><title>{title}</title></head><body>oops</body></html>"))
        assert verdict.wall is WallType.DELETED and verdict.signal.startswith("generic:not-found-title"), title


def test_a_title_that_only_starts_like_a_site_name_is_not_an_app_shell():
    short = walls.judge(_page("<html><head><title>X-ray results</title></head><body>short</body></html>"))
    assert short.signal.startswith("generic:no-post-evidence")  # not the site-title rule: "X-ray" is not X
    named = "<html><head><title>X-ray results</title><meta property='og:title' content='X-ray results'></head><body>short"
    assert walls.judge(_page(named + "</body></html>")).served
    assert walls.judge(_page("<html><head><title>抖音-记录美好生活</title></head><body></body></html>")).wall is WallType.EMPTY


def test_a_body_less_result_never_poisons_the_parse_cache():
    """A FetchResult replayed from a log has a sha256 but no body; judging it must not cache an empty page
    under that sha for the real fetch of the same bytes."""
    real = _page((FIXTURES / "generic_article.html").read_text(encoding="utf-8"))
    replay = FetchResult(**{**real.__dict__, "body": None})
    walls.judge(replay)
    assert walls.judge(real).served


# --- visible_text ----------------------------------------------------------------------------------------------


def test_visible_text_drops_what_nobody_sees():
    html = (FIXTURES / "generic_spa_shell.html").read_text(encoding="utf-8")
    title, text = walls.visible_text(html)
    assert title == "" and text == ""


def test_visible_text_decodes_entities_and_collapses_whitespace():
    title, text = walls.visible_text("<title> A &amp; B </title><body><p>one&nbsp;&nbsp;two</p>\n\n<p>three &lt;4&gt;</p></body>")
    assert title == "A & B"
    assert text == "one two three <4>"


def test_visible_text_keeps_inline_runs_together_and_separates_blocks():
    _, text = walls.visible_text("<div>参数<span>错误</span></div><div>next</div><br>last")
    assert text == "参数错误 next last"


def test_an_svg_title_is_not_the_page_title():
    title, text = walls.visible_text("<html><head><title>Page</title></head><body><svg><title>icon</title></svg>x</body></html>")
    assert title == "Page" and text == "x"
    title, _ = walls.visible_text("<body><svg><title>icon</title></svg></body>")
    assert title == ""


def test_hidden_inputs_comments_and_templates_carry_no_text():
    _, text = walls.visible_text('<input type="hidden" value="secret"><!-- note --><template>t</template><p>shown</p>')
    assert text == "shown"


def test_the_wechat_error_page_reads_as_its_message():
    html = (FIXTURES / "wechat_param_error.real.html").read_text(encoding="utf-8")
    title, text = walls.visible_text(html)
    assert title == "" and text.startswith("参数错误")


def test_transport_error_is_network_whatever_the_platform():
    fetch = FetchResult(url="https://www.toutiao.com/article/7709705250554266155/", final_url="", status=None, headers={},
                        bytes=0, sha256=sha256_bytes(b""), fetched_at="", elapsed_ms=1, origin="t", user_agent="t",
                        error="ReadTimeout", body=b"")
    verdict = walls.judge(fetch)
    assert verdict.wall is WallType.NETWORK and "ReadTimeout" in verdict.signal


def test_gate_refused_hop_is_a_policy_wall_not_network():
    """HttpNet reports a redirect hop our gate refused as error 'gate_refused:<reason>' (nothing was sent)."""
    from readproof.types import FetchResult, WallType
    from readproof.walls import judge

    f = FetchResult(url="https://v.douyin.com/AbCdEf/", final_url="https://v.douyin.com/AbCdEf/", status=302,
                    headers={}, bytes=0, sha256="", fetched_at="2026-11-21T00:00:00+00:00", elapsed_ms=1,
                    origin="test", user_agent="ReadProof/test", error="gate_refused:robots_disallow")
    v = judge(f)
    assert not v.served and v.wall == WallType.POLICY and "robots_disallow" in v.signal
