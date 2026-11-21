"""Frame sampling for on-screen text: one frame every 2 s plus every scene change, near-duplicates dropped.

One ffmpeg pass decodes the video once and writes two things for every selected frame: a 768 px wide JPEG (what
OCR reads) and a 9x8 grayscale thumbnail (what the difference hash is computed from), so no image library is
needed. The time of each frame comes from ffmpeg's own `showinfo` (pts_time), never from arithmetic on an index.

Near-identical frames (dHash Hamming distance <= 4 to any frame already kept) are dropped before OCR: a talking
head with a fixed caption bar is one frame, not thirty. dHash sees only gradients, so two flat frames of different
brightness (a black and a white title card) hash alike; a frame is therefore a duplicate only when its mean
brightness is also within 24/255 of the kept one. At most 60 frames are kept, spread evenly over the video.
"""

from __future__ import annotations

import os
import re
import subprocess
import time

from .probe import FFMPEG

EVERY_S = 2.0
SCENE = 0.3
CAP = 60
WIDTH = 768
HAMMING = 4
MEAN_DELTA = 24  # of 255: flat frames of different brightness are different frames
_PTS = re.compile(r"\bpts_time:\s*(-?[\d.]+)")


def dhash(gray9x8: bytes) -> int:
    """64-bit difference hash of a 9x8 grayscale thumbnail: is each pixel brighter than its right neighbour?"""
    h = 0
    for row in range(8):
        base = row * 9
        for col in range(8):
            h = (h << 1) | (1 if gray9x8[base + col] > gray9x8[base + col + 1] else 0)
    return h


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _spread(n: int, k: int) -> list[int]:
    """k indices spread evenly over range(n), first and last included."""
    if n <= k:
        return list(range(n))
    if k == 1:
        return [0]
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def sample_frames(media: str, outdir: str, *, every_s: float = EVERY_S, scene: float = SCENE, cap: int = CAP,
                  width: int = WIDTH, max_s: float | None = None, max_hamming: int = HAMMING,
                  timeout: float = 900.0) -> list[tuple[float, str]]:
    """[(t seconds, jpeg path)] in time order, deduplicated and capped. Raises RuntimeError if ffmpeg fails."""
    os.makedirs(outdir, exist_ok=True)
    pattern = os.path.join(outdir, "f_%05d.jpg")
    raw_path = os.path.join(outdir, "thumbs.gray")
    select = f"isnan(prev_selected_t)+gte(t-prev_selected_t,{every_s:g})+gt(scene,{scene:g})"
    graph = (f"[0:v:0]select='{select}',showinfo,split=2[big][small];"
             f"[big]scale={width}:-2[bo];[small]scale=9:8:flags=area,format=gray[so]")
    cmd = [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "info", "-y"]
    if max_s:
        cmd += ["-t", f"{float(max_s):.3f}"]
    cmd += ["-i", media, "-filter_complex", graph,
            "-map", "[bo]", "-fps_mode", "vfr", "-q:v", "3", pattern,
            "-map", "[so]", "-fps_mode", "vfr", "-f", "rawvideo", "-pix_fmt", "gray", raw_path]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    log = proc.stderr.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg frame sampling failed: " + log.strip()[-400:])
    times = [float(m.group(1)) for line in log.splitlines() if "showinfo" in line and (m := _PTS.search(line))]
    files = sorted(f for f in os.listdir(outdir) if f.startswith("f_") and f.endswith(".jpg"))
    raw = b""
    if os.path.exists(raw_path):
        with open(raw_path, "rb") as fh:
            raw = fh.read()
    n = min(len(times), len(files), len(raw) // 72)
    kept: list[tuple[float, str]] = []
    seen: list[tuple[int, float]] = []  # (dHash, mean brightness) of every kept frame
    for i in range(len(files)):
        path = os.path.join(outdir, files[i])
        if i >= n:
            os.remove(path)
            continue
        thumb = raw[i * 72:(i + 1) * 72]
        h, mean = dhash(thumb), sum(thumb) / 72.0
        if any(hamming(h, k) <= max_hamming and abs(mean - m) <= MEAN_DELTA for k, m in seen):
            os.remove(path)
            continue
        seen.append((h, mean))
        kept.append((round(max(0.0, times[i]), 3), path))
    if os.path.exists(raw_path):
        os.remove(raw_path)
    if len(kept) > cap:
        keep = set(_spread(len(kept), cap))
        for i, (_, path) in enumerate(kept):
            if i not in keep:
                os.remove(path)
        kept = [kept[i] for i in sorted(keep)]
    return kept


def frames_for(media: str, ctx, *, max_s: float | None = None) -> list[tuple[float, str]]:
    """sample_frames into ctx.workdir/frames-<n>, charged to ctx.budget("frames", seconds=wall time)."""
    t0 = time.monotonic()
    outdir = os.path.join(ctx.workdir, f"frames-{int(time.time() * 1000)}")
    frames = sample_frames(media, outdir, max_s=max_s)
    seconds = round(time.monotonic() - t0, 3)
    ctx.budget.charge("frames", usd=0.0, seconds=seconds, frames=len(frames))
    ctx.log.emit("extract", tool="frames", media=os.path.basename(media), frames=len(frames), seconds=seconds)
    return frames
