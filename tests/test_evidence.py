"""FileStore / record_from_json (readproof/evidence.py)."""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from readproof.evidence import CorruptBlob, FileStore, record_from_json
from readproof.types import Channel, Record


def rec(post: str = "douyin:1", text: str = "hello", channel: Channel = Channel.CAPTION, **loc) -> Record:
    locator = loc or {"field": "caption"}
    return Record(record_id=Record.make_id(post, channel, locator, text), post_key=post, channel=channel,
                  locator=locator, text=text, source_sha256="a" * 64, extractor="page:og",
                  fetched_at="2026-11-21T08:00:00+00:00")


def test_put_is_content_addressed_with_a_sidecar(tmp_path):
    store = FileStore(str(tmp_path))
    data = "<html>雪</html>".encode()
    sha = store.put(data, content_type="text/html", source_url="https://example.com/p")
    assert sha == hashlib.sha256(data).hexdigest()
    blob = tmp_path / "blobs" / sha[:2] / sha
    assert blob.read_bytes() == data and store.get(sha) == data
    meta = json.loads((tmp_path / "blobs" / sha[:2] / f"{sha}.json").read_text())
    assert meta["content_type"] == "text/html" and meta["source_url"] == "https://example.com/p"
    assert meta["bytes"] == len(data) and meta["first_seen"] and store.meta(sha) == meta


def test_put_deduplicates_and_keeps_first_seen(tmp_path):
    store = FileStore(str(tmp_path))
    sha = store.put(b"same", source_url="https://first.example/")
    blob = tmp_path / "blobs" / sha[:2] / sha
    stamp = os.stat(blob).st_mtime_ns
    assert store.put(b"same", source_url="https://second.example/") == sha
    assert os.stat(blob).st_mtime_ns == stamp
    assert store.meta(sha)["source_url"] == "https://first.example/"
    assert len(list((tmp_path / "blobs").rglob("*"))) == 3  # one shard dir, the blob, its sidecar
    assert store.put(b"") == hashlib.sha256(b"").hexdigest()


def test_get_missing_malformed_and_corrupt(tmp_path):
    store = FileStore(str(tmp_path))
    assert store.get("0" * 64) is None
    for bad in ("../../etc/passwd", "abc", "", "Z" * 64):
        assert store.get(bad) is None
    sha = store.put(b"original")
    (tmp_path / "blobs" / sha[:2] / sha).write_bytes(b"tampered")
    with pytest.raises(CorruptBlob):
        store.get(sha)
    with pytest.raises(TypeError):
        store.put("not bytes")  # type: ignore[arg-type]


def test_records_round_trip_and_index(tmp_path):
    store = FileStore(str(tmp_path))
    a, b = rec(text="one"), rec(text="two", channel=Channel.SPEECH, t0=1.0, t1=2.5)
    c = rec(post="bilibili:BV1", text="three")
    store.add_records([a, b, c])
    assert store.records_for("douyin:1") == [a, b]
    assert store.records_for("bilibili:BV1") == [c] and store.records_for("x:none") == []
    on_disk = [json.loads(x) for x in (tmp_path / "records.jsonl").read_text().splitlines()]
    assert on_disk == [a.to_json(), b.to_json(), c.to_json()]

    fresh = FileStore(str(tmp_path))  # loads lazily, on first use
    assert fresh._by_post is None
    assert fresh.records_for("douyin:1") == [a, b]
    assert record_from_json(json.loads(json.dumps(b.to_json()))) == b


def test_the_same_record_is_stored_once(tmp_path):
    store = FileStore(str(tmp_path))
    a = rec()
    store.add_records([a])
    store.add_records([a, a])
    FileStore(str(tmp_path)).add_records([a])
    assert len((tmp_path / "records.jsonl").read_text().splitlines()) == 1
    assert FileStore(str(tmp_path)).records_for("douyin:1") == [a]


def test_a_cut_last_record_line_is_ignored(tmp_path):
    store = FileStore(str(tmp_path))
    store.add_records([rec()])
    with open(tmp_path / "records.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"record_id": "r_cut", "post_ke')
    assert len(FileStore(str(tmp_path)).records_for("douyin:1")) == 1


def test_record_from_json_ignores_unknown_keys():
    d = rec().to_json() | {"added_later": 1}
    assert record_from_json(d) == rec()
