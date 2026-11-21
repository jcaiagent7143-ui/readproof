"""Offline tests for bench/trackK_assistants.py with a synthetic answer key (no real codes)."""

from __future__ import annotations

from bench import trackK_assistants as ta

KEY = {"posts": {
    "P1": {"platform": "youtube", "kind": "video", "lang": "en",
           "codes": {"speech": {"words": ["amber", "falcon"], "number": 42}, "on_screen": "V-ACDEF",
                     "caption": "C-GHJKM", "comment": "M-NPQRT"}},
    "P4": {"platform": "xiaohongshu", "kind": "images", "lang": "zh",
           "codes": {"speech": {"words": ["骆驼", "星星"], "number": 17}, "image": "I-UVWXY", "caption": "C-34679",
                     "comment": "M-AC3D4"}},
}}

ROWS = [
    {"assistant": "chatgpt", "post_id": "P1", "status": "answered",
     "answer": "Spoken: amber falcon 42. On screen V-ACDEF. Caption C-ZZZZZ."},
    {"assistant": "gemini", "post_id": "P1", "status": "answered", "answer": "I cannot read this post."},
    {"assistant": "gemini", "post_id": "P4", "status": "answered",
     "answer": "The image says I-UVWXY and the caption C-34679."},
    {"assistant": "chatgpt", "post_id": "P4", "status": "answered", "answer": "Codes: I-UVWXY, plus V-QQQQQ."},
    {"assistant": "kimi", "post_id": "P1", "status": "skipped:login_required", "answer": ""},
]


def test_score_row_per_channel():
    first = ta.score_row(ROWS[0], KEY)
    assert first["channels"] == {"speech": "correct", "on_screen": "correct", "caption": "fabricated", "comment": "wrong"}
    assert first["invented_codes"] == 1 and first["read_correctly"] == ["speech", "on_screen"]
    refused = ta.score_row(ROWS[1], KEY)
    assert set(refused["channels"].values()) == {"abstained"} and refused["abstained"]


def test_absent_channel_control_and_invented_codes():
    clean = ta.score_row(ROWS[2], KEY)
    assert clean["control_speech"] == "correct" and clean["planted"] == ["image", "caption", "comment"]
    assert clean["channels"]["image"] == "correct" and clean["channels"]["caption"] == "correct"
    invents = ta.score_row(ROWS[3], KEY)
    assert invents["control_speech"] == "fabricated" and invents["invented_codes"] == 1


def test_score_audit_counts_and_unusable_assistants():
    out = ta.score_audit(ROWS, KEY, write=False)
    assert out["answered"] == 4 and out["assistants"] == ["chatgpt", "gemini"]
    assert out["not_usable_without_sign_in"] == ["kimi"] and out["skipped"]["kimi"] == {"skipped:login_required": 1}
    assert out["totals"]["chatgpt"]["channels_read_correctly"] == 3 and out["totals"]["chatgpt"]["invented_codes"] == 2
    assert out["counts"]["gemini"]["speech"] == {"abstained": 1, "correct": 1}
