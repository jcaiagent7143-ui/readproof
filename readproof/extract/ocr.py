"""On-screen and image text with Apple Vision (tools/vision_ocr.swift), tesseract as the fallback.

Apple Vision because it reads Chinese, Japanese and English in one pass, locally, for free, at gold-label quality
on this platform's posts; the Swift tool is compiled once into tools/bin/vision_ocr (skipped when the binary is
newer than the source) and run on batches of images, one JSON line back per image.

Records: one per frame (ON_SCREEN, locator {"t"}) or per carousel image (IMAGE_TEXT, locator {"index"}), the
lines of that image merged in reading order (rows top to bottom, left to right inside a row). Lines Vision is
less than 0.3 confident about are dropped. A frame whose text is exactly the previous frame's is not repeated.

Fallback: tesseract, only when Vision cannot run (not macOS, or the tool cannot be built). Only the `eng`
language data is installed on the reference machine, so the fallback does not read Chinese or Japanese — records
it produces are named "tesseract:eng" so that limitation is visible in every result.

Build note: on this Mac's CommandLineTools 16.4, a stale usr/include/swift/module.modulemap (2023) redefines the
SwiftBridging module that bridging.modulemap (2025) also defines, and every Swift file importing Foundation fails
to compile. System files are never edited: the compile is retried once with a clang VFS overlay that masks only
that stale file, for this one compile.
"""

from __future__ import annotations

import csv
import io
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from ..interfaces import Context
from ..types import Channel, Record

REPO = Path(__file__).resolve().parents[2]
SWIFT_SOURCE = REPO / "tools" / "vision_ocr.swift"
VISION_BIN = REPO / "tools" / "bin" / "vision_ocr"
SWIFTC = "/usr/bin/swiftc"
TESSERACT = shutil.which("tesseract") or "/opt/homebrew/bin/tesseract"
MIN_CONFIDENCE = 0.3
BATCH = 16
VISION_EXTRACTOR = "vision:VNRecognizeText"
TESSERACT_EXTRACTOR = "tesseract:eng"
_STALE_MODULEMAP = "/Library/Developer/CommandLineTools/usr/include/swift/module.modulemap"


class OcrUnavailable(RuntimeError):
    """Neither Apple Vision nor tesseract can run here."""


# --- building the Vision tool ------------------------------------------------------------------------------


def _compile(source: Path, target: Path) -> tuple[bool, str]:
    def run(extra: list[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run([SWIFTC, "-O", *extra, "-o", str(target), str(source)], capture_output=True,
                              timeout=600, check=False)

    proc = run([])
    err = proc.stderr.decode("utf-8", "replace")
    if proc.returncode == 0:
        return True, ""
    if "redefinition of module 'SwiftBridging'" in err and os.path.exists(_STALE_MODULEMAP):
        with tempfile.TemporaryDirectory(prefix="rp-vfs-") as tmp:
            empty = os.path.join(tmp, "empty.modulemap")
            Path(empty).write_text("// masked for this compile: duplicate SwiftBridging definition\n")
            overlay = os.path.join(tmp, "overlay.yaml")
            Path(overlay).write_text(json.dumps({
                "version": 0, "case-sensitive": "false",
                "roots": [{"type": "directory", "name": os.path.dirname(_STALE_MODULEMAP),
                           "contents": [{"type": "file", "name": "module.modulemap", "external-contents": empty}]}],
            }))
            proc = run(["-vfsoverlay", overlay, "-Xcc", "-ivfsoverlay", "-Xcc", overlay])
            if proc.returncode == 0:
                return True, "built with a VFS overlay masking the stale SwiftBridging modulemap"
            err = proc.stderr.decode("utf-8", "replace")
    return False, err.strip()[-600:]


def vision_binary() -> str | None:
    """The compiled Vision tool, building it first if missing or older than its source. None off macOS."""
    if platform.system() != "Darwin" or not SWIFT_SOURCE.exists():
        return None
    if VISION_BIN.exists() and VISION_BIN.stat().st_mtime >= SWIFT_SOURCE.stat().st_mtime:
        return str(VISION_BIN)
    if not os.path.exists(SWIFTC):
        return None
    VISION_BIN.parent.mkdir(parents=True, exist_ok=True)
    import fcntl

    with open(VISION_BIN.parent / ".build.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # two reads starting at once build it once
        if VISION_BIN.exists() and VISION_BIN.stat().st_mtime >= SWIFT_SOURCE.stat().st_mtime:
            return str(VISION_BIN)
        tmp = VISION_BIN.with_name(f".vision_ocr.{os.getpid()}")
        ok, note = _compile(SWIFT_SOURCE, tmp)
        if not ok:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("could not build tools/vision_ocr.swift: " + note)
        os.replace(tmp, VISION_BIN)
    return str(VISION_BIN)


# --- running OCR -------------------------------------------------------------------------------------------


def _run_vision(binary: str, paths: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(paths), BATCH):
        batch = paths[i:i + BATCH]
        proc = subprocess.run([binary, *batch], capture_output=True, timeout=60 + 20 * len(batch), check=False)
        lines = [ln for ln in proc.stdout.decode("utf-8", "replace").splitlines() if ln.strip()]
        for path, raw in zip(batch, lines):  # one line per argument, in order
            try:
                data = json.loads(raw)
            except ValueError:
                data = {"lines": [], "error": "unparseable output"}
            out[path] = {"lines": data.get("lines") or [], "error": data.get("error")}
        for path in batch[len(lines):]:
            out[path] = {"lines": [], "error": f"no output (exit {proc.returncode})"}
    return out


def _run_tesseract(paths: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in paths:
        proc = subprocess.run([TESSERACT, path, "stdout", "-l", "eng", "--psm", "3", "tsv"], capture_output=True,
                              timeout=120, check=False)
        if proc.returncode != 0:
            out[path] = {"lines": [], "error": proc.stderr.decode("utf-8", "replace").strip()[:200]}
            continue
        out[path] = {"lines": parse_tesseract_tsv(proc.stdout.decode("utf-8", "replace")), "error": None}
    return out


def parse_tesseract_tsv(tsv: str) -> list[dict[str, Any]]:
    """tesseract TSV -> lines in the Vision tool's shape (text, confidence 0..1, bbox normalised top-left)."""
    width = height = 0.0
    groups: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") == "1":
            width, height = float(row.get("width") or 0), float(row.get("height") or 0)
        if row.get("level") != "5" or not (row.get("text") or "").strip():
            continue
        try:
            if float(row.get("conf") or -1) < 0:
                continue
        except ValueError:
            continue
        groups.setdefault((row["block_num"], row["par_num"], row["line_num"]), []).append(row)
    lines = []
    for words in groups.values():
        x0 = min(float(w["left"]) for w in words)
        y0 = min(float(w["top"]) for w in words)
        x1 = max(float(w["left"]) + float(w["width"]) for w in words)
        y1 = max(float(w["top"]) + float(w["height"]) for w in words)
        conf = sum(float(w["conf"]) for w in words) / len(words) / 100.0
        w_, h_ = (width or x1 or 1.0), (height or y1 or 1.0)
        lines.append({"text": " ".join(w["text"].strip() for w in words), "confidence": round(conf, 3),
                      "bbox": [x0 / w_, y0 / h_, (x1 - x0) / w_, (y1 - y0) / h_]})
    return lines


def ocr_paths(paths: list[str]) -> tuple[dict[str, dict[str, Any]], str]:
    """{path: {"lines": [...], "error": str|None}} and the extractor name. Vision first, tesseract second."""
    if not paths:
        return {}, VISION_EXTRACTOR
    binary = None
    try:
        binary = vision_binary()
    except RuntimeError:
        binary = None
    if binary:
        return _run_vision(binary, paths), VISION_EXTRACTOR
    if shutil.which(TESSERACT) or os.path.exists(TESSERACT):
        return _run_tesseract(paths), TESSERACT_EXTRACTOR
    raise OcrUnavailable("neither Apple Vision (macOS + swiftc) nor tesseract is available")


def merge_lines(lines: list[dict[str, Any]], *, min_confidence: float = MIN_CONFIDENCE) -> str:
    """One image's lines as text in reading order; lines below min_confidence are dropped."""
    items = []
    for ln in lines:
        text = str(ln.get("text") or "").strip()
        try:
            conf = float(ln.get("confidence", 1.0))
        except (TypeError, ValueError):
            conf = 0.0
        bbox = ln.get("bbox") or [0.0, 0.0, 0.0, 0.0]
        if not text or conf < min_confidence or len(bbox) != 4:
            continue
        x, y, _, h = (float(v) for v in bbox)
        items.append((y, x, max(h, 1e-6), text))
    items.sort(key=lambda it: (it[0], it[1]))
    rows: list[list[tuple[float, float, float, str]]] = []
    for it in items:
        if rows:
            ref = rows[-1][0]
            if abs((it[0] + it[2] / 2) - (ref[0] + ref[2] / 2)) <= 0.5 * min(it[2], ref[2]):
                rows[-1].append(it)
                continue
        rows.append([it])
    return "\n".join(" ".join(t for _, _, _, t in sorted(row, key=lambda r: r[1])) for row in rows)


def _records(kind: Channel, keyed: list[tuple[Any, str, str]], ctx: Context, *, post_key: str,
             fetched_at: str) -> list[Record]:
    t0 = time.monotonic()
    results, extractor = ocr_paths([p for _, p, _ in keyed])
    records: list[Record] = []
    previous = None
    errors = 0
    for key, path, sha in keyed:
        res = results.get(path) or {}
        errors += bool(res.get("error"))
        text = merge_lines(res.get("lines") or [])
        if not text or (kind is Channel.ON_SCREEN and text == previous):
            continue
        previous = text
        locator = {"t": float(key)} if kind is Channel.ON_SCREEN else {"index": int(key)}
        records.append(Record(record_id=Record.make_id(post_key, kind, locator, text), post_key=post_key,
                              channel=kind, locator=locator, text=text, source_sha256=sha, extractor=extractor,
                              fetched_at=fetched_at))
    seconds = round(time.monotonic() - t0, 3)
    ctx.budget.charge("ocr", usd=0.0, seconds=seconds, images=len(keyed), extractor=extractor)
    ctx.log.emit("extract", tool="ocr", channel=kind.value, extractor=extractor, images=len(keyed),
                 records=len(records), errors=errors, seconds=seconds)
    return records


def ocr_frames(frames: list[tuple[float, str, str]], ctx: Context, *, post_key: str, fetched_at: str) -> list[Record]:
    """ON_SCREEN Records from video frames given as (t seconds, jpeg path, sha256 of that jpeg)."""
    return _records(Channel.ON_SCREEN, sorted(frames, key=lambda f: f[0]), ctx, post_key=post_key, fetched_at=fetched_at)


def ocr_images(images: list[tuple[int, str, str]], ctx: Context, *, post_key: str, fetched_at: str) -> list[Record]:
    """IMAGE_TEXT Records from carousel images given as (index, path, sha256 of that image)."""
    return _records(Channel.IMAGE_TEXT, sorted(images, key=lambda i: i[0]), ctx, post_key=post_key, fetched_at=fetched_at)
