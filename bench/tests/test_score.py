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


def test_probe_answers_keep_one_backbone_and_the_current_read():
    done = {"k1": {"run_id": "r2"}, "k2": {"run_id": "r1"}}
    answers = [
        {"kind": "probe", "key": "k1", "backbone": "gemma:free", "read_run_id": "r1"},   # old backbone, old read
        {"kind": "probe", "key": "k1", "backbone": "ernie:free", "read_run_id": "r1"},   # made on a superseded read
        {"kind": "probe", "key": "k1", "backbone": "ernie:free", "read_run_id": "r2"},   # counts
        {"kind": "probe", "key": "k2", "backbone": "ernie:free", "read_run_id": "r1"},   # counts
        {"kind": "run", "phase": "probe", "backbone": "ernie:free"},
    ]
    kept, backbone = score.probe_answers(answers, done, None)
    assert backbone == "ernie:free" and [(a["key"], a["read_run_id"]) for a in kept] == [("k1", "r2"), ("k2", "r1")]


def test_round_change_counts_gains_and_losses():
    seeds = {"douyin:1": {"fp": score.fingerprint_status("一镜到底四台机器实测华为 - 抖音", "douyin")}}
    def row(group, text, run):
        return {"kind": "read", "key": "douyin:1|desktop|readproof_p1", "platform": "douyin", "post_key": "douyin:1",
                "form": "desktop", "system": "readproof_p1", "text": text, "run_group": group, "run_id": run}
    rows = [row("A1", "", "r1"), row("A2", "一镜到底四台机器实测华为", "r2")]
    done, _ = score.latest_reads(rows)
    change = score.round_change(rows, seeds, score.score_reads(done, seeds))
    cell = change["douyin"]["readproof_p1"]
    assert cell == {"n": 1, "round1_pct": 0.0, "round2_pct": 100.0, "changed": 1, "gained": 1, "lost": 0}


def test_round_change_keeps_systems_apart():
    seeds = {"douyin:1": {"fp": score.fingerprint_status("一镜到底四台机器实测华为 - 抖音", "douyin")}}
    def row(system, group, text):
        return {"kind": "read", "key": f"douyin:1|desktop|{system}", "platform": "douyin", "post_key": "douyin:1",
                "form": "desktop", "system": system, "text": text, "run_group": group, "run_id": group}
    rows = [row("readproof_p0", "A1", ""), row("readproof_p1", "A1", "一镜到底四台机器实测华为"),
            row("readproof_p0", "A2", ""), row("readproof_p1", "A2", "一镜到底四台机器实测华为")]
    done, _ = score.latest_reads(rows)
    change = score.round_change(rows, seeds, score.score_reads(done, seeds))["douyin"]
    assert change["readproof_p0"]["round2_pct"] == 0.0 and change["readproof_p1"]["round2_pct"] == 100.0


# --- a reply that describes the page is not an answer from a wall; the post in other words is served ------------


def test_a_reply_that_describes_the_page_is_not_an_answer_from_a_wall():
    from bench.score import says_not_the_post
    assert says_not_the_post("This is not a single post but the homepage/listing page of Douyin's featured feed.")
    assert says_not_the_post("A featured-video feed page listing many videos, rather than a single post.")
    assert says_not_the_post("这不是一条帖子，而是抖音精选的首页。")
    assert says_not_the_post("This post is a YouTube video comparing four electric SUVs.") is None
    assert says_not_the_post("The video shows a cook stir-frying asparagus with shrimp paste.") is None
    assert says_not_the_post("") is None and says_not_the_post(None) is None


def _ow_item(key, system, served, wall=None, coverage=0.0, platform="bilibili", post_key="bilibili:BV1abcdef"):
    return {"key": key, "platform": platform, "post_key": post_key, "form": "desktop", "system": system,
            "served": served, "coverage": coverage, "wall": wall, "text_chars": 100, "harness_served": None}


def test_other_words_needs_the_posts_own_id_no_wall_and_a_described_post():
    from bench.score import other_words_keys
    done = {
        "k1": {"post_id": "BV1abcdef", "final_url": "https://www.bilibili.com/en/video/BV1abcdef/", "text": "English"},
        "k2": {"post_id": "BV1abcdef", "final_url": "https://www.bilibili.com/", "text": "a feed of other videos"},
        "k3": {"post_id": "BV1abcdef", "final_url": "https://www.bilibili.com/en/video/BV1abcdef/", "text": "x"},
        "k4": {"post_id": "BV1abcdef", "final_url": "https://www.bilibili.com/en/video/BV1abcdef/", "text": "x"},
        "k5": {"post_id": "BV1abcdef", "final_url": "https://www.bilibili.com/en/video/BV1abcdef/", "text": "x"},
    }
    items = [_ow_item("k1", "browser_text", False), _ow_item("k2", "browser_text", False),
             _ow_item("k3", "browser_text", False), _ow_item("k4", "browser_text", False, wall="challenge"),
             _ow_item("k5", "browser_text", True)]
    answers = [{"key": k, "answer_system": "browser_text", "abstained": False, "answer": "A cook makes six dishes."}
               for k in ("k1", "k2", "k4", "k5")]
    answers.append({"key": "k3", "answer_system": "browser_text", "abstained": True, "answer": "NOT_FOUND"})
    ow = other_words_keys(items, done, answers)
    assert ow == {"k1": "translated"}  # k2: no own id; k3: abstained; k4: a wall; k5: already served


# --- no third-party page text in the public results (owner rule; 2026-11-22) -------------------------------------


def test_public_results_carry_no_page_text():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "bench" / "results"
    summary = json.loads((root / "summary.json").read_text("utf-8"))
    for row in summary.get("harness_served_but_fingerprint_missing", []):
        assert "text_head" not in row and set(row) <= {"key", "coverage", "class", "text_sha256"}
    audit = json.loads((root / "trackA_weibo_audit.json").read_text("utf-8"))
    for post in audit["posts"].values():
        assert "capture" not in post and set(post) <= {"status", "note", "capture_sha256", "capture_chars"}
