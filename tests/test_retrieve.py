"""Per-channel BM25 with CJK bigrams, fused across channels by reciprocal rank."""

from __future__ import annotations

from readproof.retrieve import BM25, rank_channel, search, tokenize
from readproof.types import Channel, Record

KEY = "xiaohongshu:66aa00000000000000000001"


def rec(channel: Channel, locator: dict, text: str) -> Record:
    return Record(record_id=Record.make_id(KEY, channel, locator, text), post_key=KEY, channel=channel, locator=locator,
                  text=text, source_sha256="0" * 64, extractor="test", fetched_at="t")


def test_tokenize_cjk_bigrams_and_words():
    assert tokenize("暗号是739 Secret-Code") == ["暗号", "号是", "739", "secret", "code"]
    assert tokenize("猫 cat") == ["猫", "cat"]
    assert tokenize("東京タワー") == ["東京", "京タ", "タワ", "ワー"]
    assert tokenize("") == []


def test_bm25_prefers_rarer_terms():
    docs = [tokenize("the cat sat"), tokenize("the dog sat"), tokenize("the cat and the cat")]
    s = BM25(docs).scores(tokenize("cat"))
    assert s[1] == 0 and s[2] > s[0] > 0


def test_search_finds_the_channel_that_holds_the_answer():
    records = [
        rec(Channel.CAPTION, {"field": "title"}, "上海咖啡探店"),
        rec(Channel.CAPTION, {"field": "caption"}, "三家店的对比，第二家最好喝"),
        rec(Channel.SPEECH, {"t0": 0.0, "t1": 3.0}, "今天带大家去三家咖啡店"),
        rec(Channel.SPEECH, {"t0": 3.0, "t1": 6.0}, "第二家的拿铁要三十八块"),
        rec(Channel.IMAGE_TEXT, {"index": 2}, "价目表 拿铁 38元 美式 28元"),
        rec(Channel.COMMENT, {"rank": 1}, "第二家在哪里"),
    ]
    top = search("拿铁多少钱", records, k=3)
    assert top[0].channel in (Channel.SPEECH, Channel.IMAGE_TEXT)
    assert {r.channel for r in top[:2]} == {Channel.SPEECH, Channel.IMAGE_TEXT}  # one best match per channel first
    assert search("拿铁", records, k=8, channels=[Channel.IMAGE_TEXT]) == [records[4]]
    assert search("拿铁", records, k=8, channels=["speech"]) == [records[3]]
    assert search("无关的词语", records) == []
    assert len(search("第二家", records, k=2)) == 2


def test_rrf_puts_every_channels_best_before_any_second_best():
    records = [rec(Channel.SPEECH, {"t0": float(i), "t1": float(i + 1)}, f"alpha beta {i}") for i in range(3)]
    records.append(rec(Channel.CAPTION, {"field": "title"}, "alpha"))
    out = search("alpha", records, k=4)
    assert {out[0].channel, out[1].channel} == {Channel.SPEECH, Channel.CAPTION}
    assert rank_channel("alpha", []) == []
