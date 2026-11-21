"""The MCP interface: four tools registered; verify and search work on records read in the session (offline)."""
import asyncio

import pytest

pytest.importorskip("mcp")

from readproof import mcp_server
from readproof.types import Channel, Record


def _rec(text, channel=Channel.SPEECH, post="douyin:1"):
    loc = {"t0": 0.0, "t1": 2.0} if channel is Channel.SPEECH else {"field": "caption"}
    return Record(record_id=Record.make_id(post, channel, loc, text), post_key=post, channel=channel, locator=loc,
                  text=text, source_sha256="0" * 64, extractor="test", fetched_at="2026-11-21T00:00:00+00:00")


def test_four_tools_are_registered():
    server = mcp_server.build_server()
    tools = asyncio.run(server.list_tools())
    assert sorted(t.name for t in tools) == ["budget", "read", "search_records", "verify"]


def test_verify_checks_the_quote_in_code(monkeypatch):
    r = _rec("今天我们测试了九种链接写法，只有三种能打开")
    monkeypatch.setattr(mcp_server, "_RECORDS", {r.record_id: r})
    assert mcp_server.tool_verify("九种链接写法，只有三种能打开", r.record_id)["verified"] is True
    assert mcp_server.tool_verify("十种链接写法", r.record_id)["verified"] is False
    assert mcp_server.tool_verify("anything", "r_missing")["verified"] is False


def test_search_ranks_records_of_the_session(monkeypatch):
    a = _rec("the speaker says the code is lighthouse orange forty two")
    b = _rec("caption about cooking pasta", channel=Channel.CAPTION)
    monkeypatch.setattr(mcp_server, "_RECORDS", {a.record_id: a, b.record_id: b})
    out = mcp_server.tool_search_records("what code does the speaker say", k=1)
    assert out and out[0]["record_id"] == a.record_id


def test_profile_must_be_p0_or_p1():
    with pytest.raises(ValueError):
        mcp_server._harness("P9")
