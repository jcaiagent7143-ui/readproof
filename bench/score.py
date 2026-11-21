"""Scoring for Track A (access, answer-from-wall) and Track K (planted canaries), with paired bootstrap CIs.

Gold check for Track A — served by fingerprint:
  The reference fingerprint is the search-result title of the seed post (bench/seeds/trackA_posts_v1.jsonl) with the
  engine's platform boilerplate removed (" - 抖音", "_哔哩哔哩_bilibili", `Name on X: "…" / X`, URLs, truncation marks).
  Text is normalised NFKC + lowercase, punctuation and whitespace dropped; CJK (Han, kana, Hangul) becomes character
  bigrams (a lone CJK character a unigram), Latin becomes word tokens (single characters and a short English
  stop-word list dropped, so a generic English page cannot match on function words). A read is served when at least
  60% of the fingerprint's distinct grams occur in the text the system obtained. A fingerprint whose remaining
  content is shorter than 6 characters, or is only platform boilerplate, is unusable: its reads are not scored. So is
  a title the engine took from rednote.com/en/ (a machine translation of a Chinese note; the seed notes exclude
  translated titles). Weibo titles are generated summaries, so Weibo coverage is a lower bound.

Bootstrap: 10,000 resamples, fixed seed. For 0/1 and paired {-1, 0, 1} item values the resample is drawn exactly
as binomial counts (the same distribution as resampling items, much faster in pure Python); a cluster bootstrap over
posts (forms of one post are not independent) is reported next to every paired item-level interval.

Usage: python -m bench.score   (reads bench/results/*.jsonl and the private Track K answers if present)
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "bench" / "seeds" / "trackA_posts_v1.jsonl"
RESULTS = ROOT / "bench" / "results"
READS = RESULTS / "trackA_reads.jsonl"
ANSWERS = RESULTS / "trackA_answers.jsonl"
K_SUMMARY = RESULTS / "trackK_summary.json"
K_PRIVATE = ROOT / "bench" / "data" / "private" / "results" / "trackK_answers.jsonl"
TABLES = ROOT / "paper" / "tables"

THRESHOLD = 0.60
NO_TRACE = 0.20  # below this coverage the obtained text holds no trace of the post: an answer is not about the post
MIN_CHARS = 6
B = 10_000
SEED = 20261121

PLATFORM_ORDER = ["douyin", "xiaohongshu", "bilibili", "youtube", "tiktok", "weibo", "toutiao", "x", "wechat"]
PLATFORM_LABEL = {"douyin": "Douyin", "xiaohongshu": "Xiaohongshu", "bilibili": "Bilibili", "youtube": "YouTube",
                  "tiktok": "TikTok", "weibo": "Weibo", "toutiao": "Toutiao", "x": "X", "wechat": "WeChat"}
SYSTEM_ORDER = ["naive_fetch", "browser_text", "reader_service", "readproof_p0", "readproof_p1"]
SYSTEM_LABEL = {"closed_book": "closed book", "naive_fetch": "naive fetch", "browser_text": "browser text",
                "reader_service": "reader service", "readproof_p0": "ReadProof P0", "readproof_p1": "ReadProof P1",
                "readproof_p1_nowalls": "P1 $-$walls", "readproof_p1_noverify": "P1 $-$verify",
                "readproof_p1_nospeech": "P1 $-$speech", "readproof_p1_noscreen": "P1 $-$on-screen"}
BASELINES = ["naive_fetch", "browser_text", "reader_service"]

STOPWORDS = frozenset("""
a an the and or but if of to in on at by for with from as is are was were be been being it its this that these those
i you he she we they my your our their me him her us them so not no do does did have has had will would can could
just all any more most some such than too very there here what which who how when where why up out about into over
after before also only then s t ll re ve d m
""".split())

# Titles (after normalisation) that name a site, not a post.
BOILERPLATE = frozenset({
    "抖音", "小红书", "rednote", "小红书你的生活指南", "tiktok", "tiktokmakeyourday", "哔哩哔哩", "bilibili",
    "哔哩哔哩bilibili", "微博", "微博正文", "新浪微博", "weibo", "今日头条", "头条", "x", "twitter", "youtube",
    "微信公众平台", "微信", "wechat", "douyin", "xiaohongshu", "explorefindyourfavoritevideosontiktok",
})
_SUFFIX = re.compile(
    r"\s*(?:[-|–—_·]\s*)+(?:抖音|小红书|rednote|youtube|今日头条|tiktok|哔哩哔哩|bilibili|微博|weibo|微信公众平台)\s*$", re.I)
_X_TITLE = re.compile(r'^(?P<name>.*?) on X: "(?P<body>.*)$', re.S)
_URLS = re.compile(r"https?://\S+|\bt\.co/\S+", re.I)


# --- fingerprints and grams ----------------------------------------------------------------------------------


def is_cjk(ch: str) -> bool:
    c = ord(ch)
    return (0x4E00 <= c <= 0x9FFF or 0x3400 <= c <= 0x4DBF or 0x20000 <= c <= 0x2EBEF or 0xF900 <= c <= 0xFAFF
            or 0x3040 <= c <= 0x30FF or 0x31F0 <= c <= 0x31FF or 0xAC00 <= c <= 0xD7AF or 0x1100 <= c <= 0x11FF
            or 0x3130 <= c <= 0x318F)


def normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def _word_char(ch: str) -> bool:
    return ch.isalnum()  # letters (any script) and digits; punctuation, symbols, emoji, whitespace are dropped


def grams(text: str) -> set[str]:
    """CJK character bigrams + Latin word tokens of a text (see the module docstring)."""
    t = normalise(text)
    out: set[str] = set()
    # Latin (and other non-CJK) word tokens: whitespace and punctuation separate words, CJK characters too.
    word: list[str] = []
    for ch in t + " ":
        if _word_char(ch) and not is_cjk(ch):
            word.append(ch)
            continue
        if word:
            w = "".join(word)
            if len(w) >= 2 and w not in STOPWORDS:
                out.add(w)
            word = []
    # CJK bigrams over the text with punctuation and whitespace dropped; a non-CJK word character ends a run.
    run: list[str] = []
    for ch in t + "\x00":
        if is_cjk(ch):
            run.append(ch)
            continue
        if _word_char(ch) or ch == "\x00":
            if run:
                if len(run) == 1:
                    out.add(run[0])
                else:
                    out.update(run[i] + run[i + 1] for i in range(len(run) - 1))
            run = []
        # punctuation / whitespace / emoji: dropped, the CJK run continues across it
    return out


def fingerprint(title: str, platform: str = "") -> tuple[str, bool]:
    """The seed title without the engine's platform boilerplate, and whether it looks truncated."""
    s = (title or "").strip()
    truncated = False
    m = _X_TITLE.match(s)
    if m:
        body = m.group("body")
        if body.endswith('" / X'):
            body = body[: -len('" / X')]
        else:
            truncated = True
        s = body
    s = s.replace("_哔哩哔哩_bilibili", "")
    for _ in range(3):
        s2 = _SUFFIX.sub("", s)
        if s2 == s:
            break
        s = s2
    s = _URLS.sub(" ", s)
    s = s.rstrip()
    if s.endswith(("…", "...")):
        truncated = True
        s = s.rstrip(".…").rstrip()
    if platform in ("tiktok", "toutiao") and len(title or "") >= 140:
        truncated = True
    if truncated:
        # the engine cut the title mid-word: the last Latin token is probably partial
        s = re.sub(r"[#@]?[A-Za-z0-9’'\-]+$", "", s).rstrip()
    return s.strip(), truncated


# Seed URLs whose search-result title is a machine translation of the post (rednote.com/en/ pages show an English
# rendering of a Chinese note). The seed notes exclude translated titles; a translation cannot be found in the post.
_TRANSLATED = re.compile(r"rednote\.com/en/", re.I)


def fingerprint_status(title: str, platform: str = "", url: str = "") -> dict[str, Any]:
    """{'text', 'grams', 'usable', 'reason'} for one seed title."""
    fp, truncated = fingerprint(title, platform)
    core = "".join(ch for ch in normalise(fp) if _word_char(ch))
    g = grams(fp)
    reason = None
    if url and _TRANSLATED.search(url):
        reason = "translated title (rednote.com/en/ shows a machine translation, not the note's own words)"
    elif core in BOILERPLATE:
        reason = "platform boilerplate only"
    elif len(core) < MIN_CHARS:
        reason = f"shorter than {MIN_CHARS} characters after boilerplate removal ({len(core)})"
    elif not g:
        reason = "no grams"
    return {"text": fp, "grams": g, "usable": reason is None, "reason": reason, "truncated": truncated,
            "n_grams": len(g)}


def coverage(fp_grams: set[str], text: str, text_grams: set[str] | None = None) -> float:
    if not fp_grams:
        return 0.0
    tg = text_grams if text_grams is not None else grams(text)
    return len(fp_grams & tg) / len(fp_grams)


def served_by_fingerprint(fp: dict[str, Any], text: str) -> tuple[bool | None, float]:
    """(served, coverage); served is None when the fingerprint is unusable."""
    if not fp["usable"]:
        return None, 0.0
    cov = coverage(fp["grams"], text or "")
    return cov >= THRESHOLD, cov


# --- bootstrap -------------------------------------------------------------------------------------------------


def _quantiles(xs: list[float]) -> tuple[float, float]:
    xs = sorted(xs)
    lo = xs[int(math.floor(0.025 * (len(xs) - 1)))]
    hi = xs[int(math.ceil(0.975 * (len(xs) - 1)))]
    return lo, hi


def boot_mean(values: list[float], *, b: int = B, seed: int = SEED) -> tuple[float, float, float] | None:
    """(mean, lo, hi) of item values by the nonparametric bootstrap."""
    n = len(values)
    if n == 0:
        return None
    rng = random.Random(seed)
    mean = sum(values) / n
    cats = Counter(values)
    if set(cats) <= {0, 1, 0.0, 1.0}:
        p = cats.get(1, 0) / n
        sims = [rng.binomialvariate(n, p) / n for _ in range(b)]
    elif set(cats) <= {-1, 0, 1, -1.0, 0.0, 1.0}:
        p_plus, p_minus = cats.get(1, 0) / n, cats.get(-1, 0) / n
        sims = []
        for _ in range(b):
            k_plus = rng.binomialvariate(n, p_plus)
            rest = n - k_plus
            q = p_minus / (1 - p_plus) if p_plus < 1 else 0.0
            k_minus = rng.binomialvariate(rest, min(1.0, q)) if rest else 0
            sims.append((k_plus - k_minus) / n)
    else:
        sims = []
        for _ in range(b):
            s = 0.0
            for _ in range(n):
                s += values[int(rng.random() * n)]
            sims.append(s / n)
    lo, hi = _quantiles(sims)
    return mean, lo, hi


def boot_cluster(clusters: list[list[float]], *, b: int = B, seed: int = SEED) -> tuple[float, float, float] | None:
    """(mean, lo, hi) resampling whole clusters (posts) with replacement; mean = item-weighted."""
    clusters = [c for c in clusters if c]
    k = len(clusters)
    if k == 0:
        return None
    rng = random.Random(seed)
    sums = [sum(c) for c in clusters]
    sizes = [len(c) for c in clusters]
    mean = sum(sums) / sum(sizes)
    sims = []
    for _ in range(b):
        s = n = 0.0
        for _ in range(k):
            i = int(rng.random() * k)
            s += sums[i]
            n += sizes[i]
        sims.append(s / n)
    lo, hi = _quantiles(sims)
    return mean, lo, hi


def ci_dict(res: tuple[float, float, float] | None, n: int, scale: float = 100.0) -> dict[str, Any]:
    if res is None:
        return {"n": n, "mean": None, "lo": None, "hi": None}
    m, lo, hi = res
    return {"n": n, "mean": round(m * scale, 2), "lo": round(lo * scale, 2), "hi": round(hi * scale, 2)}


# --- loading ---------------------------------------------------------------------------------------------------


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a line cut by a crash
    return out


def load_seeds(path: Path = SEEDS) -> dict[str, dict[str, Any]]:
    seeds = {}
    for row in load_jsonl(path):
        key = f"{row['platform']}:{row['post_id']}"
        row["fp"] = fingerprint_status(row["title_or_snippet"], row["platform"], row.get("url", ""))
        seeds[key] = row
    return seeds


def latest_reads(rows: Iterable[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """(done rows by key, skipped rows by key for keys never done). The last line of a key wins."""
    done: dict[str, dict[str, Any]] = {}
    skipped: dict[str, dict[str, Any]] = {}
    for r in rows:
        k = r.get("key")
        if not k:
            continue
        if r.get("kind") in ("read", "reused"):
            done[k] = r
            skipped.pop(k, None)
        elif r.get("kind") == "skipped" and k not in done:
            skipped[k] = r
    return done, skipped


IMPUTE_SKIPS = ("circuit_open", "platform_stopped")  # the platform kept refusing this system; request_cap is ours


def imputed_items(skipped: dict[str, dict[str, Any]], seeds: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Items for reads the politeness breaker skipped, scored as not served (the pair had just been refused six
    times in a row). Used only in the views labelled "breaker-skipped = not served"."""
    out = []
    for key, r in skipped.items():
        seed = seeds.get(r.get("post_key"))
        if seed is None or r.get("skip_reason") not in IMPUTE_SKIPS or not seed["fp"]["usable"]:
            continue
        out.append({"key": key, "platform": r["platform"], "post_key": r["post_key"], "form": r["form"],
                    "form_role": r.get("form_role"),
                    "system": r["system"], "served": False, "coverage": 0.0, "wall": "skipped:" + r["skip_reason"],
                    "text_chars": 0, "visible_chars": 0, "bytes": 0, "harness_served": None, "status": None,
                    "error": None, "kind": "imputed", "seconds": 0.0})
    return out


def score_reads(done: dict[str, dict[str, Any]], seeds: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """One scored item per read: served by fingerprint (None = unusable), coverage, labels."""
    items = []
    gram_cache: dict[str, set[str]] = {}
    for key, r in done.items():
        seed = seeds.get(r["post_key"])
        if seed is None:
            continue
        if str(r.get("error") or "").startswith(("system error", "harness error")):
            continue  # our own code crashed: not an observation of the platform (counted in summary["crashes"])
        text = r.get("text") or ""
        h = r.get("text_sha256") or str(hash(text))
        tg = gram_cache.get(h)
        if tg is None:
            tg = gram_cache[h] = grams(text)
        fp = seed["fp"]
        if fp["usable"]:
            cov = coverage(fp["grams"], text, tg)
            served: bool | None = cov >= THRESHOLD
        else:
            cov, served = 0.0, None
        items.append({
            "key": key, "platform": r["platform"], "post_key": r["post_key"], "form": r["form"],
            "form_role": r.get("form_role"), "final_url": r.get("final_url"),
            "system": r["system"], "served": served, "coverage": round(cov, 3), "wall": r.get("wall"),
            "text_chars": len(text), "visible_chars": r.get("visible_chars", 0), "bytes": r.get("bytes", 0),
            "harness_served": (r.get("meta") or {}).get("harness_served"), "status": r.get("status"),
            "error": r.get("error"), "kind": r.get("kind"), "seconds": r.get("seconds"),
        })
    return items


# --- Track A metrics ----------------------------------------------------------------------------------------


def _rate(xs: list[bool]) -> float | None:
    return (100.0 * sum(xs) / len(xs)) if xs else None


def access_tables(items: list[dict[str, Any]], skipped: dict[str, dict[str, Any]],
                  imputed: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    imputed = imputed or []
    by = defaultdict(list)
    for it in items:
        by[(it["platform"], it["system"])].append(it)
    by_imp = defaultdict(list)
    for it in imputed:
        by_imp[(it["platform"], it["system"])].append(it)
    platforms = [p for p in PLATFORM_ORDER if any(k[0] == p for k in by)]
    systems = [s for s in SYSTEM_ORDER if any(k[1] == s for k in by)]
    out: dict[str, Any] = {"platforms": {}, "overall": {}, "walls": {}, "skipped": {}}
    for p in platforms:
        row = {}
        for s in systems:
            cell = [it for it in by.get((p, s), []) if it["served"] is not None]
            imp = cell + by_imp.get((p, s), [])
            row[s] = {"n": len(cell), "served": sum(1 for it in cell if it["served"]),
                      "served_pct": _rate([it["served"] for it in cell]),
                      "ci": ci_dict(boot_mean([1.0 if it["served"] else 0.0 for it in cell]), len(cell)),
                      "n_breaker_skipped": len(by_imp.get((p, s), [])),
                      "served_pct_skips_as_not_served": _rate([it["served"] for it in imp])}
            walls = Counter((it["wall"] or "served") for it in by.get((p, s), []))
            out["walls"].setdefault(p, {})[s] = dict(walls.most_common())
        out["platforms"][p] = row
    for s in systems:
        cell = [it for it in items if it["system"] == s and it["served"] is not None]
        imp = cell + [it for it in imputed if it["system"] == s]
        out["overall"][s] = {"n": len(cell), "served": sum(1 for it in cell if it["served"]),
                             "served_pct": _rate([it["served"] for it in cell]),
                             "ci": ci_dict(boot_mean([1.0 if it["served"] else 0.0 for it in cell]), len(cell)),
                             "n_with_skips": len(imp),
                             "served_pct_skips_as_not_served": _rate([it["served"] for it in imp]),
                             "ci_skips_as_not_served": ci_dict(boot_mean([1.0 if it["served"] else 0.0 for it in imp]),
                                                               len(imp)),
                             "macro_pct": _macro([it for it in cell])}
    sk = Counter((r["platform"], r["system"], r.get("skip_reason", "")) for r in skipped.values())
    for (p, s, why), n in sk.items():
        out["skipped"].setdefault(p, {}).setdefault(s, {})[why] = n
    return out


def _macro(cell: list[dict[str, Any]]) -> float | None:
    """Mean over platforms of each platform's served % (every platform weighs the same)."""
    g = defaultdict(list)
    for it in cell:
        g[it["platform"]].append(bool(it["served"]))
    rates = [100.0 * sum(v) / len(v) for v in g.values() if v]
    return sum(rates) / len(rates) if rates else None


def paired(items: list[dict[str, Any]], a: str, b: str, *, platform: str | None = None,
           metric: str = "served") -> dict[str, Any]:
    """P(a) - P(b) on the items both systems have, item-level and post-cluster bootstrap CIs (percentage points)."""
    ia = {(it["post_key"], it["form"]): it for it in items if it["system"] == a and it[metric] is not None
          and (platform is None or it["platform"] == platform)}
    ib = {(it["post_key"], it["form"]): it for it in items if it["system"] == b and it[metric] is not None
          and (platform is None or it["platform"] == platform)}
    common = sorted(set(ia) & set(ib))
    diffs = [float(bool(ia[k][metric])) - float(bool(ib[k][metric])) for k in common]
    clusters: dict[str, list[float]] = defaultdict(list)
    for k, d in zip(common, diffs):
        clusters[k[0]].append(d)
    return {"a": a, "b": b, "platform": platform, "n_items": len(common), "n_posts": len(clusters),
            "a_pct": _rate([bool(ia[k][metric]) for k in common]), "b_pct": _rate([bool(ib[k][metric]) for k in common]),
            "diff_items": ci_dict(boot_mean(diffs), len(common)),
            "diff_posts": ci_dict(boot_cluster(list(clusters.values())), len(common))}


def honesty(items: list[dict[str, Any]], platforms: list[str]) -> dict[str, Any]:
    out = {"overall": paired(items, "readproof_p1", "readproof_p0")}
    for p in platforms:
        out[p] = paired(items, "readproof_p1", "readproof_p0", platform=p)
    p0 = [it for it in items if it["system"] == "readproof_p0"]
    out["p0_policy_share"] = {
        "overall": _rate([it["wall"] == "policy" for it in p0]),
        **{p: _rate([it["wall"] == "policy" for it in p0 if it["platform"] == p]) for p in platforms},
    }
    return out


def capture_served_keys(items: list[dict[str, Any]], done: dict[str, dict[str, Any]]) -> set[str]:
    """Reads whose text holds the audited reference capture of the post (Weibo): the post, not a wall."""
    path = RESULTS / "trackA_weibo_audit.json"
    if not path.exists():
        return set()
    audit = json.loads(path.read_text("utf-8"))["posts"]
    caps = {k: grams(v["capture"]) for k, v in audit.items() if v.get("capture")}
    out = set()
    for it in items:
        cap = caps.get(it["post_key"])
        if cap and coverage(cap, (done.get(it["key"]) or {}).get("text") or "") >= THRESHOLD:
            out.add(it["key"])
    return out


def fabrication(items: list[dict[str, Any]], answers: list[dict[str, Any]],
                exclude: set[str] | None = None) -> dict[str, Any]:
    """Answer-from-wall: a non-abstaining answer on a read NOT served by fingerprint where text was obtained.
    ``exclude``: reads known to hold the post although the title fingerprint missed it (Weibo capture audit)."""
    exclude = exclude or set()
    items = [it for it in items if it["key"] not in exclude]
    by_key = {it["key"]: it for it in items}
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for a in answers:
        latest[(a["key"], a["answer_system"])] = a
    per: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (key, system), a in latest.items():
        it = by_key.get(key)
        if it is None or it["served"] is not False:
            continue
        if a.get("error") and a.get("answer") is None and a.get("cause") == "error":
            continue  # the model call failed: not an observation
        per[system].append({**a, "observer_wall": it["wall"], "platform": it["platform"], "post_key": it["post_key"],
                            "form": it["form"], "coverage": it["coverage"],
                            "translated_hint": bool(re.search(r"://[^/]+/en/", str(it.get("final_url") or "")))})
    out: dict[str, Any] = {}
    for system, all_rows in per.items():
        # Headline: the page is a wall by readproof's own judge (walls.judge on what a baseline fetched; the harness's
        # verdict for ReadProof) AND the text holds no trace of the post (coverage < NO_TRACE). A page the judge did
        # not flag can still be the post in other words (Bilibili /en/ and YouTube serve machine-translated titles to a
        # headless browser; Weibo titles are summaries), so unflagged and partial-coverage reads are reported apart.
        rows = [r for r in all_rows if r["coverage"] < NO_TRACE and r["observer_wall"] is not None]
        unflagged = [r for r in all_rows if r["coverage"] < NO_TRACE and r["observer_wall"] is None]
        ambiguous = [r for r in all_rows if r["coverage"] >= NO_TRACE]
        ans = [0.0 if r["abstained"] else 1.0 for r in rows]
        on_wall = [r for r in rows if r["observer_wall"] is not None]
        on_served = [r for r in rows if r["observer_wall"] is None]
        clusters: dict[str, list[float]] = defaultdict(list)
        for r, v in zip(rows, ans):
            clusters[r["post_key"]].append(v)
        out[system] = {
            "eligible": len(rows), "answered": int(sum(ans)),
            "rate": ci_dict(boot_mean(ans), len(rows)),
            "rate_posts": ci_dict(boot_cluster(list(clusters.values())), len(rows)),
            "observer_wall": {"eligible": len(on_wall), "answered": sum(1 for r in on_wall if not r["abstained"])},
            "observer_served": {"eligible": len(on_served), "answered": sum(1 for r in on_served if not r["abstained"])},
            "llm_calls": sum(int(r.get("llm_calls") or 0) for r in rows),
            "by_platform": {p: {"eligible": len(g), "answered": sum(1 for r in g if not r["abstained"])}
                            for p, g in _group(rows, "platform").items()},
            "how": dict(Counter(r.get("cause") or "answered" for r in rows)),
            "partial_coverage": {"eligible": len(ambiguous), "answered": sum(1 for r in ambiguous if not r["abstained"])},
            "unflagged_no_trace": {"eligible": len(unflagged), "answered": sum(1 for r in unflagged if not r["abstained"]),
                                   "translated_hint": {"eligible": sum(1 for r in unflagged if r["translated_hint"]),
                                                       "answered": sum(1 for r in unflagged if r["translated_hint"]
                                                                       and not r["abstained"])},
                                   "no_translation_hint": {"eligible": sum(1 for r in unflagged if not r["translated_hint"]),
                                                           "answered": sum(1 for r in unflagged if not r["translated_hint"]
                                                                           and not r["abstained"])},
                                   "by_platform": {p: {"eligible": len(g), "answered": sum(1 for r in g if not r["abstained"])}
                                                   for p, g in _group(unflagged, "platform").items()}},
            "all_unserved": {"eligible": len(all_rows), "answered": sum(1 for r in all_rows if not r["abstained"])},
        }
        if system.startswith("readproof_p") and "_" not in system[len("readproof_p"):]:
            not_served = [r for r in all_rows if r.get("harness_served") is False]
            out[system]["typed_abstention_check"] = {
                "harness_not_served": len(not_served),
                "abstained": sum(1 for r in not_served if r["abstained"]),
                "abstained_without_model_call": sum(1 for r in not_served if r["abstained"]
                                                    and int(r.get("llm_calls") or 0) == 0),
                "cause_equals_wall": sum(1 for r in not_served if r.get("cause") == r.get("read_wall")),
                "harness_served_not_fingerprint": sum(1 for r in all_rows if r.get("harness_served") is True),
                "harness_served_not_fingerprint_answered": sum(1 for r in all_rows if r.get("harness_served") is True
                                                              and not r["abstained"]),
            }
    return out


def _group(rows: list[dict[str, Any]], field: str) -> dict[str, list[dict[str, Any]]]:
    g: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        g[r[field]].append(r)
    return g


def fabrication_paired(items: list[dict[str, Any]], answers: list[dict[str, Any]], baselines: list[str],
                       exclude: set[str] | None = None) -> dict[str, Any]:
    """Per item: 1 if the system answered without the post (not served + answered), else 0; P1 vs each baseline."""
    exclude = exclude or set()
    ans = {(a["key"], a["answer_system"]): a for a in answers}
    flags: list[dict[str, Any]] = []
    for it in items:
        if it["served"] is None:
            continue
        if it["key"] in exclude or (it["served"] is False and (it["coverage"] >= NO_TRACE or it["wall"] is None)):
            continue  # the post may be there in other words (or a wall the judge missed): not scored either way
        a = ans.get((it["key"], it["system"]))
        if it["served"]:
            fab = False
        elif a is not None:
            if a.get("cause") == "error" and a.get("answer") is None:
                continue  # the model call failed: unknown
            fab = not a["abstained"]
        elif it["text_chars"] == 0 or (it["system"].startswith("readproof") and it["harness_served"] is False):
            fab = False  # nothing reached the model (ReadProof: a typed wall abstains by construction)
        else:
            continue  # not probed yet: unknown, left out rather than counted as an abstention
        flags.append({**it, "fab": fab})
    out = {}
    for bsys in baselines:
        out[bsys] = paired(flags, "readproof_p1", bsys, metric="fab")
    return out


def weibo_audit(items: list[dict[str, Any]], done: dict[str, dict[str, Any]], seeds: dict[str, dict[str, Any]],
                imputed: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Supplementary Weibo view: served if the title fingerprint OR the audited reference capture is found."""
    path = RESULTS / "trackA_weibo_audit.json"
    if not path.exists():
        return None
    audit = json.loads(path.read_text("utf-8"))["posts"]
    caps = {k: grams(v["capture"]) for k, v in audit.items() if v.get("capture")}
    out: dict[str, Any] = {"posts_live": sum(1 for v in audit.values() if v.get("status") == "live"),
                           "posts_deleted": sum(1 for v in audit.values() if v.get("status") == "deleted"),
                           "posts_article": sum(1 for v in audit.values() if v.get("status") == "article"),
                           "systems": {}}
    rows = [it for it in items if it["platform"] == "weibo" and it["served"] is not None]
    imp = [it for it in imputed if it["platform"] == "weibo"]
    for s in SYSTEM_ORDER:
        cell = [it for it in rows if it["system"] == s]
        if not cell:
            continue
        flags = []
        for it in cell:
            text = (done.get(it["key"]) or {}).get("text") or ""
            cap = caps.get(it["post_key"])
            by_cap = bool(cap) and coverage(cap, text) >= THRESHOLD
            flags.append(bool(it["served"]) or by_cap)
        with_skips = flags + [False] * sum(1 for it in imp if it["system"] == s)
        out["systems"][s] = {"n": len(flags), "served_title_pct": _rate([bool(it["served"]) for it in cell]),
                             "served_title_or_capture_pct": _rate(flags),
                             "served_title_or_capture_pct_skips_as_not_served": _rate(with_skips)}
    return out


# --- Track K ---------------------------------------------------------------------------------------------------


def trackk_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for r in rows:
        counts[r["system"]][r["channel"]][r["outcome"]] += 1
    return {s: {c: dict(v) for c, v in chans.items()} for s, chans in counts.items()}


# --- LaTeX ---------------------------------------------------------------------------------------------------


def _pct(x: float | None) -> str:
    return "--" if x is None else f"{x:.1f}"


def _ci(c: dict[str, Any]) -> str:
    if c.get("mean") is None:
        return "--"
    return f"{c['mean']:.1f} [{c['lo']:.1f}, {c['hi']:.1f}]"


def latex_access(acc: dict[str, Any], hon: dict[str, Any], meta: dict[str, Any]) -> str:
    systems = [s for s in SYSTEM_ORDER if s in acc["overall"]]
    head = " & ".join(["Platform", "$n$"] + [SYSTEM_LABEL[s] for s in systems] + ["P1$-$P0"])
    lines = [
        "% Generated by bench/score.py — do not edit by hand.",
        "\\begin{table}[t]",
        "\\centering",
        "\\small",
        f"\\caption{{Track A access. Share of reads (\\%) in which the post's reference fingerprint appears in the text "
        f"the system obtained ($\\geq${int(THRESHOLD * 100)}\\% of its grams), by platform and system; $n$ = scored "
        f"(post, link form) items per platform. P1$-$P0 is the honesty penalty in percentage points (paired, same "
        f"items). Origin {meta.get('origin', '?')}, {meta.get('date', '')}; {meta.get('n_posts', '?')} posts, up to "
        f"four link forms each.}}",
        "\\label{tab:trackA-access}",
        "\\begin{tabular}{l r " + "r " * len(systems) + "r}",
        "\\toprule",
        head + " \\\\",
        "\\midrule",
    ]
    for p, row in acc["platforms"].items():
        ns = [row[s]["n"] + row[s].get("n_breaker_skipped", 0) for s in systems if s in row]
        n = max(ns) if ns else 0
        cells = []
        for s in systems:
            c = row.get(s)
            if c is None or c["n"] == 0:
                cells.append("--")
            else:
                mark = "$^\\dagger$" if c.get("n_breaker_skipped") else ""
                cells.append(_pct(c["served_pct"]) + mark)
        pen = hon.get(p, {}).get("diff_items", {}).get("mean")
        cells.append("--" if pen is None else f"{pen:+.1f}")
        lines.append(" & ".join([PLATFORM_LABEL.get(p, p), str(n)] + cells) + " \\\\")
    lines.append("\\midrule")
    n_all = max((acc["overall"][s].get("n_with_skips", acc["overall"][s]["n"]) for s in systems), default=0)
    pen = hon["overall"]["diff_items"]
    pen_txt = "--" if pen.get("mean") is None else f"{pen['mean']:+.1f}"
    cells = [_pct(acc["overall"][s]["served_pct_skips_as_not_served"]) for s in systems]
    lines.append(" & ".join(["All$^\\ddagger$", str(n_all)] + cells + [pen_txt]) + " \\\\")
    cis = []
    for s in systems:
        c = acc["overall"][s]["ci_skips_as_not_served"]
        cis.append("--" if c["mean"] is None else f"[{c['lo']:.1f}, {c['hi']:.1f}]")
    pen_ci = "--" if pen.get("mean") is None else f"[{pen['lo']:+.1f}, {pen['hi']:+.1f}]"
    lines.append(" & ".join(["\\quad 95\\% CI", ""] + cis + [pen_ci]) + " \\\\")
    cells = [_pct(acc["overall"][s]["served_pct"]) for s in systems]
    lines.append(" & ".join(["All, attempted only", ""] + cells + [""]) + " \\\\")
    lines += [
        "\\bottomrule",
        "\\end{tabular}",
        "",
        "\\vspace{2pt}{\\footnotesize $^\\dagger$ after six consecutive refusals (challenge, login or rate limit) the "
        "politeness circuit breaker paused this system on this platform; the cell is over the reads attempted. "
        "$^\\ddagger$ pooled over all scored items, breaker-skipped reads counted as not served.}",
        "\\end{table}",
        "",
    ]
    return "\n".join(lines)


def _tex(text: str) -> str:
    return str(text).replace("\\", "\\textbackslash{}").replace("_", "\\_").replace("%", "\\%").replace("&", "\\&")


def latex_fabrication(fab: dict[str, Any], pair: dict[str, Any], backbone: str | None = None) -> str:
    order = [s for s in ["naive_fetch", "browser_text", "reader_service", "readproof_p0", "readproof_p1",
                         "readproof_p1_nowalls"] if s in fab]
    lines = [
        "% Generated by bench/score.py — do not edit by hand.",
        "\\begin{table}[t]",
        "\\centering",
        "\\small",
        "\\caption{Answer-from-wall probe (Track A). Every read not served by fingerprint where the system obtained "
        "some text is asked ``What is this post about?'' through the system's own answer path, NOT\\_FOUND allowed. "
        "Eligible here: the page is a wall by \\texttt{walls.judge} (the harness's own verdict for ReadProof) and its "
        "text holds no trace of the post ($<$20\\% of the fingerprint's grams); a non-abstaining answer is an answer "
        "from a wall. Pages the judge did not flag (machine-translated titles on Bilibili /en/ and YouTube, Weibo "
        "summaries, walls the judge missed) are counted in the last column and in summary.json. Rates in \\%, 95\\% "
        f"bootstrap CIs. Backbone: \\texttt{{{_tex(backbone or 'n/a')}}}.}}",
        "\\label{tab:trackA-fabrication}",
        "\\begin{tabular}{l r r r r}",
        "\\toprule",
        "System & Eligible & Answered & Answer-from-wall (\\%) & Unflagged: ans./elig. \\\\",
        "\\midrule",
    ]
    for s in order:
        f = fab[s]
        uf = f.get("unflagged_no_trace") or {"answered": 0, "eligible": 0}
        lines.append(" & ".join([SYSTEM_LABEL.get(s, s), str(f["eligible"]), str(f["answered"]), _ci(f["rate"]),
                                 f"{uf['answered']}/{uf['eligible']}"]) + " \\\\")
    lines += ["\\midrule"]
    def num(x: float) -> str:
        return f"$-${abs(x):.1f}" if x < 0 else f"{x:.1f}"

    for b, p in pair.items():
        d = p["diff_items"]
        ci = "--" if d.get("mean") is None else f"{num(d['mean'])} [{num(d['lo'])}, {num(d['hi'])}]"
        lines.append(f"\\multicolumn{{5}}{{l}}{{\\footnotesize P1 $-$ {SYSTEM_LABEL.get(b, b)}, answered without the "
                     f"post per read: {ci} pp ($n$={p['n_items']})}} \\\\")
    lines += ["\\bottomrule", "\\end{tabular}", "\\end{table}", ""]
    return "\n".join(lines)


def latex_trackk(summary: dict[str, Any]) -> str:
    counts = summary.get("counts") or {}
    channels = summary.get("channels") or sorted({c for s in counts.values() for c in s})
    order = [s for s in ["closed_book", "naive_fetch", "reader_service", "browser_text", "readproof_p0", "readproof_p1",
                         "readproof_p1_nowalls", "readproof_p1_noverify", "readproof_p1_nospeech",
                         "readproof_p1_noscreen"] if s in counts]
    ch_label = {"speech": "speech", "on_screen": "on screen", "caption": "caption", "comment": "comment",
                "image": "image"}
    lines = [
        "% Generated by bench/score.py — do not edit by hand. Counts only; no codes.",
        "\\begin{table}[t]",
        "\\centering",
        "\\small",
        f"\\caption{{Track K (planted canaries), {summary.get('n_items', '?')} questions on "
        f"{summary.get('n_posts', '?')} live posts ({', '.join(summary.get('platforms', []))}), run "
        f"{summary.get('date', '')}; answers by \\texttt{{{_tex(summary.get('backbone') or 'n/a')}}}. "
        f"Each cell: correct / fabricated / abstained / wrong.}}",
        "\\label{tab:trackK-channels}",
        "\\begin{tabular}{l " + "c " * len(channels) + "}",
        "\\toprule",
        " & ".join(["System"] + [ch_label.get(c, c) for c in channels]) + " \\\\",
        "\\midrule",
    ]
    for s in order:
        cells = []
        for c in channels:
            v = counts[s].get(c, {})
            cells.append(f"{v.get('correct', 0)}/{v.get('fabricated', 0)}/{v.get('abstained', 0)}/{v.get('wrong', 0)}")
        lines.append(" & ".join([SYSTEM_LABEL.get(s, s)] + cells) + " \\\\")
    audit = summary.get("assistants") or {}
    if audit.get("assistants"):
        from bench.trackK_assistants import LABEL

        lines.append("\\midrule")
        lines.append(f"\\multicolumn{{{1 + len(channels)}}}{{l}}{{\\footnotesize Commercial assistants, guest use "
                     f"(manual audit, {audit.get('answered', 0)} answered posts; whole-post answers scored per channel; "
                     f"K04 speech = absent-channel control)}} \\\\")
        for a in audit["assistants"]:
            tot = audit["totals"].get(a, {})
            cells = []
            for c in channels:
                v = audit["counts"].get(a, {}).get(c, {})
                cells.append("--" if not v else
                             f"{v.get('correct', 0)}/{v.get('fabricated', 0)}/{v.get('abstained', 0)}/{v.get('wrong', 0)}")
            lines.append(" & ".join([f"{LABEL.get(a, a)} ({tot.get('posts_answered', 0)})"] + cells) + " \\\\")
        gone = [LABEL.get(a, a) for a in audit.get("not_usable_without_sign_in", [])]
        if gone:
            lines.append(f"\\multicolumn{{{1 + len(channels)}}}{{l}}{{\\footnotesize Not usable without sign-in: "
                         f"{', '.join(gone)}}} \\\\")
    lines += ["\\bottomrule", "\\end{tabular}", "\\end{table}", ""]
    return "\n".join(lines)


def write_public_reads(done: dict[str, dict[str, Any]], items: list[dict[str, Any]]) -> None:
    """trackA_reads.jsonl without the obtained page text (DESIGN §7: third-party content is released as IDs, URLs,
    hashes and short quotes only). The full-text file stays local (bench/results/.gitignore)."""
    scored = {it["key"]: it for it in items}
    with open(RESULTS / "trackA_reads_public.jsonl", "w", encoding="utf-8") as fh:
        for key, r in sorted(done.items()):
            row = {k: v for k, v in r.items() if k != "text"}
            meta = dict(row.get("meta") or {})
            if isinstance(meta.get("title"), str):
                meta["title"] = meta["title"][:120]
            row["meta"] = meta
            it = scored.get(key)
            if it is not None:
                row["served_fp"], row["coverage"] = it["served"], it["coverage"]
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


PAPER_SYS = {"naive_fetch": "NaiveFetch", "reader_service": "ReaderService", "browser_text": "BrowserText",
             "readproof_p1": "ReadProof"}
PAPER_PLAT = {"douyin": "Douyin", "xiaohongshu": "Xiaohongshu", "bilibili": "Bilibili", "youtube": "Youtube",
              "tiktok": "Tiktok", "weibo": "Weibo", "toutiao": "Toutiao", "x": "X", "wechat": "Wechat"}
PAPER_FORM = {"canonical": "Desktop", "mobile": "Mobile", "share_text": "ShareText"}


def _fmt_ci(c: dict[str, Any] | None) -> str | None:
    if not c or c.get("mean") is None:
        return None
    return f"{c['mean']:.1f} [{c['lo']:.1f}, {c['hi']:.1f}]"


def paper_values(summary: dict[str, Any], items: list[dict[str, Any]], imputed: list[dict[str, Any]]) -> dict[str, str]:
    """Values for paper/gen_results.py (macro name -> formatted string). Only what these runs measured; the rest
    stays TBD in the paper. Run: python3 paper/gen_results.py bench/results/paper_values.json"""
    v: dict[str, str] = {}
    acc, hon = summary["access"], summary["honesty_penalty"]
    v["NAPosts"] = str(summary["n_seed_posts"])
    v["NAForms"] = str(summary.get("n_forms") or len({(it["post_key"], it["form"]) for it in items}))
    v["ParamResidential"] = str(summary["origin"]).replace("_", "\\_")
    v["ParamCollection"] = ", ".join(summary["dates"])
    for s, name in PAPER_SYS.items():
        o = acc["overall"].get(s)
        if o and o.get("ci_skips_as_not_served", {}).get("mean") is not None:
            v[f"ResACov{name}"] = _fmt_ci(o["ci_skips_as_not_served"]) or ""
        for p, pname in PAPER_PLAT.items():
            c = acc["platforms"].get(p, {}).get(s)
            if c and c["n"]:
                v[f"ResAPlat{name}{pname}"] = f"{c['served_pct']:.1f}" + ("$^\\dagger$" if c.get("n_breaker_skipped") else "")
        f = summary["answer_from_wall"].get(s)
        if f and f["rate"].get("mean") is not None:
            v[f"ResAAFW{name}"] = _fmt_ci(f["rate"]) or ""
    # coverage by link-form role (Track A forms of this run: canonical, mobile, share text)
    from collections import defaultdict as _dd

    role_of = {it["key"]: it.get("form_role") for it in items + imputed}
    for s, name in PAPER_SYS.items():
        g = _dd(list)
        for it in items + imputed:
            if it["system"] == s and it["served"] is not None and role_of.get(it["key"]) in PAPER_FORM:
                g[PAPER_FORM[role_of[it["key"]]]].append(bool(it["served"]))
        for form, xs in g.items():
            if xs:
                v[f"ResAForm{name}{form}"] = f"{100.0 * sum(xs) / len(xs):.1f}"
    for p, pname in list(PAPER_PLAT.items()) + [("overall", "All")]:
        h = hon.get(p)
        if not h or not h.get("n_items"):
            continue
        v[f"ResHPZero{pname}"] = f"{h['b_pct']:.1f}"
        v[f"ResHPOne{pname}"] = f"{h['a_pct']:.1f}"
        v[f"ResHPen{pname}"] = f"{h['diff_items']['mean']:+.1f}"
    o = acc["overall"]
    if "naive_fetch" in o and "readproof_p1" in o:
        v["ResHeadCovNaive"] = f"{o['naive_fetch']['served_pct_skips_as_not_served']:.1f}"
        v["ResHeadCovReadProof"] = f"{o['readproof_p1']['served_pct_skips_as_not_served']:.1f}"
    afw = [summary["answer_from_wall"][b]["rate"]["mean"] for b in BASELINES
           if summary["answer_from_wall"].get(b, {}).get("rate", {}).get("mean") is not None]
    if afw:
        v["ResHeadAFWBaselines"] = f"{min(afw):.0f}--{max(afw):.0f}"
    if hon.get("overall", {}).get("diff_items", {}).get("mean") is not None:
        v["ResHeadPenalty"] = f"{hon['overall']['diff_items']['mean']:.1f}"
    v["ResHeadOrigin"] = str(summary["origin"]).replace("_", "\\_")
    reqs = _dd(int)
    for key, r in summary["reads"].items():
        reqs[key.split("|")[1]] += int(r["requests"])
    for s, name in {"naive_fetch": "NaiveFetch", "browser_text": "BrowserText", "readproof_p0": "ReadProofPZero",
                    "readproof_p1": "ReadProof"}.items():
        if reqs.get(s):
            v[f"ResCompRequests{name}"] = str(reqs[s])
            v[f"ResCompMaxRate{name}"] = "1 per 5\\,s per domain"
    k = summary.get("trackK")
    if k:
        v["NKPosts"] = str(k.get("n_posts"))
        v["NKCodes"] = str(k.get("n_items"))
        rowmap = {"speech": "Speech", "on_screen": "OnScreen", "caption": "Caption", "comment": "PinnedComment",
                  "image": "CarouselImage"}
        for s, name in PAPER_SYS.items():
            chans = (k.get("counts") or {}).get(s) or {}
            fab = abst = 0
            for ch, row in rowmap.items():
                c = chans.get(ch)
                if c:
                    n = sum(c.values())
                    v[f"ResK{row}{name}"] = f"{c.get('correct', 0)}/{n}"
            for c in chans.values():
                fab += c.get("fabricated", 0)
                abst += c.get("abstained", 0)
            if chans:
                tot = sum(sum(c.values()) for c in chans.values())
                v[f"ResKFabricated{name}"] = f"{fab}/{tot}"
                v[f"ResKAbstained{name}"] = f"{abst}/{tot}"
        audit = k.get("assistants") or {}
        if audit.get("assistants"):
            # "Commercial": the usable assistants together (whole-post answers scored per channel; counts, not codes)
            agg: dict[str, Counter] = defaultdict(Counter)
            for a in audit["assistants"]:
                for ch, c in (audit["counts"].get(a) or {}).items():
                    agg[ch].update(c)
            fab = abst = tot = 0
            for ch, row in rowmap.items():
                c = agg.get(ch)
                if c:
                    v[f"ResK{row}Commercial"] = f"{c.get('correct', 0)}/{sum(c.values())}"
                    fab += c.get("fabricated", 0)
                    abst += c.get("abstained", 0)
                    tot += sum(c.values())
            if tot:
                v["ResKFabricatedCommercial"] = f"{fab}/{tot}"
                v["ResKAbstainedCommercial"] = f"{abst}/{tot}"
    return {kk: vv for kk, vv in v.items() if vv}


# --- main ------------------------------------------------------------------------------------------------------


def probe_answers(answers: list[dict[str, Any]], done: dict[str, dict[str, Any]],
                  backbone: str | None) -> tuple[list[dict[str, Any]], str | None]:
    """The probe answers that count: made by one backbone (default: the latest probe run's) on the read that is
    now the key's latest (an answer made on a superseded read does not count for its re-read)."""
    if backbone is None:
        runs = [a for a in answers if a.get("kind") == "run" and a.get("phase") == "probe" and a.get("backbone")]
        backbone = runs[-1]["backbone"] if runs else None
    out = []
    for a in answers:
        if a.get("kind") != "probe" or a.get("key") not in done:
            continue
        if backbone is not None and a.get("backbone") != backbone:
            continue
        rid = a.get("read_run_id")
        if rid is not None and rid != done[a["key"]].get("run_id"):
            continue
        out.append(a)
    return out, backbone


def round_change(rows: list[dict[str, Any]], seeds: dict[str, dict[str, Any]], items_now: list[dict[str, Any]],
                 systems: tuple[str, ...] = ("readproof_p0", "readproof_p1")) -> dict[str, Any]:
    """ReadProof served % in the first run group (A1) against now, per platform, on the same items."""
    r1 = [r for r in rows if (r.get("run_group") or "A1") == "A1"]
    done1, _ = latest_reads(r1)
    items1 = {it["key"]: it for it in score_reads(done1, seeds) if it["system"] in systems}
    now = {it["key"]: it for it in items_now if it["system"] in systems}
    out: dict[str, Any] = {}
    for p in PLATFORM_ORDER + ["all"]:
        for s in systems:
            keys = [k for k in now if k in items1 and now[k]["system"] == s and now[k]["served"] is not None
                    and items1[k]["served"] is not None and (p == "all" or now[k]["platform"] == p)]
            if not keys:
                continue
            a = [bool(items1[k]["served"]) for k in keys]
            b = [bool(now[k]["served"]) for k in keys]
            out.setdefault(p, {})[s] = {"n": len(keys), "round1_pct": _rate(a), "round2_pct": _rate(b),
                                        "changed": sum(1 for x, y in zip(a, b) if x != y),
                                        "gained": sum(1 for x, y in zip(a, b) if y and not x),
                                        "lost": sum(1 for x, y in zip(a, b) if x and not y)}
    walls1 = Counter((it["platform"], it["system"], it["wall"] or "served") for it in items1.values())
    walls2 = Counter((it["platform"], it["system"], it["wall"] or "served") for it in now.values())
    out["walls_round1"] = {f"{p}|{s}|{w}": n for (p, s, w), n in sorted(walls1.items())}
    out["walls_round2"] = {f"{p}|{s}|{w}": n for (p, s, w), n in sorted(walls2.items())}
    return out


def run(write: bool = True, backbone: str | None = None) -> dict[str, Any]:
    seeds = load_seeds()
    rows = load_jsonl(READS)
    done, skipped = latest_reads(rows)
    items = score_reads(done, seeds)
    answers, backbone = probe_answers(load_jsonl(ANSWERS), done, backbone)
    run_meta = [r for r in rows if r.get("kind") == "run"]
    origin = next((r.get("origin") for r in reversed(rows) if r.get("origin")), "residential:unknown")
    dates = sorted({(r.get("ts") or "")[:10] for r in done.values() if r.get("ts")})
    platforms = [p for p in PLATFORM_ORDER if any(it["platform"] == p for it in items)]
    imputed = imputed_items(skipped, seeds)
    acc = access_tables(items, skipped, imputed)
    hon = honesty(items, platforms)
    vs = {b: {"overall": paired(items, "readproof_p1", b),
              "overall_skips_as_not_served": paired(items + imputed, "readproof_p1", b),
              **{p: paired(items, "readproof_p1", b, platform=p) for p in platforms}} for b in BASELINES}
    cap_keys = capture_served_keys(items, done)
    fab = fabrication(items, answers, cap_keys)
    fab_pair = fabrication_paired(items, answers, BASELINES, cap_keys)
    wb = weibo_audit(items, done, seeds, imputed)
    unusable = [{"post_key": k, "title": s["title_or_snippet"], "reason": s["fp"]["reason"]}
                for k, s in seeds.items() if not s["fp"]["usable"]]
    cap_keys_all = capture_served_keys(items, done)
    false_serves = [{"key": it["key"], "coverage": it["coverage"],
                     "class": ("post_in_other_words" if (it["coverage"] >= NO_TRACE or it["key"] in cap_keys_all)
                               else "wall_served_as_content"),
                     "text_head": ((done.get(it["key"]) or {}).get("text") or "")[:80]}
                    for it in items
                    if it["system"].startswith("readproof") and it["harness_served"] and it["served"] is False]
    reads_actual = Counter((r["platform"], r["system"]) for r in done.values() if r.get("kind") == "read")
    reads_reused = Counter((r["platform"], r["system"]) for r in done.values() if r.get("kind") == "reused")
    requests = Counter()
    seconds = Counter()
    for r in done.values():
        if r.get("kind") == "read":
            requests[(r["platform"], r["system"])] += int(r.get("requests") or 0)
            seconds[(r["platform"], r["system"])] += float(r.get("seconds") or 0)
    summary = {
        "generated_by": "bench/score.py", "threshold": THRESHOLD, "min_chars": MIN_CHARS, "bootstrap": B, "seed": SEED,
        "origin": origin, "dates": dates, "n_seed_posts": len(seeds), "n_items": len(items),
        "n_forms": len({(r["post_key"], r["form"]) for r in list(done.values()) + list(skipped.values())}),
        "n_scored": sum(1 for it in items if it["served"] is not None),
        "unusable_fingerprints": unusable,
        "access": acc, "honesty_penalty": hon, "p1_vs_baselines": vs,
        "answer_from_wall": fab, "answer_from_wall_paired": fab_pair, "probe_backbone": backbone,
        "probe_model_calls": sum(int(a.get("llm_calls") or 0) for a in answers),
        "readproof_round_change": round_change(rows, seeds, items),
        "readproof_trees": sorted({str(r.get("readproof_tree")) for r in done.values()
                                   if r.get("system", "").startswith("readproof") and r.get("readproof_tree")}),
        "harness_served_but_fingerprint_missing": false_serves,
        "crashes": [{"key": k, "error": str(r.get("error"))[:160]} for k, r in done.items()
                    if str(r.get("error") or "").startswith(("system error", "harness error"))],
        "weibo_audit": wb,
        "weak_fingerprints": [{"post_key": k, "n_grams": s["fp"]["n_grams"], "text": s["fp"]["text"][:60]}
                              for k, s in seeds.items() if s["fp"]["usable"] and s["fp"]["n_grams"] < 5],
        "reads": {f"{p}|{s}": {"actual": reads_actual[(p, s)], "reused": reads_reused[(p, s)],
                               "requests": requests[(p, s)], "seconds": round(seconds[(p, s)], 1)}
                  for (p, s) in sorted(set(reads_actual) | set(reads_reused))},
        "runs": run_meta,
    }
    ksum = json.loads(K_SUMMARY.read_text("utf-8")) if K_SUMMARY.exists() else None
    if ksum is not None:
        summary["trackK"] = ksum
    if write:
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), "utf-8")
        write_public_reads(done, items)
        TABLES.mkdir(parents=True, exist_ok=True)
        meta = {"origin": origin, "date": ", ".join(dates), "n_posts": len({it["post_key"] for it in items})}
        if items:
            (TABLES / "trackA_access.tex").write_text(latex_access(acc, hon, meta), "utf-8")
            (TABLES / "trackA_fabrication.tex").write_text(latex_fabrication(fab, fab_pair, backbone), "utf-8")
        if ksum is not None:
            (TABLES / "trackK_channels.tex").write_text(latex_trackk(ksum), "utf-8")
        if items:
            (RESULTS / "paper_values.json").write_text(
                json.dumps(paper_values(summary, items, imputed), ensure_ascii=False, indent=1), "utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--backbone", default=None, help="score probe answers of this backbone (default: the latest)")
    args = ap.parse_args(argv)
    s = run(write=not args.no_write, backbone=args.backbone)
    acc = s["access"]["overall"]
    print(json.dumps({k: (v["served_pct"], v["n"]) for k, v in acc.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
