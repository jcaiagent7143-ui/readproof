"""Track A — the access experiment (DESIGN.md §7).

For each seed post: spec = platforms.for_url(url); ref = spec.parse(url); up to four link forms from spec.forms(ref)
(FORM_PLAN: the canonical/desktop form, the mobile form, the app share-text form, and the most informative
platform-specific variant). Every form x system in TRACK_A_SYSTEMS gets one read; one JSON line per read goes to
bench/results/trackA_reads.jsonl. The runner is resumable: keys already done are skipped (skipped keys are retried).

Politeness, enforced in code (bench/systems/pacing.py):
- >= 5 s between any two requests this process sends to one registrable domain (plain HTTP per request on the wire,
  browser top-level loads per navigation; the harness's own HttpNet and browser readers included);
- one attempt per form and system (the harness's own one-retry rule aside); a form whose request is identical to an
  earlier read by the same system in this study reuses that read instead of sending it again: for the baselines the
  share-text form carries the same URL as another form, and for ReadProof every form of a post resolves offline to
  the same canonical read (rows say kind="reused" and name the read they reuse);
- a circuit breaker pauses a (platform, system) pair for the rest of the run after 6 consecutive challenge /
  rate_limited / login results; a platform whose last 12 reads (two or more systems) were all challenge or
  rate_limited is stopped and reported;
- at most 300 requests per platform per run (reader_service requests go to r.jina.ai, which fetches on its own,
  and are paced at 15 a minute instead).

Answer-from-wall probe (--phase probe): for every read NOT served by fingerprint where the system obtained some
text, the system's own answer path is asked bench.systems.PROBE_QUESTION. Results: bench/results/trackA_answers.jsonl.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import threading
import time
import traceback
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench import score  # noqa: E402
from bench.systems import PROBE_QUESTION, TRACK_A_SYSTEMS  # noqa: E402
from bench.systems.base import (BASELINE_SYSTEM, CallBudgetExceeded, CountingLLM, SysRead, ask_text,  # noqa: E402
                                first_url, now_iso, readproof_commit, sha)
from bench.systems.pacing import JINA_GAP_S, Breaker, DomainScheduler, PlatformWatch, registrable_domain  # noqa: E402

READS = ROOT / "bench" / "results" / "trackA_reads.jsonl"
ANSWERS = ROOT / "bench" / "results" / "trackA_answers.jsonl"
RUNS = ROOT / "runs" / "trackA"
RR_DIR = RUNS / "rr"
LLM_CACHE = ROOT / "runs" / "llm_cache"
LLM_LEDGER = ROOT / "runs" / "llm_calls.ledger"
ORIGIN = "residential:MY"

CAP_PER_PLATFORM = 300
CAP_MARGIN = 12  # a ReadProof read never starts unless this many requests are left under the cap (baselines: 6)
DIRECT_SYSTEMS = ("naive_fetch", "browser_text", "readproof_p1", "readproof_p0")
HARNESS_OPTIONS = {"speech": False, "on_screen": False, "images": False, "vlm": False, "keep_media": False,
                   "min_host_interval_s": 5.0}

# The four link forms per platform: canonical/desktop, mobile, app share text, the most informative variant.
FORM_PLAN: dict[str, list[str]] = {
    "douyin": ["desktop", "share_page", "share_text", "modal"],  # mobile = the iesdouyin share page; no share_param form
    # canonical = token_stripped (search indexes tokenless notes, so the token-stripped variant IS the canonical);
    # mobile = /discovery/item/ (where app share links land); share text. Three distinct forms.
    "xiaohongshu": ["token_stripped", "discovery_token_stripped", "share_text"],
    "bilibili": ["desktop", "mobile", "share_text", "share_param"],
    "youtube": ["desktop", "mobile", "share_text", "youtu_be"],
    "tiktok": ["desktop", "mobile", "share_text", "share_param"],
    "x": ["x", "mobile", "share_text", "share_param"],
    "weibo": ["desktop_status", "mobile_status", "share_text", "mobile_detail"],
    "toutiao": ["article", "mobile_i", "share_text", "group"],
    "wechat": ["long_query", "s_path", "share_text"],
}
FORM_ROLE = {
    "douyin": {"desktop": "canonical", "share_page": "mobile", "share_text": "share_text", "modal": "variant"},
    "xiaohongshu": {"token_stripped": "canonical", "discovery_token_stripped": "mobile", "share_text": "share_text",
                    "rednote_token_stripped": "variant", "seed_url": "variant"},
    "bilibili": {"desktop": "canonical", "mobile": "mobile", "share_text": "share_text", "share_param": "variant"},
    "youtube": {"desktop": "canonical", "mobile": "mobile", "share_text": "share_text", "youtu_be": "variant"},
    "tiktok": {"desktop": "canonical", "mobile": "mobile", "share_text": "share_text", "share_param": "variant"},
    "x": {"x": "canonical", "mobile": "mobile", "share_text": "share_text", "share_param": "variant"},
    "weibo": {"desktop_status": "canonical", "mobile_status": "mobile", "share_text": "share_text",
              "mobile_detail": "variant", "seed_url": "canonical"},
    "toutiao": {"article": "canonical", "mobile_i": "mobile", "share_text": "share_text", "group": "variant"},
    "wechat": {"long_query": "canonical", "s_path": "canonical", "share_text": "share_text"},
}


# --- plan --------------------------------------------------------------------------------------------------


def load_seeds() -> list[dict[str, Any]]:
    return score.load_jsonl(score.SEEDS)


def plan_forms(seed: dict[str, Any]) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    """[(form, input)] for one seed and a note on how they were made."""
    from readproof import platforms

    url = seed["url"]
    spec = platforms.for_url(url)
    note: dict[str, Any] = {"spec": spec.name if spec else None}
    ref = spec.parse(url) if spec is not None else None
    seed_form = None
    if ref is None and seed["platform"] == "xiaohongshu" and spec is not None:
        # rednote.com/en/discovery/item/<id> is not parsed by readproof (reported): canonicalise from post_id, as the
        # seed notes prescribe, and keep the URL found in the wild as the variant form.
        ref = spec.parse(f"https://www.xiaohongshu.com/explore/{seed['post_id']}")
        seed_form = ("seed_url", url)
        note["derived_from_post_id"] = True
    if ref is None:
        note["unparsed"] = True
        return [("seed_url", url)], note
    note["ref_form"] = ref.form
    available = dict(spec.forms(ref))
    wanted = FORM_PLAN.get(seed["platform"], [])
    chosen = [(f, available[f]) for f in wanted if f in available]
    if seed["platform"] == "wechat":
        chosen = [c for c in chosen if c[0] in (ref.form, "share_text")]
    if seed_form is not None:
        chosen = chosen + [seed_form]
    return chosen[:4], note


def readproof_target(text: str) -> str:
    """The read ReadProof will make for this input, computed offline exactly as the harness's resolver does it
    (extract the URL, parse it, build the canonical URL). Inputs that need a network hop to resolve get a key of
    their own, so they are never reused."""
    from readproof import canonical, platforms

    url = canonical.extract_url(text)
    if not url:
        return "input:" + text
    spec = platforms.for_url(url)
    ref = spec.parse(url) if spec is not None else None
    if ref is None:
        return "input:" + url
    # readers read spec.canonical(ref) and the embed / oEmbed addresses built from the same ref; PostRef.extra only
    # matters through canonical() (an xsec_token, a Douyin note kind), so the canonical URL identifies the read.
    return "canonical:" + spec.canonical(ref)


def target_for(system: str, text: str) -> str:
    if system.startswith("readproof"):
        return readproof_target(text)
    return "url:" + (first_url(text) or ("input:" + text))


def build_tasks(seeds: list[dict[str, Any]], platforms: set[str] | None, max_posts: int | None) -> dict[str, Any]:
    by_platform: dict[str, list[dict[str, Any]]] = defaultdict(list)
    notes = {}
    for seed in seeds:
        if platforms and seed["platform"] not in platforms:
            continue
        if max_posts is not None and len(by_platform[seed["platform"]]) >= max_posts:
            continue
        forms, note = plan_forms(seed)
        post_key = f"{seed['platform']}:{seed['post_id']}"
        notes[post_key] = note
        by_platform[seed["platform"]].append({"seed": seed, "post_key": post_key, "forms": forms})
    return {"by_platform": by_platform, "notes": notes}


def row_key(post_key: str, form: str, system: str) -> str:
    return f"{post_key}|{form}|{system}"


# --- output ------------------------------------------------------------------------------------------------


class JsonlOut:
    """Append-only JSONL with an advisory lock per line (several threads, possibly several processes)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, obj: dict[str, Any]) -> None:
        line = json.dumps(obj, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    fh.write(line)
                    fh.flush()
                    os.fsync(fh.fileno())
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def rr_path(key: str) -> Path:
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return RR_DIR / f"{h}.json"


def save_rr(key: str, read: SysRead) -> None:
    """Private, git-ignored: what the probe needs to re-run ReadVerifyAgent on this read without the network."""
    from bench.systems.readproof_sys import wall_page_text

    rr = read.raw
    if rr is None:
        return
    wall_text, wall_chars, wall_url = ("", 0, None) if rr.served else wall_page_text(rr)
    doc = {"key": key, "url": rr.url, "served": rr.served, "wall": rr.wall.value if rr.wall else None,
           "wall_signal": rr.wall_signal, "reader": rr.reader,
           "post": None if rr.post is None else {"platform": rr.post.platform, "post_id": rr.post.post_id,
                                                  "form": rr.post.form, "url": rr.post.url, "extra": rr.post.extra},
           "records": [r.to_json() for r in rr.records], "wall_text": wall_text[:30_000], "wall_chars": wall_chars,
           "wall_url": wall_url, "cost_usd": rr.cost_usd, "seconds": rr.seconds}
    RR_DIR.mkdir(parents=True, exist_ok=True)
    rr_path(key).write_text(json.dumps(doc, ensure_ascii=False, default=str), "utf-8")


def load_rr(key: str) -> Any:
    from readproof.evidence import record_from_json
    from readproof.types import PostRef, ReadResult, WallType

    p = rr_path(key)
    if not p.exists():
        return None, None
    doc = json.loads(p.read_text("utf-8"))
    post = PostRef(**doc["post"]) if doc.get("post") else None
    rr = ReadResult(url=doc["url"], post=post, served=doc["served"], wall=WallType(doc["wall"]) if doc["wall"] else None,
                    wall_signal=doc.get("wall_signal") or "", records=[record_from_json(r) for r in doc["records"]],
                    reader=doc.get("reader"))
    return rr, doc


# --- the reads phase ------------------------------------------------------------------------------------------


class Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.out = JsonlOut(READS)
        self.scheduler = DomainScheduler(args.gap, gaps={"jina.ai": JINA_GAP_S})
        self.breaker = Breaker(6)
        self.watch = PlatformWatch(12, 2)
        self.cap = args.cap
        self.seeds_fp = score.load_seeds()
        self.lock = threading.Lock()
        self.done: dict[str, dict[str, Any]] = {}
        self.reuse: dict[tuple[str, str], dict[str, Any]] = {}
        redo = bool(getattr(args, "redo_errors", False))
        for r in score.load_jsonl(READS):
            if r.get("kind") in ("read", "reused"):
                if redo and str(r.get("error") or "").startswith("system error"):
                    self.done.pop(r["key"], None)  # a crash of our own code: read again with the fixed code
                    continue
                self.done[r["key"]] = r
                if r.get("kind") == "read":
                    self.reuse[(r["system"], r["target"])] = r
        # Requests already sent to a platform today by earlier runs of this experiment count toward this run's cap.
        today = time.strftime("%Y-%m-%d", time.gmtime())
        self.precharged: Counter[str] = Counter()
        superseded = score.load_jsonl(ROOT / "bench" / "results" / "raw" / "trackA_superseded.jsonl")
        for r in list(self.done.values()) + superseded:
            if r.get("kind") == "read" and str(r.get("ts", "")).startswith(today) and r.get("system") != "reader_service":
                self.precharged[r["platform"]] += int(r.get("requests") or 0)
        for p, n in self.precharged.items():
            self.scheduler.platform_requests[p] += n
        # A restart continues today's run: replay today's actual reads through the circuit breaker in file order, so
        # a pair paused before the restart stays paused (it never gets another six refusals).
        self.replayed = 0
        for r in score.load_jsonl(READS):
            if r.get("kind") != "read" or not str(r.get("ts", "")).startswith(today):
                continue
            served, _ = self._served_fp(r["post_key"], r.get("text") or "")
            outcome = None if served else (r.get("wall") or ("empty" if not r.get("text") else "unknown"))
            self.breaker.record(r["platform"], r["system"], outcome)
            self.replayed += 1
        if self.breaker.opened:
            print(f"breaker state replayed from {self.replayed} reads: paused {sorted(self.breaker.opened)}", flush=True)
        self.stats: Counter[str] = Counter()
        self.events: list[dict[str, Any]] = []
        self.started = now_iso()
        self.stop_all = threading.Event()

    # -- one task --------------------------------------------------------------------------------------------

    def _base_row(self, post: dict[str, Any], form: str, text: str, system: str, kind: str) -> dict[str, Any]:
        seed = post["seed"]
        return {"kind": kind, "key": row_key(post["post_key"], form, system), "run_id": self.run_id, "ts": now_iso(),
                "platform": seed["platform"], "post_key": post["post_key"], "post_id": seed["post_id"],
                "seed_url": seed["url"], "language": seed.get("language"), "domain": seed.get("domain"),
                "form": form, "form_role": FORM_ROLE.get(seed["platform"], {}).get(form), "input": text,
                "system": system, "target": target_for(system, text), "origin": ORIGIN,
                "readproof_commit": readproof_commit()}

    def _served_fp(self, post_key: str, text: str) -> tuple[bool | None, float]:
        seed = self.seeds_fp.get(post_key)
        if seed is None:
            return None, 0.0
        return score.served_by_fingerprint(seed["fp"], text)

    def skip(self, post: dict[str, Any], form: str, text: str, system: str, reason: str) -> None:
        row = self._base_row(post, form, text, system, "skipped")
        row["skip_reason"] = reason
        self.out.write(row)
        self.stats[f"skipped:{reason}"] += 1

    def do(self, post: dict[str, Any], form: str, text: str, system: str, sysobj: Any) -> None:
        platform = post["seed"]["platform"]
        key = row_key(post["post_key"], form, system)
        if key in self.done or self.stop_all.is_set():
            return
        target = target_for(system, text)
        with self.lock:
            original = self.reuse.get((system, target))
        if original is not None:
            row = self._base_row(post, form, text, system, "reused")
            for f in ("status", "final_url", "bytes", "visible_chars", "text", "text_sha256", "wall", "error", "meta"):
                row[f] = original.get(f)
            row.update({"seconds": 0.0, "requests": 0, "reused_from": original["key"]})
            row["served_fp"], row["coverage"] = self._served_fp(post["post_key"], row.get("text") or "")
            self.out.write(row)
            with self.lock:
                self.done[key] = row
            self.stats["reused"] += 1
            return
        if self.watch.is_stopped(platform) and system != "reader_service":
            return self.skip(post, form, text, system, "platform_stopped")
        if self.breaker.is_open(platform, system):
            return self.skip(post, form, text, system, "circuit_open")
        margin = CAP_MARGIN if system.startswith("readproof") else 6
        if system != "reader_service" and self.scheduler.requests_for(platform) + margin > self.cap:
            return self.skip(post, form, text, system, "request_cap")
        from bench.systems.baselines import CHROMIUM_SLOTS

        t0 = time.monotonic()
        with self.scheduler.bind(platform) as frame:
            try:
                if system.startswith("readproof"):
                    with CHROMIUM_SLOTS:
                        read = sysobj.read(text)
                else:
                    read = sysobj.read(text)
            except Exception as exc:
                read = SysRead(system, text, None, None, 0, 0, "", "unknown", time.monotonic() - t0,
                               f"system error {type(exc).__name__}: {str(exc)[:300]}",
                               meta={"traceback": traceback.format_exc()[-1500:]})
        sent = frame["requests"]
        row = self._base_row(post, form, text, system, "read")
        row.update(read.to_json())
        row.update({"requests": sent, "domains": dict(frame["domains"]), "text_sha256": sha(read.text),
                    "reused_from": None})
        served, cov = self._served_fp(post["post_key"], read.text)
        row["served_fp"], row["coverage"] = served, round(cov, 3)
        if system.startswith("readproof"):
            try:
                save_rr(key, read)
            except Exception as exc:
                row["rr_error"] = f"{type(exc).__name__}: {exc}"[:200]
        self.out.write(row)
        with self.lock:
            self.done[key] = row
            self.reuse[(system, target)] = row
        self.stats["read"] += 1
        self.stats[f"requests:{platform}"] += sent
        outcome = None if served else (read.wall or ("empty" if not read.text else "unknown"))
        if self.breaker.record(platform, system, outcome):
            self.events.append({"ts": now_iso(), "event": "circuit_open", "platform": platform, "system": system,
                                "last": outcome})
            print(f"[breaker] {platform}/{system} paused after 6 consecutive {outcome}", flush=True)
        if system != "reader_service" and self.watch.record(platform, system, outcome, sent):
            self.events.append({"ts": now_iso(), "event": "platform_stopped", "platform": platform,
                                "why": self.watch.stopped[platform]})
            print(f"[stop] {platform}: {self.watch.stopped[platform]}", flush=True)
        read.raw = None

    # -- workers ----------------------------------------------------------------------------------------------

    def make_systems(self, platform: str, llm: Any) -> dict[str, Any]:
        from bench.systems.baselines import BrowserText, NaiveFetch
        from bench.systems.readproof_sys import ReadProofSystem

        systems: dict[str, Any] = {}
        if "naive_fetch" in self.args.systems:
            systems["naive_fetch"] = NaiveFetch(llm, self.scheduler, origin=ORIGIN)
        if "browser_text" in self.args.systems:
            systems["browser_text"] = BrowserText(llm, self.scheduler, origin=ORIGIN)
        for name, prof in (("readproof_p1", "P1"), ("readproof_p0", "P0")):
            if name in self.args.systems:
                systems[name] = ReadProofSystem(name, prof, llm, self.scheduler, root=str(RUNS / platform / prof),
                                                origin=ORIGIN, options=HARNESS_OPTIONS)
        return systems

    def platform_worker(self, platform: str, posts: list[dict[str, Any]]) -> None:
        systems = self.make_systems(platform, None)
        order = [s for s in DIRECT_SYSTEMS if s in systems]
        n = 0
        try:
            for i, post in enumerate(posts):
                for j, (form, text) in enumerate(post["forms"]):
                    k = (i + j) % max(1, len(order))
                    for system in order[k:] + order[:k]:  # rotate the order: no system always goes first
                        if self.stop_all.is_set():
                            return
                        self.do(post, form, text, system, systems[system])
                        n += 1
                print(f"[{platform}] post {i + 1}/{len(posts)} done; requests={self.scheduler.requests_for(platform)}",
                      flush=True)
        except Exception:
            print(f"[{platform}] worker crashed:\n{traceback.format_exc()}", flush=True)
            self.events.append({"ts": now_iso(), "event": "worker_crash", "platform": platform,
                                "error": traceback.format_exc()[-800:]})
        finally:
            for s in systems.values():
                closer = getattr(s, "close", None)
                if callable(closer):
                    try:
                        closer()
                    except Exception:
                        pass

    def jina_worker(self, queue: list[tuple[dict[str, Any], str, str]], idx: int, step: int) -> None:
        from bench.systems.baselines import ReaderService

        svc = ReaderService(None, self.scheduler, origin=ORIGIN)
        for t in range(idx, len(queue), step):
            if self.stop_all.is_set():
                return
            post, form, text = queue[t]
            try:
                self.do(post, form, text, "reader_service", svc)
            except Exception:
                print(f"[jina] task crashed:\n{traceback.format_exc()}", flush=True)
            if (t // step) % 25 == 0:
                print(f"[jina{idx}] {t + 1}/{len(queue)}", flush=True)

    def run(self, plan: dict[str, Any]) -> None:
        by_platform = plan["by_platform"]
        threads = []
        if any(s in self.args.systems for s in DIRECT_SYSTEMS):
            for platform, posts in by_platform.items():
                th = threading.Thread(target=self.platform_worker, args=(platform, posts), name=f"w-{platform}",
                                      daemon=True)
                threads.append(th)
        if "reader_service" in self.args.systems:
            # round-robin across platforms so the service's load is spread out over the run
            queues = [[(p, f, t) for p in posts for f, t in p["forms"]] for posts in by_platform.values()]
            queue = []
            while any(queues):
                for q in queues:
                    if q:
                        queue.append(q.pop(0))
            for idx in range(self.args.jina_workers):
                threads.append(threading.Thread(target=self.jina_worker, args=(queue, idx, self.args.jina_workers),
                                                name=f"jina-{idx}", daemon=True))
        t0 = time.monotonic()
        for th in threads:
            th.start()
        try:
            while any(th.is_alive() for th in threads):
                time.sleep(2.0)
        except KeyboardInterrupt:
            print("interrupted: finishing the reads in flight", flush=True)
            self.stop_all.set()
            for th in threads:
                th.join(timeout=120)
        self.out.write({"kind": "run", "phase": "reads", "run_id": self.run_id, "started": self.started,
                        "readproof_commit": readproof_commit(),
                        "ended": now_iso(), "seconds": round(time.monotonic() - t0, 1), "origin": ORIGIN,
                        "gap_s": self.args.gap, "cap_per_platform": self.cap, "systems": list(self.args.systems),
                        "platform_requests": dict(self.scheduler.platform_requests),
                        "precharged_from_earlier_runs_today": dict(self.precharged),
                        "domain_requests": dict(self.scheduler.domain_requests), "waited_s": round(self.scheduler.waited_s),
                        "stats": dict(self.stats), "events": self.events,
                        "breaker_open": [f"{p}/{s}:{w}" for (p, s), w in self.breaker.opened.items()],
                        "stopped": dict(self.watch.stopped)})


# --- the probe phase -----------------------------------------------------------------------------------------


def make_llm() -> CountingLLM:
    from readproof.llm import ChatClient

    llm = CountingLLM(ChatClient(cache_dir=LLM_CACHE, max_retries=3),
                      max_uncached=int(os.environ.get("BENCH_MAX_CALLS", "700")), ledger=str(LLM_LEDGER))
    llm.patience_s = float(os.environ.get("BENCH_LLM_PATIENCE_S", "10800"))
    return llm


def probe(args: argparse.Namespace) -> None:
    from readproof.agent import ReadVerifyAgent

    seeds = score.load_seeds()
    done, _ = score.latest_reads(score.load_jsonl(READS))
    cap_keys = score.capture_served_keys(score.score_reads(done, seeds), done)  # Weibo reads that hold the post
    have = {(a["key"], a["answer_system"]) for a in score.load_jsonl(ANSWERS) if not a.get("error")}
    out = JsonlOut(ANSWERS)
    llm = make_llm()
    agent = ReadVerifyAgent(None, llm)
    jobs = []
    for key, r in done.items():
        seed = seeds.get(r["post_key"])
        if seed is None or not seed["fp"]["usable"]:
            continue
        served, _ = score.served_by_fingerprint(seed["fp"], r.get("text") or "")
        if served or key in cap_keys:
            continue
        system = r["system"]
        src_key = r.get("reused_from") or key
        if system.startswith("readproof"):
            if int(r.get("bytes") or 0) <= 0 and not (r.get("meta") or {}).get("harness_served"):
                continue  # nothing was obtained (e.g. refused by policy before any request)
            jobs.append((key, r, system, src_key))
            if args.nowalls and system == "readproof_p1":
                jobs.append((key, r, "readproof_p1_nowalls", src_key))
        else:
            if not (r.get("text") or "").strip():
                continue
            jobs.append((key, r, system, src_key))
    jobs = [j for j in jobs if (j[0], j[2]) not in have]
    # ReadProof jobs first: a read the harness did not serve abstains without a model call, so the typed-abstention
    # check never waits on the free endpoint; then the baselines, distinct texts before their repeats.
    seen_text: set[str] = set()

    def order(job: tuple[str, dict[str, Any], str, str]) -> tuple[int, int]:
        _key, r, answer_system, _src = job
        if answer_system in ("readproof_p1", "readproof_p0"):
            return (1, 0) if (r.get("meta") or {}).get("harness_served") else (0, 0)
        seed = seeds.get(r["post_key"])
        _, cov = score.served_by_fingerprint(seed["fp"], r.get("text") or "") if seed else (None, 0.0)
        if cov >= score.NO_TRACE:
            return (6, 0)  # partial coverage: reported apart, asked last
        if r.get("wall") is None:
            return (5, 0)  # a page the judge did not flag (often the post in translation): reported apart
        t = sha(r.get("text") or "")
        first = t not in seen_text
        seen_text.add(t)
        return (2 if first else 3, 0)  # headline: flagged walls, distinct texts first

    jobs = sorted(jobs, key=order)
    if args.max_probe is not None:
        jobs = jobs[: args.max_probe]
    print(f"probe: {len(jobs)} answers to get ({llm.today} uncached calls already today)", flush=True)
    lock = threading.Lock()
    stop = threading.Event()

    def one(job: tuple[str, dict[str, Any], str, str]) -> None:
        if stop.is_set():
            return
        key, r, answer_system, src_key = job
        t0 = time.monotonic()
        base = {"kind": "probe", "key": key, "answer_system": answer_system, "system": r["system"],
                "platform": r["platform"], "post_key": r["post_key"], "form": r["form"], "ts": now_iso(),
                "question": PROBE_QUESTION, "read_wall": r.get("wall"), "readproof_commit": readproof_commit(),
                "read_commit": r.get("readproof_commit"),
                "harness_served": (r.get("meta") or {}).get("harness_served"), "reused_from": r.get("reused_from")}
        try:
            if answer_system in ("readproof_p1", "readproof_p0"):
                rr, _doc = load_rr(src_key)
                if rr is None:
                    return
                ans, trace = agent.answer(PROBE_QUESTION, [r["input"]], reads=[rr])
                row = {**base, "answer": ans.text, "abstained": ans.abstained, "cause": ans.cause,
                       "evidence_channel": ans.channel.value if ans.channel else None, "llm_calls": ans.llm_calls,
                       "seconds": round(time.monotonic() - t0, 2), "error": None,
                       "citations_verified": sum(1 for c in ans.citations if c.verified)}
            elif answer_system == "readproof_p1_nowalls":
                rr, doc = load_rr(src_key)
                if rr is None:
                    return
                if rr.served:
                    ans, _ = agent.answer(PROBE_QUESTION, [r["input"]], reads=[rr])
                    row = {**base, "answer": ans.text, "abstained": ans.abstained, "cause": ans.cause,
                           "llm_calls": ans.llm_calls, "seconds": round(time.monotonic() - t0, 2), "error": None}
                elif not (doc.get("wall_text") or "").strip():
                    row = {**base, "answer": None, "abstained": True, "cause": "no_wall_text", "llm_calls": 0,
                           "seconds": 0.0, "error": None}
                else:
                    reply, abstained, how, calls, err = ask_text(llm, BASELINE_SYSTEM, PROBE_QUESTION, doc["wall_text"],
                                                                 purpose="readproof_p1_nowalls:probe")
                    row = {**base, "answer": reply, "abstained": abstained, "cause": how if err is None else "error",
                           "llm_calls": calls, "seconds": round(time.monotonic() - t0, 2), "error": err,
                           "wall_text_chars": len(doc["wall_text"])}
            else:
                reply, abstained, how, calls, err = ask_text(llm, BASELINE_SYSTEM, PROBE_QUESTION, r.get("text") or "",
                                                             purpose=f"{answer_system}:probe")
                row = {**base, "answer": reply, "abstained": abstained, "cause": how if err is None else "error",
                       "llm_calls": calls, "seconds": round(time.monotonic() - t0, 2), "error": err,
                       "text_chars": len(r.get("text") or "")}
        except CallBudgetExceeded as exc:
            print(f"probe stopped: {exc}", flush=True)
            stop.set()
            return
        out.write(row)
        with lock:
            n_done[0] += 1
            if n_done[0] % 25 == 0:
                print(f"probe: {n_done[0]}/{len(jobs)} (uncached today {llm.today})", flush=True)

    n_done = [0]
    with ThreadPoolExecutor(max_workers=args.llm_workers) as pool:
        list(pool.map(one, jobs))
    out.write({"kind": "run", "phase": "probe", "ts": now_iso(), "readproof_commit": readproof_commit(),
               "answers": n_done[0], "uncached_calls": llm.uncached,
               "cached_calls": llm.cached, "today": llm.today, "stopped_by_budget": stop.is_set()})
    print(f"probe done: {n_done[0]} answers, {llm.uncached} uncached / {llm.cached} cached calls", flush=True)


# --- main ----------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Track A access experiment")
    ap.add_argument("--phase", choices=["plan", "reads", "probe", "all"], default="plan")
    ap.add_argument("--platforms", default="", help="comma-separated subset")
    ap.add_argument("--systems", default=",".join(TRACK_A_SYSTEMS))
    ap.add_argument("--max-posts", type=int, default=None, help="per platform (smoke tests)")
    ap.add_argument("--cap", type=int, default=CAP_PER_PLATFORM)
    ap.add_argument("--gap", type=float, default=5.0)
    ap.add_argument("--jina-workers", type=int, default=2)
    ap.add_argument("--llm-workers", type=int, default=2)
    ap.add_argument("--max-probe", type=int, default=None)
    ap.add_argument("--slots", type=int, default=4, help="Chromium instances at once, all workers together")
    ap.add_argument("--redo-errors", action="store_true", help="re-read rows our own code crashed on")
    ap.add_argument("--nowalls", action="store_true", help="also probe the readproof_p1 -walls ablation")
    args = ap.parse_args(argv)
    args.systems = tuple(s for s in args.systems.split(",") if s)
    if args.gap < 5.0:
        ap.error("the study's politeness rule is at least 5 s between requests to one registrable domain")
    platforms = {p for p in args.platforms.split(",") if p} or None
    plan = build_tasks(load_seeds(), platforms, args.max_posts)
    if args.phase == "plan":
        rows = 0
        for p, posts in plan["by_platform"].items():
            forms = Counter(f for post in posts for f, _ in post["forms"])
            n_forms = sum(len(post["forms"]) for post in posts)
            targets = {s: len({target_for(s, t) for post in posts for _, t in post["forms"]}) for s in TRACK_A_SYSTEMS}
            rows += n_forms * len(args.systems)
            print(f"{p}: {len(posts)} posts, {n_forms} forms {dict(forms)}; distinct reads {targets}")
        unparsed = [k for k, n in plan["notes"].items() if n.get("unparsed") or n.get("derived_from_post_id")]
        print(f"rows: {rows}; seeds readproof does not parse: {unparsed}")
        return 0
    if args.phase in ("reads", "all"):
        from bench.systems import baselines

        baselines.CHROMIUM_SLOTS = threading.BoundedSemaphore(max(1, args.slots))
        from bench.systems.netlock import network_phase

        with network_phase("trackA reads"):
            Runner(args).run(plan)
    if args.phase in ("probe", "all"):
        probe(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
