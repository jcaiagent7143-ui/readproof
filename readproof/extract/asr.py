"""Local speech recognition with whisper.cpp: media -> 16 kHz mono WAV -> timed SPEECH Records.

Local on purpose: the benchmark's cost column for speech is wall time on the reader's own machine, not a vendor
bill, and nothing a post says is sent to a third party to be transcribed. Records carry {"t0","t1"} in seconds
from whisper's own segment offsets, and the extractor names the model file ("whisper.cpp:ggml-small"), so a
gold label can say which recogniser heard what.

whisper.cpp can cut a segment in the middle of a multi-byte character (byte-level BPE); the bytes are carried over
to the next segment instead of being replaced, so Chinese text survives segment boundaries intact.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any

from ..interfaces import Context
from ..types import Channel, Record
from .probe import FFMPEG

WHISPER_BIN = os.environ.get("READPROOF_WHISPER_BIN") or "/opt/homebrew/bin/whisper-cli"
DEFAULT_MODEL = "/Users/loongnianchew/.cache/hyperframes/whisper/models/ggml-small.bin"


class AsrUnavailable(RuntimeError):
    """whisper-cli or its model is not installed here."""


def model_path() -> str:
    return os.environ.get("READPROOF_WHISPER_MODEL") or DEFAULT_MODEL


def extractor_name(model: str) -> str:
    stem = os.path.basename(model)
    return "whisper.cpp:" + (stem[:-4] if stem.endswith(".bin") else stem)


def available() -> bool:
    return bool(shutil.which(WHISPER_BIN) or os.path.exists(WHISPER_BIN)) and os.path.exists(model_path())


def to_wav(media: str, wav: str, *, max_s: float | None = None, timeout: float = 600.0) -> None:
    """ffmpeg: the first audio stream, mono, 16 kHz, 16-bit PCM — what whisper.cpp expects."""
    cmd = [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", media]
    if max_s:
        cmd += ["-t", f"{float(max_s):.3f}"]
    cmd += ["-vn", "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    if proc.returncode != 0 or not os.path.exists(wav):
        raise RuntimeError("ffmpeg could not extract audio: " + proc.stderr.decode("utf-8", "replace").strip()[:300])


def _split_incomplete_utf8(data: bytes) -> tuple[bytes, bytes]:
    """(complete prefix, trailing bytes of a character that continues in the next segment)."""
    for back in range(1, min(4, len(data)) + 1):
        b = data[-back]
        if b & 0xC0 == 0x80:  # continuation byte: keep looking for the lead byte
            continue
        need = 2 if b & 0xE0 == 0xC0 else 3 if b & 0xF0 == 0xE0 else 4 if b & 0xF8 == 0xF0 else 1
        return (data[:-back], data[-back:]) if need > back else (data, b"")
    return data, b""


# Annotations whisper writes for non-speech: "[Music]", "(音乐)", "[BLANK_AUDIO]", "♪♪".
_ANNOTATION = re.compile(r"^\s*(?:[\[\(（【][^\]\)）】]{0,24}[\]\)）】]\s*)+$|^[\s♪♫]+$")


def parse_whisper_json(raw: bytes) -> tuple[list[tuple[float, float, str]], str]:
    """whisper-cli -oj output -> ([(t0, t1, text)], detected language). Bytes in, so split characters survive."""
    data = json.loads(raw.decode("utf-8", errors="surrogateescape"))
    segments: list[tuple[float, float, str]] = []
    carry = b""
    for seg in data.get("transcription") or []:
        text_bytes = carry + str(seg.get("text") or "").encode("utf-8", errors="surrogateescape")
        whole, carry = _split_incomplete_utf8(text_bytes)
        text = whole.decode("utf-8", errors="replace").replace("�", "").strip()
        off = seg.get("offsets") or {}
        try:
            t0, t1 = float(off.get("from", 0)) / 1000.0, float(off.get("to", 0)) / 1000.0
        except (TypeError, ValueError):
            continue
        if text and not _ANNOTATION.match(text):
            segments.append((round(t0, 2), round(t1, 2), text))
    return segments, str((data.get("result") or {}).get("language") or "")


def run_whisper(wav: str, *, model: str, language: str = "auto", threads: int | None = None,
                timeout: float = 1800.0) -> bytes:
    """whisper-cli with JSON output; returns the JSON file's bytes."""
    if not (shutil.which(WHISPER_BIN) or os.path.exists(WHISPER_BIN)):
        raise AsrUnavailable(f"whisper-cli not found at {WHISPER_BIN}")
    if not os.path.exists(model):
        raise AsrUnavailable(f"whisper model not found at {model} (set READPROOF_WHISPER_MODEL)")
    threads = threads or max(1, min(8, os.cpu_count() or 4))
    with tempfile.TemporaryDirectory(prefix="rp-asr-") as tmp:
        prefix = os.path.join(tmp, "out")
        cmd = [WHISPER_BIN, "-m", model, "-f", wav, "-l", language or "auto", "-oj", "-of", prefix, "-np",
               "-t", str(threads)]
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        path = prefix + ".json"
        if proc.returncode != 0 or not os.path.exists(path):
            raise RuntimeError("whisper-cli failed: " + proc.stderr.decode("utf-8", "replace").strip()[-400:])
        with open(path, "rb") as fh:
            return fh.read()


def transcribe(media: str, ctx: Context, *, post_key: str, source_sha256: str, fetched_at: str,
               language: str | None = None, max_s: float | None = None) -> list[Record]:
    """SPEECH Records for one media file. Charges ctx.budget("asr", usd=0, seconds=wall time)."""
    model = model_path()
    language = language or ctx.options.get("language") or "auto"
    t0 = time.monotonic()
    os.makedirs(ctx.workdir, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rp-wav-", dir=ctx.workdir) as tmp:
        wav = os.path.join(tmp, "speech.wav")
        to_wav(media, wav, max_s=max_s)
        raw = run_whisper(wav, model=model, language=language)
    segments, detected = parse_whisper_json(raw)
    seconds = round(time.monotonic() - t0, 3)
    extractor = extractor_name(model)
    records = [
        Record(record_id=Record.make_id(post_key, Channel.SPEECH, {"t0": a, "t1": b}, text), post_key=post_key,
               channel=Channel.SPEECH, locator={"t0": a, "t1": b}, text=text, source_sha256=source_sha256,
               extractor=extractor, fetched_at=fetched_at)
        for a, b, text in segments
    ]
    meta: dict[str, Any] = {"model": os.path.basename(model), "language": detected or language, "segments": len(records),
                            "max_s": max_s}
    ctx.budget.charge("asr", usd=0.0, seconds=seconds, **meta)
    ctx.log.emit("extract", tool="asr", extractor=extractor, source=source_sha256, records=len(records),
                 seconds=seconds, language=detected or language)
    return records
