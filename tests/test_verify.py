"""Quotes are checked in code against the Record they cite; Citation.verified is never a model's word."""

from __future__ import annotations

from readproof.types import Channel, Citation, Record
from readproof.verify import clean_quote, locate, norm, substantial, verified, verify_citations, verify_quote

KEY = "douyin:7300000000000000001"


def rec(channel: Channel, locator: dict, text: str) -> Record:
    return Record(record_id=Record.make_id(KEY, channel, locator, text), post_key=KEY, channel=channel, locator=locator,
                  text=text, source_sha256="0" * 64, extractor="test", fetched_at="2026-11-21T00:00:00+00:00")


def test_norm_ignores_spacing_punctuation_case_and_link_targets():
    assert norm("Hello, World！ 你好，世界。") == "helloworld你好世界"
    assert norm("see [the docs](https://example.com/a?b=1) now") == norm("see the docs now")
    assert norm("") == "" and norm(None) == ""  # type: ignore[arg-type]


def test_substantial_counts_cjk_twice():
    assert not substantial("AI") and not substantial("是")
    assert substantial("K7Q2") and substantial("暗号") and not substantial("暗")


def test_locate_across_segments():
    segs = ["今天我们来", "测试语音识别", "暗号是七三九"]
    assert locate("测试语音", segs) == 1
    assert locate("我们来测试", segs) is None
    assert locate("我们来测试", segs, across=True) == 0
    assert locate("识别暗号", segs, across=True) == 1
    assert locate("", segs) is None


def test_verified_breakdown_rule_kept():
    assert verified("", "anything") and verified("a b", norm("xaby")) and not verified("zz", norm("xaby"))


def test_clean_quote_strips_our_labels():
    assert clean_quote("[12.3s] 「暗号是七三九」") == "暗号是七三九"
    assert clean_quote("image 3: Secret code") == "Secret code"
    assert len(clean_quote("x" * 500)) == 200


def test_verify_quote():
    r = rec(Channel.CAPTION, {"field": "caption"}, "三家店的对比，第二家最好喝！")
    assert verify_quote("第二家 最好喝", r)
    assert not verify_quote("第三家最好喝", r)
    assert not verify_quote("", r)


def test_verify_citations_sets_verified_in_code():
    caption = rec(Channel.CAPTION, {"field": "caption"}, "The secret code is K7Q2, said twice.")
    s1 = rec(Channel.SPEECH, {"t0": 0.0, "t1": 2.0}, "the code word is")
    s2 = rec(Channel.SPEECH, {"t0": 2.0, "t1": 4.0}, "seven three nine and")
    s3 = rec(Channel.SPEECH, {"t0": 4.0, "t1": 6.0}, "that is all")
    by_id = {r.record_id: r for r in (caption, s1, s2, s3)}
    cites = [
        Citation(caption.record_id, "secret code is K7Q2"),         # in the record
        Citation(caption.record_id, "K7Q3"),                         # fabricated
        Citation("r_unknown", "secret code is K7Q2"),                # unknown record
        Citation(s1.record_id, "code word is seven three nine"),     # runs on into the next segment
        Citation(s2.record_id, "code word is seven"),                # starts in an earlier record: not this one
        Citation(caption.record_id, "is"),                           # too short to be evidence
        Citation(s3.record_id, "[4.0s] that is all", verified=True),  # a model's True is recomputed
    ]
    out = verify_citations(cites, by_id)
    assert out is not cites and [c.verified for c in out] == [True, False, False, True, False, False, True]
    assert all(a is b for a, b in zip(out, cites))  # the same Citation objects, mutated in place


def test_run_on_only_for_recogniser_channels():
    p1 = rec(Channel.PAGE, {"offset": 0}, "first passage ends here")
    p2 = rec(Channel.PAGE, {"offset": 24}, "second passage starts")
    by_id = {r.record_id: r for r in (p1, p2)}
    [c] = verify_citations([Citation(p1.record_id, "ends here second passage")], by_id)
    assert c.verified is False
