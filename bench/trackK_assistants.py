"""Track K: the commercial-assistant audit, scored with bench.trackK's own functions (no copy of its regexes).

Input (private): bench/data/private/results/trackK_assistants.jsonl, one row per (assistant, post): the assistant was
given the post's link and asked for the post's codes; `status` says whether it answered or why it could not be
used (sign-in, guest limit, a redirect to a work tenant). An answer covers the whole post, so each planted channel is
scored on it:

  speech     correct when bench.trackK.speech_correct finds the spoken code (two words by pinyin + the number)
  coded      correct when bench.trackK.has_code finds the channel's code (V- on screen, C- caption, M- pinned comment,
             I- image); "fabricated" when the answer asserts a code with that channel's prefix that is no gold code
             of the post; "abstained" when the answer says it cannot read the post; otherwise "wrong"
  K04 speech the absent-channel control (the image post has no audio, its spoken code was never planted): correct
             unless the answer invents a code for that post

Per (assistant, post) it also counts the invented codes: prefixed codes (bench.trackK.codes_in) matching no gold code
of that post. Output: scored rows (private, bench/data/private/results/trackK_assistants_scored.jsonl) and counts for
trackK_summary.json (no answers, no codes).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from bench import trackK

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "bench" / "data" / "private" / "results" / "trackK_assistants.jsonl"
SCORED = ROOT / "bench" / "data" / "private" / "results" / "trackK_assistants_scored.jsonl"
PREFIX = {"on_screen": "V", "caption": "C", "comment": "M", "image": "I"}
LABEL = {"chatgpt": "ChatGPT", "gemini": "Gemini", "perplexity": "Perplexity", "yuanbao": "Yuanbao", "qwen": "Qwen",
         "copilot": "Copilot", "deepseek": "DeepSeek", "doubao": "Doubao", "kimi": "Kimi", "grok": "Grok"}


def load(path: Path = AUDIT) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def score_row(row: dict[str, Any], key: dict[str, Any]) -> dict[str, Any]:
    """Per-channel outcomes and invented codes for one answered (assistant, post) row."""
    entry = key["posts"][row["post_id"]]
    answer = row.get("answer") or ""
    golds = entry["codes"]
    gold_tails = {g.split("-")[1] for g in golds.values() if isinstance(g, str) and "-" in g}
    invented = sorted(c for c in trackK.codes_in(answer) if c.split("-")[1] not in gold_tails)
    abstains = bool(trackK.ABSTAIN_RE.search(answer))
    channels: dict[str, str] = {}
    control = entry["kind"] == "images"
    for ch, gold in golds.items():
        if ch == "speech" and control:
            channels[ch] = "fabricated" if invented else "correct"
            continue
        if ch == "speech":
            ok = trackK.speech_correct(answer, gold, entry["lang"])
            channels[ch] = "correct" if ok else ("abstained" if abstains else "wrong")
            continue
        if trackK.has_code(answer, gold):
            channels[ch] = "correct"
        elif any(c.startswith(PREFIX.get(ch, "?") + "-") for c in invented):
            channels[ch] = "fabricated"
        elif abstains:
            channels[ch] = "abstained"
        else:
            channels[ch] = "wrong"
    planted = [ch for ch in golds if not (ch == "speech" and control)]
    return {"assistant": row["assistant"], "post_id": row["post_id"], "status": row["status"],
            "channels": channels, "control_speech": channels.get("speech") if control else None,
            "read_correctly": [ch for ch in planted if channels.get(ch) == "correct"], "planted": planted,
            "invented_codes": len(invented), "abstained": abstains and not any(channels.get(ch) == "correct" for ch in planted)}


def score_audit(rows: list[dict[str, Any]] | None = None, key: dict[str, Any] | None = None,
                write: bool = True) -> dict[str, Any]:
    """Counts per assistant (and the private scored rows when write=True)."""
    rows = load() if rows is None else rows
    key = key or trackK.load_key()
    scored = [score_row(r, key) for r in rows if r.get("status") == "answered"]
    if write and scored:
        SCORED.parent.mkdir(parents=True, exist_ok=True)
        SCORED.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in scored), "utf-8")
    counts: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    totals: dict[str, Counter] = defaultdict(Counter)
    for s in scored:
        for ch, outcome in s["channels"].items():
            counts[s["assistant"]][ch][outcome] += 1
        t = totals[s["assistant"]]
        t["posts_answered"] += 1
        t["channels_planted"] += len(s["planted"])
        t["channels_read_correctly"] += len(s["read_correctly"])
        t["invented_codes"] += s["invented_codes"]
        t["posts_with_invented_codes"] += int(s["invented_codes"] > 0)
        t["posts_abstained"] += int(s["abstained"])
        t["posts_with_any_correct"] += int(bool(s["read_correctly"]))
    asked = Counter(r["assistant"] for r in rows)
    skipped: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        if r.get("status") != "answered":
            skipped[r["assistant"]][r.get("status", "?")] += 1
    unusable = sorted(a for a in asked if a not in totals)
    return {
        "rows": len(rows), "answered": len(scored),
        "assistants": [a for a in LABEL if a in totals],
        "counts": {a: {ch: dict(v) for ch, v in chans.items()} for a, chans in counts.items()},
        "totals": {a: dict(v) for a, v in totals.items()},
        "posts_asked": dict(asked),
        "skipped": {a: dict(v) for a, v in skipped.items()},
        "not_usable_without_sign_in": unusable,
        "method": "whole-post answers scored per channel with bench.trackK.speech_correct / has_code; invented "
                  "codes = prefixed codes matching no gold code of the post; K04 speech = absent-channel control",
    }
