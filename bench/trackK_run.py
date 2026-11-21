"""Track K — planted canaries on the author's own posts, exact gold (bench/trackK.py).

Every system and ablation answers every live item (bench.trackK.items(): posts whose URL is recorded in the private
posts.json). Each post is read once per reading system (ReadProof with full extraction: speech, on-screen text,
images); the ablations reuse the P1 read. Answers are scored with bench.trackK.score into correct / fabricated /
abstained / wrong.

Privacy: post URLs, codes and answers stay under bench/data/private/ (git-ignored): the answers, the harness's
evidence store and logs, and the model cache for these prompts. The public summary (bench/results/trackK_summary.json)
holds counts per system x channel x outcome and per-post read outcomes, never a URL, a code or an answer.

Re-runnable: (item, system) pairs already answered without an error are skipped; --fresh re-reads and re-answers
everything (for the re-run once more posts are live).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench import trackK  # noqa: E402
from bench.systems import TRACK_K_SYSTEMS  # noqa: E402
from bench.systems.base import CallBudgetExceeded, CountingLLM, now_iso, readproof_commit  # noqa: E402
from bench.systems.pacing import JINA_GAP_S, DomainScheduler  # noqa: E402

PRIVATE = ROOT / "bench" / "data" / "private"
ANSWERS = PRIVATE / "results" / "trackK_answers.jsonl"
SUMMARY = ROOT / "bench" / "results" / "trackK_summary.json"
RUNS = PRIVATE / "runs" / "trackK"
LLM_CACHE = PRIVATE / "llm_cache"
LLM_LEDGER = ROOT / "runs" / "llm_calls.ledger"
ORIGIN = "residential:MY"
HARNESS_OPTIONS = {"speech": True, "on_screen": True, "images": True, "vlm": False, "min_host_interval_s": 5.0}
READERS = ("naive_fetch", "reader_service", "browser_text", "readproof_p1", "readproof_p0")


def make_systems(llm: Any, scheduler: DomainScheduler) -> dict[str, Any]:
    from bench.systems.baselines import BrowserText, ClosedBook, NaiveFetch, ReaderService
    from bench.systems.readproof_sys import Ablation, ReadProofSystem

    p1 = ReadProofSystem("readproof_p1", "P1", llm, scheduler, root=str(RUNS / "P1"), origin=ORIGIN,
                         options=HARNESS_OPTIONS)
    p0 = ReadProofSystem("readproof_p0", "P0", llm, scheduler, root=str(RUNS / "P0"), origin=ORIGIN,
                         options=HARNESS_OPTIONS)
    return {
        "closed_book": ClosedBook(llm),
        "naive_fetch": NaiveFetch(llm, scheduler, origin=ORIGIN),
        "reader_service": ReaderService(llm, scheduler, origin=ORIGIN),
        "browser_text": BrowserText(llm, scheduler, origin=ORIGIN),
        "readproof_p1": p1,
        "readproof_p0": p0,
        "readproof_p1_nowalls": Ablation("readproof_p1_nowalls", p1, "nowalls"),
        "readproof_p1_noverify": Ablation("readproof_p1_noverify", p1, "noverify"),
        "readproof_p1_nospeech": Ablation("readproof_p1_nospeech", p1, "nospeech"),
        "readproof_p1_noscreen": Ablation("readproof_p1_noscreen", p1, "noscreen"),
    }


def read_source(system: str) -> str:
    """Which read an answering system uses (ablations answer from the P1 read)."""
    return "readproof_p1" if system.startswith("readproof_p1_") else system


def load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text("utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_summary(read: Any) -> dict[str, Any]:
    """What is safe to publish about a read of a canary post: its outcome, not its text."""
    meta = read.meta or {}
    return {"system": read.system, "status": read.status, "wall": read.wall, "bytes": read.bytes,
            "visible_chars": read.visible_chars, "seconds": round(read.seconds, 2),
            "error": re.sub(r"https?://\S+", "<url>", read.error or "")[:120],
            "reader": meta.get("reader"), "harness_served": meta.get("harness_served"),
            "records_per_channel": meta.get("records_per_channel")}


def summarise(rows: list[dict[str, Any]], reads: dict[str, dict[str, Any]], items: list[dict[str, Any]]) -> dict[str, Any]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        if r.get("kind") == "answer" and not r.get("error"):
            latest[(r["item"], r["system"])] = r
    counts: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    totals: dict[str, Counter] = defaultdict(Counter)
    for (_item, system), r in latest.items():
        counts[system][r["channel"]][r["outcome"]] += 1
        totals[system][r["outcome"]] += 1
    errors = Counter(r["system"] for r in rows if r.get("kind") == "answer" and r.get("error"))
    channels = [c for c in ("speech", "on_screen", "caption", "comment", "image") if any(it["channel"] == c for it in items)]
    return {
        "generated_by": "bench/trackK_run.py", "date": time.strftime("%Y-%m-%d"), "readproof_commit": readproof_commit(),
        "origin": ORIGIN, "n_posts": len({it["post"] for it in items}), "posts": sorted({it["post"] for it in items}),
        "platforms": sorted({it["platform"] for it in items}), "n_items": len(items), "channels": channels,
        "controls": sum(1 for it in items if it.get("control")),
        "systems": [s for s in TRACK_K_SYSTEMS if s in counts],
        "counts": {s: {c: dict(v) for c, v in chans.items()} for s, chans in counts.items()},
        "totals": {s: dict(v) for s, v in totals.items()},
        "answer_errors": dict(errors),
        "reads": reads,
        "note": "Counts only. Post URLs, codes and answers are private until the answer key is published "
                "(bench/canary/COMMITMENT.md).",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Track K canary run")
    ap.add_argument("--systems", default=",".join(TRACK_K_SYSTEMS))
    ap.add_argument("--fresh", action="store_true", help="re-read and re-answer everything")
    ap.add_argument("--summary-only", action="store_true")
    args = ap.parse_args(argv)
    systems_wanted = [s for s in args.systems.split(",") if s]
    items = trackK.items(only_live=True)
    key = trackK.load_key()
    rows = load_rows(ANSWERS)
    if args.summary_only:
        reads = {}
        for r in rows:
            if r.get("kind") == "read":
                reads.setdefault(r["post"], {})[r["system"]] = r["summary"]
        SUMMARY.write_text(json.dumps(summarise(rows, reads, items), ensure_ascii=False, indent=1), "utf-8")
        return 0
    have = set() if args.fresh else {(r["item"], r["system"]) for r in rows if r.get("kind") == "answer" and not r.get("error")}
    todo = [(it, s) for it in items for s in systems_wanted if (it["id"], s) not in have]
    print(f"Track K: {len(items)} live items on {len({it['post'] for it in items})} posts; {len(todo)} answers to get",
          flush=True)
    from readproof.llm import ChatClient

    llm = CountingLLM(ChatClient(cache_dir=LLM_CACHE, max_retries=3),
                      max_uncached=int(os.environ.get("BENCH_MAX_CALLS", "700")), ledger=str(LLM_LEDGER))
    llm.patience_s = float(os.environ.get("BENCH_LLM_PATIENCE_S", "10800"))
    scheduler = DomainScheduler(5.0, gaps={"jina.ai": JINA_GAP_S})
    systems = make_systems(llm, scheduler)
    run_id = time.strftime("%Y%m%dT%H%M%S")
    reads: dict[tuple[str, str], Any] = {}
    read_rows: dict[str, dict[str, Any]] = {}
    need_reads = sorted({(it["post"], read_source(s)) for it, s in todo if read_source(s) in READERS})
    posts_url = {it["post"]: it["url"] for it in items}
    from bench.systems.netlock import network_phase

    lock = network_phase("trackK reads", wait=True)
    lock.__enter__()
    for post, rs in need_reads:
        url = posts_url[post]
        with scheduler.bind(f"trackK:{post}") as frame:
            t0 = time.monotonic()
            read = systems[rs].read(url)
        reads[(post, rs)] = read
        summ = read_summary(read)
        summ["requests"] = frame["requests"]
        read_rows.setdefault(post, {})[rs] = summ
        append(ANSWERS, {"kind": "read", "run_id": run_id, "ts": now_iso(), "post": post, "system": rs,
                         "readproof_commit": readproof_commit(), "summary": summ,
                         "text_chars": len(read.text or ""), "channels": sorted((read.meta or {}).get(
                             "records_per_channel", {}) or {})})
        print(f"read {post} {rs}: wall={read.wall} chars={read.visible_chars} {time.monotonic() - t0:.1f}s", flush=True)
    lock.__exit__(None, None, None)  # answering below sends no platform requests
    stop = False
    for it, s in todo:
        if stop:
            break
        rs = read_source(s)
        read = reads.get((it["post"], rs)) if rs in READERS else None
        sysobj = systems[s]
        t0 = time.monotonic()
        try:
            ans = sysobj.answer(it["question"], it["url"], read=read)
        except CallBudgetExceeded as exc:
            print(f"stopped: {exc}", flush=True)
            stop = True
            break
        except Exception as exc:
            append(ANSWERS, {"kind": "answer", "run_id": run_id, "ts": now_iso(), "item": it["id"], "post": it["post"],
                             "platform": it["platform"], "channel": it["channel"], "system": s,
                             "readproof_commit": readproof_commit(), "error": f"{type(exc).__name__}: {exc}"[:300]})
            continue
        err = ans.error if (ans.answer is None and ans.cause == "error") else None
        outcome = None if err else trackK.score(it, ans.answer, ans.abstained, key)
        append(ANSWERS, {"kind": "answer", "run_id": run_id, "ts": now_iso(), "item": it["id"], "post": it["post"],
                         "platform": it["platform"], "channel": it["channel"], "control": it.get("control"),
                         "system": s, "readproof_commit": readproof_commit(), "question": it["question"],
                         "answer": ans.answer, "abstained": ans.abstained, "cause": ans.cause,
                         "evidence_channel": ans.evidence_channel, "llm_calls": ans.llm_calls,
                         "seconds": round(time.monotonic() - t0, 2), "outcome": outcome, "error": err,
                         "trace": ans.trace})
        print(f"answered {it['id']:<24} {s:<24} {'ERROR' if err else outcome}", flush=True)
    for s in systems.values():
        closer = getattr(s, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
    rows = load_rows(ANSWERS)
    reads_pub: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("kind") == "read":
            reads_pub.setdefault(r["post"], {})[r["system"]] = r["summary"]
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(summarise(rows, reads_pub, items), ensure_ascii=False, indent=1), "utf-8")
    print(f"summary -> {SUMMARY}; model calls uncached {llm.uncached}, cached {llm.cached}, today {llm.today}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
