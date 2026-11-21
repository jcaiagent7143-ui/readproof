"""ReadProof as a benchmark system, and its ablations, built by wrapping (readproof/ is not modified).

readproof_p1 / readproof_p0   readproof.harness.Harness with profile P1 / P0, answers by readproof.agent.ReadVerifyAgent.
                              The harness gets a stack wired exactly as readproof.net.make_stack wires it, plus the
                              study's politeness: its HttpNet transport and its browser navigations take slots from
                              the shared DomainScheduler.

Ablations of readproof_p1 (the same P1 read, a different answer step):
  nowalls    when the read is not served, the wall page's visible text goes to the model as naive_fetch's text does
             (rule 6 removed);
  noverify   the model's answer is accepted without the verbatim-quote check (and without the re-ask);
  nospeech   SPEECH records are filtered out before answering;
  noscreen   ON_SCREEN records are filtered out before answering.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import replace
from typing import Any

from readproof import walls
from readproof.types import Channel, ReadResult

from .base import BASELINE_SYSTEM, TEXT_CAP, SysAnswer, SysRead, ask_text
from .pacing import DomainScheduler, PacedGate, PacedTransport, registrable_domain


def _records_text(rr: ReadResult) -> str:
    parts = []
    for r in rr.records:
        t = (r.text or "").strip()
        if t:
            parts.append(t)
    return "\n".join(parts)


def wall_page_text(rr: ReadResult) -> tuple[str, int, str | None]:
    """(title + visible text, visible chars, url) of the most text-bearing HTML page the harness fetched.

    Used only by the nowalls ablation and the observer statistics; the harness itself never exposes it."""
    best = ("", 0, None)
    for f in rr.fetches:
        body = f.body
        if not body:
            continue
        ctype = ""
        for k, v in (f.headers or {}).items():
            if k.lower() == "content-type":
                ctype = (v or "").lower()
        if ctype and not any(t in ctype for t in ("html", "text/", "xml", "json")):
            continue
        try:
            html = body.decode("utf-8", errors="replace")
        except Exception:
            continue
        if "json" in ctype:
            title, vis = "", html[:20000]
        else:
            title, vis = walls.visible_text(html)
        shown = (title + "\n" + vis).strip() if title else vis
        if len(vis) + len(title) > best[1]:
            best = (shown, len(vis) + len(title), f.final_url or f.url)
    return best


class ReadProofSystem:
    """One Harness (one profile, one evidence root) + ReadVerifyAgent. One instance per worker thread."""

    def __init__(self, name: str, profile_name: str, llm: Any, scheduler: DomainScheduler, *, root: str,
                 origin: str = "residential:unknown", options: dict[str, Any] | None = None) -> None:
        from readproof import policy, profiles
        from readproof.agent import ReadVerifyAgent
        from readproof.harness import Harness
        from readproof.log import JsonlLog
        from readproof.net import GuardedTransport, make_stack

        self.name = name
        self.profile = profiles.get(profile_name)
        self.scheduler = scheduler
        os.makedirs(root, exist_ok=True)
        run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.log = JsonlLog(os.path.join(root, "logs", f"{run_id}.jsonl"), run_id=run_id)
        transport = PacedTransport(GuardedTransport(policy.default_resolve), scheduler)
        gate, net = make_stack(self.profile, origin=origin, log=self.log, transport=transport)
        self.gate = PacedGate(gate, scheduler)
        self.harness = Harness(self.profile, root=root, origin=origin, options=options, net=net, gate=self.gate,
                               log=self.log)
        self.llm = llm
        self.agent = ReadVerifyAgent(self.harness, llm) if llm is not None else None

    def close(self) -> None:
        self.harness.close()

    # -- read ----------------------------------------------------------------------------------------------

    def read(self, text: str) -> SysRead:
        t0 = time.monotonic()
        try:
            rr = self.harness.read(text)
        except Exception as exc:  # a harness crash is recorded as such, never as a served read
            return SysRead(self.name, text, None, None, 0, 0, "", "unknown", time.monotonic() - t0,
                           f"harness error {type(exc).__name__}: {str(exc)[:300]}", meta={"harness_crash": True})
        seconds = time.monotonic() - t0
        main = rr.fetches[-1] if rr.fetches else None
        statuses = [f.status for f in rr.fetches]
        records_text = _records_text(rr) if rr.served else ""
        if rr.served:
            visible = len(records_text)
        else:
            _, visible, _ = wall_page_text(rr)
        domains = sorted({registrable_domain(u) for f in rr.fetches for u in (f.url, f.final_url) if u})
        self.scheduler.touch(domains)
        per_channel: dict[str, int] = {}
        for r in rr.records:
            per_channel[r.channel.value] = per_channel.get(r.channel.value, 0) + 1
        gate_refusals = [{"url": g.url[:200], "reason": g.reason, "rule": g.matched_rule} for g in rr.gate if not g.allowed]
        meta = {
            "reader": rr.reader, "wall_signal": (rr.wall_signal or "")[:500],
            "post_key": rr.post.key if rr.post else None, "post_form": rr.post.form if rr.post else None,
            "records": len(rr.records), "records_per_channel": per_channel, "fetches": len(rr.fetches),
            "statuses": statuses[:20], "fetch_bytes": [f.bytes for f in rr.fetches][:20],
            "gate_refusals": gate_refusals[:10], "profile": self.profile.name, "harness_served": rr.served,
            "snapshots": len(rr.snapshots), "cost_usd": rr.cost_usd,
        }
        return SysRead(self.name, text, main.status if main else None, (main.final_url or main.url) if main else None,
                       sum(f.bytes for f in rr.fetches), visible, records_text[:TEXT_CAP],
                       None if rr.served else (rr.wall.value if rr.wall else "unknown"), seconds, None, meta=meta, raw=rr)

    # -- answer --------------------------------------------------------------------------------------------

    def answer(self, question: str, url: str, read: SysRead | None = None) -> SysAnswer:
        t0 = time.monotonic()
        read = read if read is not None else self.read(url)
        rr = read.raw
        if rr is None:
            return SysAnswer(self.name, question, url, None, True, read.wall or "unknown", None,
                             time.monotonic() - t0, 0, trace={"read_error": read.error}, error=read.error)
        return agent_answer(self.name, self.agent, question, url, rr, t0)


def agent_answer(name: str, agent: Any, question: str, url: str, rr: ReadResult, t0: float) -> SysAnswer:
    try:
        ans, trace = agent.answer(question, [url], reads=[rr])
    except Exception as exc:
        if type(exc).__name__ == "CallBudgetExceeded":
            raise
        return SysAnswer(name, question, url, None, True, "error", None, time.monotonic() - t0, 1,
                         error=f"{type(exc).__name__}: {str(exc)[:300]}")
    return SysAnswer(name, question, url, ans.text, ans.abstained, ans.cause,
                     ans.channel.value if ans.channel else None, time.monotonic() - t0, ans.llm_calls,
                     trace={"reads": trace.reads, "replies": trace.replies[:3],
                            "citations": [{"record_id": c.record_id, "quote": c.quote, "verified": c.verified}
                                          for c in ans.citations][:6]})


# --- ablations -----------------------------------------------------------------------------------------------


def filtered(rr: ReadResult, drop: set[Channel]) -> ReadResult:
    """The same read with the records of some channels removed (served stays as the harness said)."""
    return replace(rr, records=[r for r in rr.records if r.channel not in drop])


class Ablation:
    """A readproof_p1 ablation: reuses the P1 read (SysRead.raw), changes only the answer step."""

    def __init__(self, name: str, base: ReadProofSystem, kind: str) -> None:
        assert kind in ("nowalls", "noverify", "nospeech", "noscreen")
        self.name = name
        self.base = base
        self.kind = kind
        self.llm = base.llm

    def read(self, text: str) -> SysRead:
        r = self.base.read(text)
        r.system = self.name
        return r

    def answer(self, question: str, url: str, read: SysRead | None = None) -> SysAnswer:
        t0 = time.monotonic()
        read = read if read is not None else self.base.read(url)
        rr: ReadResult | None = read.raw
        if rr is None:
            return SysAnswer(self.name, question, url, None, True, read.wall or "unknown", None,
                             time.monotonic() - t0, 0, error=read.error)
        if self.kind == "nospeech":
            return agent_answer(self.name, self.base.agent, question, url, filtered(rr, {Channel.SPEECH}), t0)
        if self.kind == "noscreen":
            return agent_answer(self.name, self.base.agent, question, url, filtered(rr, {Channel.ON_SCREEN}), t0)
        if self.kind == "noverify":
            return noverify_answer(self.name, self.llm, question, url, rr, t0)
        # nowalls
        if rr.served:
            return agent_answer(self.name, self.base.agent, question, url, rr, t0)
        text, chars, page = wall_page_text(rr)
        if not text:
            return SysAnswer(self.name, question, url, None, True, rr.wall.value if rr.wall else "unknown", None,
                             time.monotonic() - t0, 0, trace={"wall_text_chars": 0})
        reply, abstained, how, calls, err = ask_text(self.llm, BASELINE_SYSTEM, question, text,
                                                     purpose=f"{self.name}:answer")
        return SysAnswer(self.name, question, url, reply, abstained, how, "wall_text", time.monotonic() - t0, calls,
                         trace={"reply": reply, "wall_text_chars": chars, "wall_page": page}, error=err)


def noverify_answer(name: str, llm: Any, question: str, url: str, rr: ReadResult, t0: float) -> SysAnswer:
    """ReadVerifyAgent's first step exactly (same prompt, so the call is shared through the cache), but the answer
    is accepted whatever its quotes say, and there is no re-ask."""
    from readproof.agent import SYSTEM, AgentConfig, _evidence_block, _first_wall
    from readproof.retrieve import search
    from readproof.verify import verify_quote

    cfg = AgentConfig()
    records = [rec for rec in rr.records] if rr.served else []
    if not records:
        return SysAnswer(name, question, url, None, True, _first_wall([rr]), None, time.monotonic() - t0, 0)
    top = search(question, records, k=cfg.k)
    block, index = _evidence_block(top, cfg.max_record_chars)
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Question: {question}\n\nEvidence records:\n{block}"}]
    try:
        res = llm.chat(messages, max_tokens=cfg.max_tokens, json_mode=True, purpose="answer")
    except Exception as exc:
        if type(exc).__name__ == "CallBudgetExceeded":
            raise
        return SysAnswer(name, question, url, None, True, "error", None, time.monotonic() - t0, 1,
                         error=f"{type(exc).__name__}: {str(exc)[:300]}")
    try:
        reply = res.json()
    except ValueError:
        reply = {}
    if not isinstance(reply, dict):
        reply = {}
    if reply.get("not_found") or not reply.get("answer"):
        return SysAnswer(name, question, url, None, True, "not_found", None, time.monotonic() - t0, 1,
                         trace={"replies": [res.text]})
    cites = []
    channel = None
    for c in reply.get("citations") or []:
        if not isinstance(c, dict):
            continue
        rec = index.get(str(c.get("record", "")).strip().strip("[]"))
        quote = str(c.get("quote") or "")
        ok = rec is not None and verify_quote(quote, rec)
        if rec is not None and channel is None:
            channel = rec.channel.value
        cites.append({"record_id": rec.record_id if rec else str(c.get("record")), "quote": quote, "verified": ok})
    return SysAnswer(name, question, url, str(reply.get("answer")), False, None, channel, time.monotonic() - t0, 1,
                     trace={"replies": [res.text], "citations": cites[:6]})
