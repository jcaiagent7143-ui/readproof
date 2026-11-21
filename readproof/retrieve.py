"""Retrieval over a post's Records: BM25 inside each channel, reciprocal-rank fusion across channels.

Per channel, because channels differ in kind and size (a 40-segment transcript, one caption, nine images of
OCR): one global BM25 would let the longest channel crowd out the rest, while an answer often sits in exactly one
small channel. Each channel is ranked on its own and the lists are fused with RRF (k=60), so the best match of
every channel comes before the second-best of any.

Tokens: lowercase words for alphabetic scripts and digits, and character 2-grams for CJK runs (a run of one
character is kept as a unigram), since Chinese and Japanese are not written with spaces.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence

from .types import Channel, Record

K1 = 1.5
B = 0.75
RRF_K = 60

_CJK_RUN = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]+")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens plus CJK character bigrams."""
    tokens: list[str] = []
    text = (text or "").lower()
    pos = 0
    for m in _CJK_RUN.finditer(text):
        tokens.extend(_WORD.findall(text[pos:m.start()]))
        run = m.group(0)
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
        pos = m.end()
    tokens.extend(_WORD.findall(text[pos:]))
    return tokens


class BM25:
    """Okapi BM25 over a fixed list of documents (already tokenized)."""

    def __init__(self, docs: Sequence[Sequence[str]], *, k1: float = K1, b: float = B) -> None:
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = (sum(self.len) / len(docs)) if docs else 0.0
        df: Counter[str] = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(docs)
        self.idf = {t: math.log((n - f + 0.5) / (f + 0.5) + 1.0) for t, f in df.items()}

    def scores(self, query: Sequence[str]) -> list[float]:
        out = []
        q = Counter(query)
        for tf, length in zip(self.tf, self.len):
            s = 0.0
            norm = self.k1 * (1 - self.b + self.b * (length / self.avg if self.avg else 0.0))
            for term, qn in q.items():
                f = tf.get(term)
                if f:
                    s += self.idf.get(term, 0.0) * (f * (self.k1 + 1)) / (f + norm) * qn
            out.append(s)
        return out


def _channel(c: Channel | str) -> Channel:
    return c if isinstance(c, Channel) else Channel(c)


def rank_channel(query: str, records: Sequence[Record]) -> list[tuple[Record, float]]:
    """BM25 ranking of one channel's records; records sharing no token with the query are left out."""
    if not records:
        return []
    q = tokenize(query)
    if not q:
        return []
    scores = BM25([tokenize(r.text) for r in records]).scores(q)
    ranked = [(r, s) for r, s in zip(records, scores) if s > 0]
    ranked.sort(key=lambda x: -x[1])  # stable: ties keep the records' own order
    return ranked


def search(query: str, records: Iterable[Record], k: int = 8,
           channels: Iterable[Channel | str] | None = None) -> list[Record]:
    """The k records most relevant to `query`: per-channel BM25, fused across channels by reciprocal rank."""
    wanted = {_channel(c) for c in channels} if channels is not None else None
    by_channel: dict[Channel, list[Record]] = {}
    for r in records:
        if wanted is None or r.channel in wanted:
            by_channel.setdefault(r.channel, []).append(r)
    fused: dict[str, float] = {}
    best: dict[str, float] = {}
    order: dict[str, int] = {}
    pick: dict[str, Record] = {}
    for ch in sorted(by_channel, key=lambda c: c.value):
        for rank, (rec, score) in enumerate(rank_channel(query, by_channel[ch]), start=1):
            fused[rec.record_id] = fused.get(rec.record_id, 0.0) + 1.0 / (RRF_K + rank)
            best[rec.record_id] = max(best.get(rec.record_id, 0.0), score)
            order.setdefault(rec.record_id, len(order))
            pick[rec.record_id] = rec
    ids = sorted(fused, key=lambda i: (-fused[i], -best[i], order[i]))
    return [pick[i] for i in ids[: max(0, k)]]
