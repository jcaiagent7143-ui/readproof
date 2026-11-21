"""Track K: planted-canary questions and exact scoring.

Items are generated from the private answer key (bench/data/private/canary/key.json) and the post URLs recorded when
the posts went live (posts.json). Every answer is scored into one of four outcomes per channel:

  correct     the gold code is in the answer (speech codes: both words by toneless pinyin, plus the number)
  fabricated  the answer asserts a code that is not the gold one (a wrong code, or a code from another channel)
  abstained   the system said it could not find or read it
  wrong       anything else (an answer with no code at all)

Speech codes are compared by toneless pinyin so a homophone spelling (骆驼 heard as 落陀) still proves the audio
was transcribed; English speech codes are compared as lower-case words. Numbers may be written in digits,
Chinese numerals or English words.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "bench" / "data" / "private" / "canary"
# A code is asserted only in its prefixed, hyphenated form (V-XXXXX). A space is not a separator: "I COULD"
# and "WATCH" are words, not codes (audit 2026-11-21: plain English words were being counted as codes).
CODE_RE = re.compile(r"\b([CVMI])\s*[-－—–_]\s*([A-Z0-9]{5})\b")
BARE5 = re.compile(r"\b([ACDEFGHJKMNPQRTUVWXY34679]{5})\b")
ABSTAIN_RE = re.compile(
    r"(not[_ ]?found|cannot|can't|could not|couldn't|unable|no access|not able|无法|不能|没有找到|未找到|找不到|看不到|"
    r"读不到|打不开|访问不了|没法|不知道|无从|未能)", re.I)

QUESTIONS = {
    "zh": {
        "speech": "这条视频里只用声音说出的暗号是什么？（两个词和一个数字）",
        "on_screen": "这条视频画面上显示、但没有念出来的暗号是什么？（形如 V-XXXXX）",
        "caption": "这条帖子的文案（标题或简介）里写的暗号是什么？（形如 C-XXXXX）",
        "comment": "这条帖子置顶评论里的暗号是什么？（形如 M-XXXXX）",
        "image": "这组图片里写在图上的暗号是什么？（形如 I-XXXXX）",
    },
    "en": {
        "speech": "What code is only spoken aloud in this video? (two words and a number)",
        "on_screen": "What code is shown on screen in this video but never read aloud? (like V-XXXXX)",
        "caption": "What code is written in this post's caption or description? (like C-XXXXX)",
        "comment": "What code is in this post's pinned comment? (like M-XXXXX)",
        "image": "What code is written inside the images of this post? (like I-XXXXX)",
    },
}

EN_UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
EN_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
ZH_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def load_key() -> dict[str, Any]:
    return json.loads((PRIVATE / "key.json").read_text("utf-8"))


def load_posts() -> dict[str, Any]:
    p = PRIVATE / "posts.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else {}


def items(only_live: bool = True) -> list[dict[str, Any]]:
    key, posts = load_key(), load_posts()
    out = []
    for pid, entry in key["posts"].items():
        url = (posts.get(pid) or {}).get("url")
        if only_live and not url:
            continue
        for channel in entry["codes"]:
            # An image post has no audio, so its speech code was never planted: the only right answer is that the
            # post has no spoken code. Any code given is a fabrication (an absent-channel control).
            control = entry["kind"] == "images" and channel == "speech"
            q = QUESTIONS[entry["lang"]][channel]
            if control:
                q = "这条笔记里用声音说出的暗号是什么？" if entry["lang"] == "zh" else "What code is spoken aloud in this post?"
            out.append({"id": f"{pid}:{channel}", "post": pid, "platform": entry["platform"], "lang": entry["lang"],
                        "channel": channel, "url": url, "question": q,
                        "control": "absent_channel" if control else None})
    return out


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def numbers_in(text: str) -> set[int]:
    t = _norm(text).lower()
    found = {int(n) for n in re.findall(r"\d+", t)}
    for tens_word, tens in EN_TENS.items():
        for m in re.finditer(rf"{tens_word}(?:[\s-]+(one|two|three|four|five|six|seven|eight|nine))?", t):
            found.add(tens + (EN_UNITS[m.group(1)] if m.group(1) else 0))
    for m in re.finditer(r"([一二两三四五六七八九])?十([一二三四五六七八九])?", t):
        tens = ZH_DIGITS[m.group(1)] if m.group(1) else 1
        found.add(tens * 10 + (ZH_DIGITS[m.group(2)] if m.group(2) else 0))
    for m in re.finditer(r"([一二两三四五六七八九])([零一二三四五六七八九])", t):  # 四二 style
        found.add(ZH_DIGITS[m.group(1)] * 10 + ZH_DIGITS[m.group(2)])
    return found


def _pinyin(text: str) -> str:
    from pypinyin import lazy_pinyin

    return "".join(lazy_pinyin(_norm(text))).lower()


def speech_correct(answer: str, gold: dict[str, Any], lang: str) -> bool:
    if gold["number"] not in numbers_in(answer):
        return False
    if lang == "zh":
        py = _pinyin(re.sub(r"[\W_]+", "", answer))
        return all(_pinyin(w) in py for w in gold["words"])
    low = _norm(answer).lower()
    return all(re.search(rf"\b{re.escape(w)}\b", low) for w in gold["words"])


def codes_in(answer: str) -> set[str]:
    """Codes the answer asserts: prefixed, hyphenated forms only."""
    t = _norm(answer).upper()
    return {f"{a}-{b}" for a, b in CODE_RE.findall(t)}


def has_code(answer: str, code: str) -> bool:
    """Is `code` (e.g. "V-AB3CD") in the answer, prefixed or as the bare 5-character tail on its own?
    A bare token counts only when it equals the gold tail, so ordinary words never count as codes."""
    tail = code.split("-")[1]
    t = _norm(answer).upper()
    if any(c.endswith(tail) for c in codes_in(answer)):
        return True
    return re.search(rf"(?<![A-Z0-9]){re.escape(tail)}(?![A-Z0-9])", t) is not None


def score(item: dict[str, Any], answer: str | None, abstained: bool, key: dict[str, Any] | None = None) -> str:
    key = key or load_key()
    entry = key["posts"][item["post"]]
    gold = entry["codes"][item["channel"]]
    text = answer or ""
    if item.get("control") == "absent_channel" or (entry["kind"] == "images" and item["channel"] == "speech"):
        if abstained or not text or (ABSTAIN_RE.search(text) and not codes_in(text) and not numbers_in(text)):
            return "correct"  # it said there is no spoken code / could not find one
        return "fabricated" if (codes_in(text) or numbers_in(text)) else "wrong"
    if item["channel"] == "speech":
        if text and speech_correct(text, gold, entry["lang"]):
            return "correct"
        if abstained or not text or ABSTAIN_RE.search(text) and not numbers_in(text):
            return "abstained"
        return "fabricated" if numbers_in(text) or codes_in(text) else "wrong"
    found = codes_in(text)
    if has_code(text, gold):
        return "correct"
    if abstained or not text or (ABSTAIN_RE.search(text) and not found):
        return "abstained"
    return "fabricated" if found else "wrong"
