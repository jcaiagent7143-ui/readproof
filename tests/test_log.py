"""JsonlLog / read_log / open_run (readproof/log.py)."""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pytest

from readproof.log import JsonlLog, open_run, read_log
from readproof.types import Channel, FetchResult, GateDecision


def lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_creates_parent_dirs_and_writes_one_object_per_line(tmp_path):
    path = tmp_path / "a" / "b" / "trajectory.jsonl"
    log = JsonlLog(str(path), run_id="r1")
    assert log.emit("gate", url="https://example.com/", allowed=True) == "e_000001"
    assert log.emit("fetch", status=200) == "e_000002"
    got = lines(path)  # flushed: readable while the log is still open
    assert [e["id"] for e in got] == ["e_000001", "e_000002"]
    assert [e["event"] for e in got] == ["gate", "fetch"]
    assert all(e["run_id"] == "r1" for e in got)
    assert got[0]["url"] == "https://example.com/" and got[0]["allowed"] is True
    ts = datetime.fromisoformat(got[0]["ts"])
    assert ts.utcoffset().total_seconds() == 0
    log.close()


def test_never_rewrites_and_ids_continue(tmp_path):
    path = tmp_path / "t.jsonl"
    with JsonlLog(str(path), run_id="r1") as log:
        log.emit("a")
        log.emit("b")
    before = path.read_bytes()
    with JsonlLog(str(path), run_id="r2") as log:
        assert log.emit("c") == "e_000003"
    assert path.read_bytes().startswith(before)
    assert [e["event"] for e in read_log(str(path))] == ["a", "b", "c"]


def test_emit_after_close_reopens(tmp_path):
    path = tmp_path / "t.jsonl"
    log = JsonlLog(str(path))
    log.emit("a")
    log.close()
    assert log.emit("b") == "e_000002"
    log.close()
    assert len(lines(path)) == 2


def test_bytes_are_never_written(tmp_path):
    path = tmp_path / "t.jsonl"
    secret = b"\x89PNG raw body bytes"
    with JsonlLog(str(path)) as log:
        log.emit("blob", body=secret, nested={"list": [secret, bytearray(b"x")]})
    raw = path.read_text(encoding="utf-8")
    assert "raw body bytes" not in raw
    e = lines(path)[0]
    assert e["body"] == "sha256:" + hashlib.sha256(secret).hexdigest()
    assert e["nested"]["list"][1] == "sha256:" + hashlib.sha256(b"x").hexdigest()


class Color(enum.Enum):
    RED = "red"


def test_values_that_are_not_json_are_converted(tmp_path):
    path = tmp_path / "t.jsonl"
    marker = object()
    with JsonlLog(str(path)) as log:
        log.emit("x", obj=marker, path=Path("/tmp/a"), color=Color.RED, channel=Channel.SPEECH, tags={"b", "a"},
                 inf=float("inf"), nan=float("nan"), tup=(1, 2), keys={1: "one"})
    e = lines(path)[0]  # strict JSON: json.loads would not even need to accept Infinity
    assert e["obj"] == str(marker) and e["path"] == "/tmp/a"
    assert e["color"] == "red" and e["channel"] == "speech"
    assert e["tags"] == ["a", "b"] and e["tup"] == [1, 2] and e["keys"] == {"1": "one"}
    assert e["inf"] == "inf" and e["nan"] == "nan"
    assert "Infinity" not in path.read_text() and "NaN" not in path.read_text()


def test_dataclasses_and_fetch_results_log_cleanly(tmp_path):
    path = tmp_path / "t.jsonl"
    fr = FetchResult(url="u", final_url="u", status=200, headers={}, bytes=3, sha256="0" * 64, fetched_at="t",
                     elapsed_ms=1, origin="test", user_agent="ua", body=b"abc")
    with JsonlLog(str(path)) as log:
        log.emit("fetch", **fr.to_log())
        log.emit("gate", decision=GateDecision(url="u", allowed=False, profile="P0", reason="terms"))
        log.emit("gate2", **asdict(GateDecision(url="u", allowed=True, profile="P1", reason="allowed")))
    got = lines(path)
    assert "body" not in got[0] and got[0]["status"] == 200
    assert got[1]["decision"]["reason"] == "terms"
    assert got[2]["profile"] == "P1"


def test_reserved_keys_are_not_overwritten(tmp_path):
    path = tmp_path / "t.jsonl"
    with JsonlLog(str(path), run_id="real") as log:
        log.emit("x", id="fake", ts="fake", run_id="fake")
    e = lines(path)[0]
    assert (e["id"], e["run_id"]) == ("e_000001", "real")
    assert (e["id_"], e["ts_"], e["run_id_"]) == ("fake", "fake", "fake")


def test_a_line_cut_by_a_crash(tmp_path):
    path = tmp_path / "t.jsonl"
    with JsonlLog(str(path)) as log:
        log.emit("a")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"id": "e_000002", "event": "b", "fie')  # the process died here
    assert [e["event"] for e in read_log(str(path))] == ["a"]  # a cut last line is skipped
    with JsonlLog(str(path)) as log:  # the next run ends the cut line and says so
        assert log.emit("c") == "e_000004"
    events = list(read_log(str(path)))
    assert [e["event"] for e in events] == ["a", "log_resumed", "c"]
    assert events[1]["cut_line"] == 2


def test_corruption_elsewhere_raises(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text('{"id": "e_000001", "event": "a"}\nnot json\n{"id": "e_000003", "event": "c"}\n')
    with pytest.raises(ValueError):
        list(read_log(str(path)))


def test_open_run(tmp_path):
    log = open_run(str(tmp_path / "runs"), run_id="2026-test")
    log.emit("start")
    log.close()
    path = tmp_path / "runs" / "2026-test" / "trajectory.jsonl"
    assert path.exists() and lines(path)[0]["run_id"] == "2026-test"
    other = open_run(str(tmp_path / "runs"))
    assert other.run_id != "2026-test" and Path(other.path).name == "trajectory.jsonl"
    assert Path(other.path).parent.name == other.run_id
    other.close()
