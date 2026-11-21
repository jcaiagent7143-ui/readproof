"""ReadProof over the Model Context Protocol (stdio). Any MCP client — a ReAct loop, a deep-research framework, a
coding assistant — reads social posts through the same gate, rules, wall detector, evidence store and log as the CLI.

Tools:
  read(link, profile, speech, on_screen, images) -> served post records, or a typed wall (never wall text)
  search_records(query, post_key, k, channels)    -> the most relevant stored records (BM25 + reciprocal rank)
  verify(quote, record_id)                        -> is the quote word for word in that record? (checked in code)
  budget()                                        -> what this session has spent, per tool

Counting tools (label_posts, count) arrive with the counting track. Run: `readproof-mcp` (requires the `mcp` extra).
"""

from __future__ import annotations

import os
from typing import Any

from .profiles import get as get_profile
from .types import Channel, Record

_HARNESSES: dict[str, Any] = {}
_RECORDS: dict[str, Record] = {}


def _harness(profile: str) -> Any:
    from .harness import Harness

    name = profile.upper()
    if name not in ("P0", "P1"):
        raise ValueError("profile must be P0 (strict robots) or P1 (user-initiated)")
    if name not in _HARNESSES:
        _HARNESSES[name] = Harness(get_profile(name), root=os.environ.get("READPROOF_ROOT", "runs/mcp"),
                                   origin=os.environ.get("READPROOF_ORIGIN", "unknown"))
    return _HARNESSES[name]


def _record_view(r: Record) -> dict[str, Any]:
    return {"record_id": r.record_id, "post_key": r.post_key, "channel": r.channel.value, "locator": r.locator,
            "text": r.text, "snapshot_sha256": r.source_sha256, "extractor": r.extractor}


def tool_read(link: str, profile: str = "P1", speech: bool = True, on_screen: bool = True, images: bool = True,
              max_records: int = 200) -> dict[str, Any]:
    """Read one social-media post (a URL or app share text). Returns records located by channel, or a typed wall."""
    h = _harness(profile)
    h.options.update({"speech": speech, "on_screen": on_screen, "images": images})
    res = h.read(link)
    for r in res.records:
        _RECORDS[r.record_id] = r
    return {
        "served": res.served,
        "wall": res.wall.value if res.wall else None,
        "wall_signal": res.wall_signal,
        "post_key": res.post.key if res.post else None,
        "reader": res.reader,
        "channels": sorted(c.value for c in res.channels()),
        "records": [_record_view(r) for r in res.records[:max_records]],
        "records_total": len(res.records),
        "snapshots": res.snapshots,
    }


def tool_search_records(query: str, post_key: str | None = None, k: int = 8,
                        channels: list[str] | None = None) -> list[dict[str, Any]]:
    """Rank the records read in this session for a question (per-channel BM25, fused by reciprocal rank)."""
    from .retrieve import search

    pool = [r for r in _RECORDS.values() if post_key is None or r.post_key == post_key]
    chans = [Channel(c) for c in channels] if channels else None
    return [_record_view(r) for r in search(query, pool, k=k, channels=chans)]


def tool_verify(quote: str, record_id: str) -> dict[str, Any]:
    """Check in code that `quote` appears word for word (ignoring spacing, punctuation and case) in one record."""
    from .verify import verify_quote

    rec = _RECORDS.get(record_id)
    if rec is None:
        return {"verified": False, "reason": "unknown record_id (read the post in this session first)"}
    return {"verified": verify_quote(quote, rec), "channel": rec.channel.value, "locator": rec.locator}


def tool_budget() -> dict[str, Any]:
    """Spending so far in this session, per profile and tool (US dollars and seconds)."""
    return {name: h.budget.summary() for name, h in _HARNESSES.items()}


def build_server() -> Any:
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("readproof", instructions=(
        "Read social-media posts with proof: call read(link) first; a post that cannot be read returns a typed wall "
        "(never its page text). Answer only from returned records and check quotes with verify()."))
    server.tool(name="read", description=tool_read.__doc__)(tool_read)
    server.tool(name="search_records", description=tool_search_records.__doc__)(tool_search_records)
    server.tool(name="verify", description=tool_verify.__doc__)(tool_verify)
    server.tool(name="budget", description=tool_budget.__doc__)(tool_budget)
    return server


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
