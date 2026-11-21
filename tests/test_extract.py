"""Extraction: frames (ffmpeg + dHash), speech (whisper.cpp), on-screen/image text (Apple Vision), VLM cost rule.

Media fixtures are generated at test time with ffmpeg (and macOS `say` for speech); tests that need a tool this
machine lacks are skipped, never faked.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess

import pytest

from readproof.extract import asr, frames, ocr, vlm
from readproof.extract.probe import probe
from readproof.testing import FakeNet, make_context
from readproof.types import Channel

from .reading_fakes import CJK_FONT, P1_NO_BROWSER, have, make_talking_video, render_text_png

needs_ffmpeg = pytest.mark.skipif(not have("ffmpeg", "ffprobe"), reason="ffmpeg/ffprobe not installed")
needs_vision = pytest.mark.skipif(platform.system() != "Darwin" or not os.path.exists("/usr/bin/swiftc") or not CJK_FONT,
                                  reason="Apple Vision OCR needs macOS, swiftc and a CJK font")
needs_whisper = pytest.mark.skipif(not (asr.available() and have("say")), reason="whisper-cli/model or `say` missing")


def ctx_in(tmp_path, **options):
    return make_context(P1_NO_BROWSER, FakeNet({}), workdir=str(tmp_path / "work"), **options)


@pytest.fixture(scope="module")
def three_scene_video(tmp_path_factory):
    if not have("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    path = str(tmp_path_factory.mktemp("media") / "three.mp4")
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=red:s=320x180:d=4:r=10",
                    "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=4:r=10",
                    "-f", "lavfi", "-i", "testsrc2=s=320x180:d=4:r=10",
                    "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", path], check=True, capture_output=True)
    return path


# --- frames ------------------------------------------------------------------------------------------------


def test_dhash_and_hamming():
    flat = bytes([100] * 72)
    ramp = bytes([(row * 9 + col) * 3 % 256 for row in range(8) for col in range(9)])
    assert frames.dhash(flat) == 0
    assert frames.hamming(frames.dhash(ramp), frames.dhash(ramp)) == 0
    assert frames.hamming(0b1011, 0b0001) == 2


@needs_ffmpeg
def test_frames_every_2s_plus_scenes_deduplicated(three_scene_video, tmp_path):
    got = frames.sample_frames(three_scene_video, str(tmp_path / "f"))
    times = [t for t, _ in got]
    # 0-4 s red, 4-8 s blue, 8-12 s test pattern: identical frames inside each still scene are dropped,
    # a flat red and a flat blue frame are not merged (brightness guard), and every file kept exists.
    assert times[:2] == [0.0, 4.0] and any(t >= 8.0 for t in times)
    assert all(os.path.exists(p) and p.endswith(".jpg") for _, p in got)
    assert len(os.listdir(tmp_path / "f")) == len(got)  # dropped frames and the hash thumbnails are deleted
    assert probe(got[0][1])["width"] == 768


@needs_ffmpeg
def test_frames_cap_spreads_over_the_video(three_scene_video, tmp_path):
    got = frames.sample_frames(three_scene_video, str(tmp_path / "f"), cap=2, max_hamming=-1)
    assert len(got) == 2 and got[0][0] == 0.0 and got[1][0] >= 8.0


# --- speech ------------------------------------------------------------------------------------------------


def test_whisper_json_keeps_characters_split_across_segments():
    raw = (b'{"result":{"language":"zh"},"transcription":['
           b'{"offsets":{"from":0,"to":1500},"text":"\xe4\xbb\x8a\xe5\xa4\xa9\xe4\xb8"},'
           b'{"offsets":{"from":1500,"to":3000},"text":"\xad\xe6\x96\x87"},'
           b'{"offsets":{"from":3000,"to":4000},"text":" [Music] "},'
           b'{"offsets":{"from":4000,"to":5250},"text":"\xe5\xa5\xbd"}]}')
    segs, lang = asr.parse_whisper_json(raw)
    assert lang == "zh"
    assert segs == [(0.0, 1.5, "今天"), (1.5, 3.0, "中文"), (4.0, 5.25, "好")]


def test_asr_extractor_names_the_model_file():
    assert asr.extractor_name("/x/ggml-small.bin") == "whisper.cpp:ggml-small"


@needs_whisper
@needs_ffmpeg
def test_transcribe_real_speech(tmp_path):
    video = str(tmp_path / "talk.mp4")
    make_talking_video(video, text_lines=["hello"], speech="The secret code is seven three nine.", seconds=4.0)
    ctx = ctx_in(tmp_path)
    recs = asr.transcribe(video, ctx, post_key="test:asr", source_sha256="a" * 64, fetched_at="t")
    text = " ".join(r.text for r in recs).lower()
    assert "secret code" in text and ("739" in text or "seven three nine" in text)
    assert all(r.channel is Channel.SPEECH and set(r.locator) == {"t0", "t1"} for r in recs)
    assert recs[0].extractor.startswith("whisper.cpp:") and recs[0].source_sha256 == "a" * 64
    assert ctx.budget.charges[0]["tool"] == "asr" and ctx.budget.charges[0]["usd"] == 0.0


# --- OCR ---------------------------------------------------------------------------------------------------


def test_merge_lines_reading_order_and_confidence():
    lines = [
        {"text": "right", "confidence": 1.0, "bbox": [0.6, 0.10, 0.2, 0.05]},
        {"text": "bottom", "confidence": 1.0, "bbox": [0.1, 0.50, 0.3, 0.05]},
        {"text": "left", "confidence": 1.0, "bbox": [0.1, 0.11, 0.2, 0.05]},
        {"text": "noise", "confidence": 0.2, "bbox": [0.1, 0.80, 0.2, 0.05]},
    ]
    assert ocr.merge_lines(lines) == "left right\nbottom"


def test_tesseract_tsv_lines():
    tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
           "1\t1\t0\t0\t0\t0\t0\t0\t1000\t500\t-1\t\n"
           "5\t1\t1\t1\t1\t1\t100\t50\t200\t40\t96.0\tSecret\n"
           "5\t1\t1\t1\t1\t2\t320\t50\t150\t40\t90.0\tcode\n"
           "5\t1\t1\t1\t2\t1\t100\t150\t100\t40\t-1\t \n")
    [line] = ocr.parse_tesseract_tsv(tsv)
    assert line["text"] == "Secret code" and abs(line["confidence"] - 0.93) < 1e-6
    assert line["bbox"] == [0.1, 0.1, 0.37, 0.08]


@needs_vision
@needs_ffmpeg
def test_vision_ocr_reads_chinese_and_english(tmp_path):
    img = str(tmp_path / "card.png")
    render_text_png(img, ["今天的暗号是七三九", "Secret code K7Q2 ready"])
    ctx = ctx_in(tmp_path)
    recs = ocr.ocr_images([(0, img, "b" * 64)], ctx, post_key="test:ocr", fetched_at="t")
    assert len(recs) == 1
    r = recs[0]
    assert r.channel is Channel.IMAGE_TEXT and r.locator == {"index": 0} and r.extractor == "vision:VNRecognizeText"
    assert "暗号是七三九" in r.text.replace(" ", "") and "K7Q2" in r.text
    assert r.text.index("暗号") < r.text.index("K7Q2")  # reading order: top line first


@needs_vision
@needs_ffmpeg
def test_vision_ocr_frames_skip_repeated_text(tmp_path):
    a, b = str(tmp_path / "a.png"), str(tmp_path / "b.png")
    render_text_png(a, ["第一页 Page one"])
    render_text_png(b, ["第二页 Page two"])
    ctx = ctx_in(tmp_path)
    recs = ocr.ocr_frames([(0.0, a, "1" * 64), (2.0, a, "1" * 64), (4.0, b, "2" * 64)], ctx, post_key="t:f", fetched_at="t")
    assert [r.locator for r in recs] == [{"t": 0.0}, {"t": 4.0}]
    assert all(r.channel is Channel.ON_SCREEN for r in recs)


# --- VLM cost rule -----------------------------------------------------------------------------------------


def test_vlm_refuses_paid_models_before_any_request(tmp_path, monkeypatch):
    import httpx

    def no_network(*a, **k):
        raise AssertionError("a request was built for a refused model")

    monkeypatch.setattr(httpx, "Client", no_network)
    monkeypatch.setenv("READPROOF_VLM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("READPROOF_LLM_KEY", "test-key")
    monkeypatch.delenv("READPROOF_ALLOW_PAID", raising=False)
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    ctx = ctx_in(tmp_path, vlm=True)
    with pytest.raises(vlm.PaidModelRefused, match="openai/gpt-4o-mini"):
        vlm.describe([(0, str(img), "c" * 64)], ctx, kind=Channel.IMAGE, post_key="t:v", fetched_at="t")
    # disabled unless options["vlm"] is true: nothing is checked or sent
    assert vlm.describe([(0, str(img), "c" * 64)], ctx_in(tmp_path), kind=Channel.IMAGE, post_key="t:v", fetched_at="t") == []


def test_vlm_model_rule(monkeypatch):
    monkeypatch.delenv("READPROOF_VLM_MODEL", raising=False)
    monkeypatch.delenv("READPROOF_ALLOW_PAID", raising=False)
    assert vlm.model_name() == "google/gemma-4-31b-it:free"
    vlm.check_model_allowed(vlm.model_name())
    with pytest.raises(vlm.PaidModelRefused):
        vlm.check_model_allowed("anthropic/claude-x")
    with pytest.raises(vlm.PaidModelRefused):
        vlm.check_model_allowed("google/gemma-4-31b-it:free-trial")  # must END with ":free"
    monkeypatch.setenv("READPROOF_ALLOW_PAID", "1")
    vlm.check_model_allowed("anthropic/claude-x")


def test_probe_reports_streams(three_scene_video):
    info = probe(three_scene_video)
    assert info["has_video"] and not info["has_audio"] and abs(info["duration_s"] - 12.0) < 0.2
    assert probe("/nonexistent/file.mp4")["error"]
    assert json.dumps(info)  # plain data
