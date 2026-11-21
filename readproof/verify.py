"""Checking quoted words against the Record they claim to come from.

Ported from LinkDigest's `linkdigest/verify.py` (same owner, Apache-2.0 here): a model is asked for words copied
verbatim from the evidence; instructions are not guarantees, so the words are looked up here, in code, before any
Citation is marked verified. `Citation.verified` is only ever set by this module, never by a model.

Comparison ignores spacing, punctuation and case: a model drops a comma or turns 「，」 into ","; that is the same
quote. Anything else (a changed word, a traditional character for a simplified one) is not.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Mapping, Sequence

from .types import Channel, Citation, Record

_CJK = re.compile(r"[一-鿿]")

# A Markdown link's target, "](https://…)": a quote of the sentence around a link names its words, never the
# address between them.
_MD_LINK_TARGET = re.compile(r"\]\(https?://[^)\s]*\)")


def norm(text: str) -> str:
    """Compare quotes ignoring spacing and punctuation — the model may drop a comma."""
    return re.sub(r"[\W_]+", "", _MD_LINK_TARGET.sub("", text or "")).lower()


def verified(quote: str, source: str) -> bool:
    """Is `quote` in `source` (already normalised with norm)?

    LinkDigest's breakdown rule, kept as it was: an empty quote has nothing to check and passes. Citations do not
    use this — an empty quote supports nothing (see verify_quote)."""
    q = norm(quote)
    return not q or q in source


def substantial(quote: str) -> bool:
    """Long enough to be evidence: four letters or digits, a CJK character counting two.

    "AI" or "是" is in almost every post, so finding it proves nothing about the claim it is meant to support."""
    q = norm(quote)
    return len(q) + len(_CJK.findall(q)) >= 4


def locate(quote: str, passages: Sequence[str], *, across: bool = False) -> int | None:
    """The index of the passage holding `quote` word for word, else None.

    across=True lets a quote run on from one passage into the next, for transcript segments and on-screen lines,
    which are cut where the recogniser cut them rather than where a sentence ends. The index is then the passage
    the quote starts in."""
    q = norm(quote)
    if not q:
        return None
    normed = [norm(p) for p in passages]
    for i, p in enumerate(normed):
        if q in p:
            return i
    if not across:
        return None
    starts: list[int] = []
    pos = 0
    for p in normed:
        starts.append(pos)
        pos += len(p)
    at = "".join(normed).find(q)
    if at < 0:
        return None
    return bisect.bisect_right(starts, at) - 1


# A time or index label a model copied along with the words: "[12.3s] ", "image 3: ", "- ". They are our labels,
# not the post's text.
_LABEL = re.compile(r"^\s*(?:-\s*)?(?:\[\s*\d+(?:\.\d+)?\s*s?\s*\]\s*|image\s*\d+\s*[:：.]\s*)+", re.I)
_QUOTE_MARKS = "\"'“”‘’「」『』《》"
# A model asked for a short phrase sometimes pastes a paragraph. Cut, not dropped: the opening of a verbatim
# passage is still verbatim.
QUOTE_CHARS = 200


def clean_quote(raw: object) -> str:
    """The quote as it should be looked up: our own labels and wrapping quote marks removed, at most 200 chars."""
    text = _LABEL.sub("", str(raw or "")).strip().strip(_QUOTE_MARKS).strip()
    return text[:QUOTE_CHARS]


def verify_quote(quote: str, record: Record) -> bool:
    """Is `quote` (cleaned) word for word inside this one Record's text? An empty quote is never verified."""
    q = norm(clean_quote(quote))
    return bool(q) and q in norm(record.text)


# Channels whose Records are cut where a recogniser cut them, not where a sentence ends: a quote may run on into
# the next Record of the same post and channel.
_RUN_ON = (Channel.SPEECH, Channel.ON_SCREEN)


def _order_key(r: Record) -> tuple[float, str]:
    loc = r.locator or {}
    t = loc.get("t0", loc.get("t", 0.0))
    try:
        return float(t), r.record_id
    except (TypeError, ValueError):
        return 0.0, r.record_id


def verify_citations(citations: Iterable[Citation], records_by_id: Mapping[str, Record]) -> list[Citation]:
    """Set `verified` on each Citation, in code. Returns the same Citation objects, in order.

    A citation is verified when its record exists, its quote is substantial (four letters/digits, CJK counting
    two) and the quote is found word for word in that record — or, for SPEECH and ON_SCREEN, when it starts in
    that record and runs on into the next records of the same post and channel (the recogniser's cut is not the
    speaker's). A citation naming an unknown record, or a quote found only elsewhere, is not verified."""
    out = list(citations)
    by_stream: dict[tuple[str, Channel], list[Record]] | None = None
    for c in out:
        rec = records_by_id.get(c.record_id)
        quote = clean_quote(c.quote)
        if rec is None or not substantial(quote):
            c.verified = False
            continue
        if verify_quote(quote, rec):
            c.verified = True
            continue
        if rec.channel not in _RUN_ON:
            c.verified = False
            continue
        if by_stream is None:
            by_stream = {}
            for r in records_by_id.values():
                by_stream.setdefault((r.post_key, r.channel), []).append(r)
            for v in by_stream.values():
                v.sort(key=_order_key)
        stream = by_stream.get((rec.post_key, rec.channel), [])
        try:
            i = next(k for k, r in enumerate(stream) if r.record_id == rec.record_id)
        except StopIteration:
            c.verified = False
            continue
        # The quote must start in the cited record: look it up across the cited record and the ones after it.
        normed = [norm(r.text) for r in stream[i:i + 8]]
        at = "".join(normed).find(norm(quote))
        c.verified = 0 <= at < len(normed[0])
    return out
