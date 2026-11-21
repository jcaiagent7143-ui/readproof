"""ffprobe, once per media file: duration and which kinds of stream it really carries.

Why its own module: the embed reader needs the duration it reports in meta, and the harness needs to know which
file carries speech and which carries pictures (Bilibili's player serves them as two DASH files; Douyin's as one
MP4). A video stream that is only cover art (an m4a's attached picture) does not count as pictures.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

FFPROBE = os.environ.get("READPROOF_FFPROBE") or shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
FFMPEG = os.environ.get("READPROOF_FFMPEG") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def probe(path: str, *, timeout: float = 60.0) -> dict[str, Any]:
    """{"duration_s", "has_audio", "has_video", "format", "width", "height", "error"} — never raises."""
    out: dict[str, Any] = {"duration_s": None, "has_audio": False, "has_video": False, "format": "", "width": None,
                           "height": None, "error": None}
    try:
        proc = subprocess.run(
            [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
            capture_output=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        out["error"] = f"ffprobe failed: {exc}"
        return out
    if proc.returncode != 0:
        out["error"] = (proc.stderr.decode("utf-8", "replace").strip() or f"ffprobe exit {proc.returncode}")[:300]
        return out
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except ValueError:
        out["error"] = "ffprobe printed no JSON"
        return out
    durations: list[float] = []
    for s in data.get("streams") or []:
        kind = s.get("codec_type")
        if kind == "audio":
            out["has_audio"] = True
        elif kind == "video" and not (s.get("disposition") or {}).get("attached_pic"):
            out["has_video"] = True
            out["width"], out["height"] = s.get("width"), s.get("height")
        try:
            durations.append(float(s.get("duration")))
        except (TypeError, ValueError):
            pass
    fmt = data.get("format") or {}
    out["format"] = fmt.get("format_name", "")
    try:
        out["duration_s"] = round(float(fmt.get("duration")), 3)
    except (TypeError, ValueError):
        out["duration_s"] = round(max(durations), 3) if durations else None
    return out
