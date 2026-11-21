"""Offline tests for bench/score.py: fingerprints, grams, the served check, bootstrap and the tables."""

from __future__ import annotations

import math
import random

from bench import score


def test_grams_cjk_bigrams_cross_punctuation_not_latin():
    g = score.grams("外焦里嫩，又入味")
    assert {"外焦", "焦里", "里嫩", "嫩又", "又入", "入味"} <= g  # the comma does not break the run
    g2 = score.grams("小米SU7评测")
    assert "小米" in g2 and "评测" in g2 and "su7" in g2
    assert "米评" not in g2  # a Latin token ends a CJK run


def test_grams_latin_tokens_stopwords_and_nfkc():
    g = score.grams("The ONLY Skincare Routine Men Actually Need")
    assert g == {"skincare", "routine", "men", "actually", "need"}  # "the", "only": stop words
    assert score.grams("ＡＩ工具") == {"ai", "工具"}  # full-width folds to ASCII
    assert "a" not in score.grams("a b c") and score.grams("x") == set()


def test_grams_kana_and_single_cjk():
    g = score.grams("美味しい料理")
    assert "美味" in g and "しい" in g
    assert score.grams("我 love 你") == {"我", "love", "你"}


def test_fingerprint_strips_boilerplate():
    assert score.fingerprint("#家常菜 #教你做美食 - 抖音")[0] == "#家常菜 #教你做美食"
    assert score.fingerprint("详细体验全新蔚来ES8_哔哩哔哩_bilibili")[0] == "详细体验全新蔚来ES8"
    assert score.fingerprint("Every New AI Model Explained (2026) - YouTube")[0] == "Every New AI Model Explained (2026)"
    assert score.fingerprint("护肤真的可以简单！坚持坚持坚持 - 小红书")[0] == "护肤真的可以简单！坚持坚持坚持"
    assert score.fingerprint("✈My Singapore travel checklist📝 - rednote")[0] == "✈My Singapore travel checklist📝"


def test_fingerprint_x_titles_and_truncation():
    fp, trunc = score.fingerprint('NerdWallet on X: "Tips to make your money-saving resolutions stick." / X')
    assert fp == "Tips to make your money-saving resolutions stick." and not trunc
    fp, trunc = score.fingerprint('Allrecipes on X: "It\'s easy, nutritious, and delicious. https://t.co/dW13ZlXo6V" / X')
    assert "t.co" not in fp and "delicious" in fp
    fp, trunc = score.fingerprint('TravelGov on X: "Many countries require your passport to be valid beyond you')
    assert trunc and fp.endswith("beyond")  # the cut word is dropped


def test_fingerprint_status_unusable():
    st = score.fingerprint_status("护肤记录。 - 小红书", "xiaohongshu")
    assert not st["usable"] and "shorter" in st["reason"]
    st = score.fingerprint_status("抖音", "douyin")
    assert not st["usable"] and st["reason"] == "platform boilerplate only"
    st = score.fingerprint_status("TikTok - Make Your Day", "tiktok")
    assert not st["usable"]
    assert score.fingerprint_status("这才是家常煎豆腐的正确做法 - 抖音", "douyin")["usable"]


def test_served_by_fingerprint_threshold():
    fp = score.fingerprint_status("一镜到底！四台机器的实测成绩告诉你华为到底是不是4G - 抖音", "douyin")
    assert fp["usable"]
    served, cov = score.served_by_fingerprint(fp, "抖音 一镜到底！四台机器的实测成绩告诉你华为到底是不是4G #手机")
    assert served and cov == 1.0
    served, cov = score.served_by_fingerprint(fp, "验证码 请完成下列验证后继续")
    assert served is False and cov < 0.2
    half = "一镜到底四台机器"  # a prefix covers well under 60%
    served, cov = score.served_by_fingerprint(fp, half)
    assert served is False and 0 < cov < score.THRESHOLD
    bad = score.fingerprint_status("抖音", "douyin")
    assert score.served_by_fingerprint(bad, "anything") == (None, 0.0)


def test_seed_fingerprints_mostly_usable():
    seeds = score.load_seeds()
    assert len(seeds) == 145
    usable = [s for s in seeds.values() if s["fp"]["usable"]]
    assert len(usable) >= 140
    translated = [k for k, s in seeds.items() if not s["fp"]["usable"]]
    assert len(translated) == 3 and all(k.startswith("xiaohongshu:") for k in translated)
    for s in seeds.values():  # no fingerprint keeps the engine's suffix
        assert not s["fp"]["text"].endswith(("- 抖音", "_bilibili", "- YouTube", "- 今日头条", '" / X'))


def test_boot_mean_binomial_path_matches_resampling():
    vals = [1.0] * 30 + [0.0] * 70
    m, lo, hi = score.boot_mean(vals, b=4000)
    assert m == 0.3 and lo < 0.3 < hi
    # brute force resampling gives about the same interval
    rng = random.Random(1)
    sims = sorted(sum(vals[int(rng.random() * 100)] for _ in range(100)) / 100 for _ in range(4000))
    assert abs(sims[100] - lo) <= 0.03 and abs(sims[3899] - hi) <= 0.03
    assert score.boot_mean(vals, b=2000) == score.boot_mean(vals, b=2000)  # fixed seed: reproducible


def test_boot_mean_paired_three_values():
    diffs = [1.0] * 40 + [-1.0] * 10 + [0.0] * 50
    m, lo, hi = score.boot_mean(diffs, b=4000)
    assert math.isclose(m, 0.3) and lo < 0.3 < hi and lo > 0.1
    assert score.boot_mean([]) is None
    m, lo, hi = score.boot_mean([0.5, 0.25, 1.0], b=500)
    assert lo <= m <= hi


def test_boot_cluster():
    clusters = [[1.0, 1.0], [0.0, 0.0], [1.0, 0.0]] * 10
    m, lo, hi = score.boot_cluster(clusters, b=2000)
    assert math.isclose(m, 0.5) and lo < 0.5 < hi


def _item(system, post, form, served, platform="douyin", wall=None):
    return {"key": f"{post}|{form}|{system}", "system": system, "post_key": post, "form": form, "served": served,
            "platform": platform, "wall": wall, "harness_served": None, "coverage": 1.0 if served else 0.0}


def test_paired_uses_common_items_only():
    items = [_item("readproof_p1", "p1", "a", True), _item("readproof_p1", "p1", "b", True),
             _item("naive_fetch", "p1", "a", False), _item("naive_fetch", "p2", "a", False),
             _item("readproof_p1", "p3", "a", None), _item("naive_fetch", "p3", "a", True)]
    res = score.paired(items, "readproof_p1", "naive_fetch")
    assert res["n_items"] == 1 and res["diff_items"]["mean"] == 100.0


def test_latest_reads_skipped_then_done():
    rows = [{"key": "k1", "kind": "skipped", "platform": "x", "system": "s"},
            {"key": "k1", "kind": "read", "platform": "x", "system": "s"},
            {"key": "k2", "kind": "skipped", "platform": "x", "system": "s"},
            {"kind": "run"}]
    done, skipped = score.latest_reads(rows)
    assert set(done) == {"k1"} and set(skipped) == {"k2"}


def test_fabrication_counts_only_unserved_reads():
    items = [_item("naive_fetch", "p1", "a", False, wall="challenge"), _item("naive_fetch", "p2", "a", True),
             _item("naive_fetch", "p3", "a", False, wall=None)]
    answers = [{"key": "p1|a|naive_fetch", "answer_system": "naive_fetch", "abstained": False, "cause": "answered"},
               {"key": "p2|a|naive_fetch", "answer_system": "naive_fetch", "abstained": False, "cause": "answered"},
               {"key": "p3|a|naive_fetch", "answer_system": "naive_fetch", "abstained": True, "cause": "token"}]
    fab = score.fabrication(items, answers)["naive_fetch"]
    # headline: a page the judge flags as a wall with no trace of the post; an unflagged page is reported apart
    assert fab["eligible"] == 1 and fab["answered"] == 1
    assert fab["unflagged_no_trace"]["eligible"] == 1 and fab["unflagged_no_trace"]["answered"] == 0
    assert fab["all_unserved"] == {"eligible": 2, "answered": 1}


def test_trackk_counts_and_latex_has_no_codes():
    rows = [{"system": "naive_fetch", "channel": "caption", "outcome": "abstained"},
            {"system": "naive_fetch", "channel": "caption", "outcome": "fabricated"},
            {"system": "readproof_p1", "channel": "speech", "outcome": "correct"}]
    counts = score.trackk_counts(rows)
    assert counts["naive_fetch"]["caption"] == {"abstained": 1, "fabricated": 1}
    tex = score.latex_trackk({"counts": counts, "channels": ["speech", "caption"], "n_items": 3, "n_posts": 1,
                              "platforms": ["youtube"], "date": "2026-11-21"})
    assert "\\toprule" in tex and "0/1/1/0" in tex and "1/0/0/0" in tex


def test_latex_access_renders():
    items = [_item("readproof_p1", "douyin:1", "desktop", True), _item("readproof_p0", "douyin:1", "desktop", False,
                                                                       wall="policy"),
             _item("naive_fetch", "douyin:1", "desktop", False, wall="challenge")]
    acc = score.access_tables(items, {})
    hon = score.honesty(items, ["douyin"])
    tex = score.latex_access(acc, hon, {"origin": "residential:MY", "date": "2026-11-21", "n_posts": 1})
    assert "\\midrule" in tex and "Douyin" in tex and "+100.0" in tex


def test_fabrication_headline_excludes_partial_coverage():
    a = _item("browser_text", "y1", "desktop", False, platform="youtube", wall=None)
    a["coverage"] = 0.4  # a translated title: the post in other words
    b = _item("browser_text", "y2", "desktop", False, platform="youtube", wall="login")
    answers = [{"key": a["key"], "answer_system": "browser_text", "abstained": False, "cause": "answered"},
               {"key": b["key"], "answer_system": "browser_text", "abstained": False, "cause": "answered"}]
    fab = score.fabrication([a, b], answers)["browser_text"]
    assert fab["eligible"] == 1 and fab["answered"] == 1
    assert fab["partial_coverage"] == {"eligible": 1, "answered": 1}
    assert fab["all_unserved"] == {"eligible": 2, "answered": 2}


def test_imputed_items_only_for_breaker_skips():
    seeds = {"douyin:1": {"fp": {"usable": True}}}
    skipped = {"douyin:1|desktop|naive_fetch": {"platform": "douyin", "post_key": "douyin:1", "form": "desktop",
                                                 "system": "naive_fetch", "skip_reason": "circuit_open"},
               "douyin:1|modal|naive_fetch": {"platform": "douyin", "post_key": "douyin:1", "form": "modal",
                                               "system": "naive_fetch", "skip_reason": "request_cap"}}
    imp = score.imputed_items(skipped, seeds)
    assert len(imp) == 1 and imp[0]["served"] is False and imp[0]["kind"] == "imputed"
