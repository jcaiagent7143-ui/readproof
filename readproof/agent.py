"""Read–Verify–Count, the method (DESIGN.md §4, paper §5).

1. Read every link through the harness. A link whose read was not served contributes its typed wall, never text.
2. Retrieve the records most relevant to the question, per channel (char-n-gram BM25 + reciprocal-rank fusion).
3. Ask the model for an answer that cites record ids with verbatim quotes, or "not_found".
4. Verify in code: a citation counts only if its quote is a word-for-word substring of the cited record.
   No verified citation → one re-ask that names the failure → otherwise abstain with cause "unsupported".
5. If nothing was served, abstain with the wall type of the first link (typed abstention).

Counting across posts (Track C) lives in aggregate.py and reuses steps 1, 3 and 4 per post.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .types import Answer, Channel, Citation, ReadResult, Record

SYSTEM = (
    "You answer questions about social-media posts using ONLY the numbered evidence records given. "
    "Each record says which channel it came from: speech (what is said aloud), on_screen (text shown in the video), "
    "frame, image_text (text inside a picture), image, caption (title/description), comment, page. "
    "Reply with one JSON object: {\"answer\": string or null, \"citations\": [{\"record\": \"R<n>\", \"quote\": "
    "\"words copied exactly from that record\"}], \"not_found\": true|false}. "
    "Quotes must be copied character for character from the record you cite. "
    "If the records do not contain the answer, reply {\"answer\": null, \"citations\": [], \"not_found\": true}. "
    "Never guess and never use outside knowledge."
)


@dataclass
class AgentConfig:
    k: int = 12
    max_record_chars: int = 1200
    reask: int = 1
    max_tokens: int = 600


@dataclass
class Trace:
    reads: list[dict[str, Any]] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)


def _evidence_block(records: list[Record], limit: int) -> tuple[str, dict[str, Record]]:
    lines, index = [], {}
    for i, r in enumerate(records, start=1):
        tag = f"R{i}"
        index[tag] = r
        loc = ",".join(f"{k}={v}" for k, v in r.locator.items())
        text = r.text if len(r.text) <= limit else r.text[:limit] + "…"
        lines.append(f"[{tag}] ({r.channel.value}{'; ' + loc if loc else ''}) {text}")
    return "\n".join(lines), index


def _first_wall(reads: list[ReadResult]) -> str:
    for r in reads:
        if not r.served and r.wall is not None:
            return r.wall.value
    return "unknown"


class ReadVerifyAgent:
    """The method. `harness` needs .read(url) -> ReadResult; `llm` needs .chat(messages, ...) -> LLMResult."""

    name = "readproof"

    def __init__(self, harness: Any, llm: Any, config: AgentConfig | None = None) -> None:
        self.harness = harness
        self.llm = llm
        self.config = config or AgentConfig()

    def answer(self, question: str, urls: list[str], *, reads: list[ReadResult] | None = None) -> tuple[Answer, Trace]:
        from .retrieve import search
        from .verify import verify_quote

        t0 = time.monotonic()
        trace = Trace()
        reads = reads if reads is not None else [self.harness.read(u) for u in urls]
        for r in reads:
            trace.reads.append({"url": r.url, "served": r.served, "wall": r.wall.value if r.wall else None,
                                "signal": r.wall_signal, "reader": r.reader,
                                "channels": sorted(c.value for c in r.channels())})
        records = [rec for r in reads if r.served for rec in r.records]
        cost = sum(r.cost_usd for r in reads)
        if not records:
            ans = Answer(question=question, text=None, abstained=True, cause=_first_wall(reads), cost_usd=cost,
                         seconds=round(time.monotonic() - t0, 2))
            return ans, trace
        top = search(question, records, k=self.config.k)
        block, index = _evidence_block(top, self.config.max_record_chars)
        user = f"Question: {question}\n\nEvidence records:\n{block}"
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
        calls = 0
        for attempt in range(1 + self.config.reask):
            res = self.llm.chat(messages, max_tokens=self.config.max_tokens, json_mode=True, purpose="answer")
            calls += 1
            cost += res.cost_usd
            trace.prompts.append(messages[-1]["content"][:4000])
            trace.replies.append(res.text)
            try:
                reply = res.json()
            except ValueError:
                reply = {}
            if not isinstance(reply, dict):
                reply = {}
            if reply.get("not_found") or not reply.get("answer"):
                return Answer(question=question, text=None, abstained=True, cause="not_found", cost_usd=cost,
                              seconds=round(time.monotonic() - t0, 2), llm_calls=calls), trace
            cites: list[Citation] = []
            for c in reply.get("citations") or []:
                if not isinstance(c, dict):
                    continue
                rec = index.get(str(c.get("record", "")).strip().strip("[]"))
                quote = str(c.get("quote") or "")
                ok = rec is not None and verify_quote(quote, rec)
                cites.append(Citation(record_id=rec.record_id if rec else str(c.get("record")), quote=quote, verified=ok))
            verified = [c for c in cites if c.verified]
            if verified:
                rec_by_id = {r.record_id: r for r in top}
                first = rec_by_id.get(verified[0].record_id)
                return Answer(question=question, text=str(reply.get("answer")), abstained=False, cause=None,
                              citations=cites, channel=first.channel if first else None, cost_usd=cost,
                              seconds=round(time.monotonic() - t0, 2), llm_calls=calls), trace
            messages = messages + [
                {"role": "assistant", "content": res.text},
                {"role": "user", "content": "None of your quotes appears word for word in the record you cited. "
                 "Copy the exact words from one record, or reply not_found."},
            ]
        return Answer(question=question, text=None, abstained=True, cause="unsupported", cost_usd=cost,
                      seconds=round(time.monotonic() - t0, 2), llm_calls=calls), trace
