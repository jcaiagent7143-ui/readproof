"""Track K: planted-canary posts. Each post carries distinct random codes, each in exactly ONE channel:

  speech     spoken only (two nouns and a number; the burned-in subtitle masks it)
  on_screen  shown only (5 characters, never spoken)
  caption    written only in the post's caption / description
  comment    written only in the author's pinned comment (self-reply on X)
  image      written only inside one carousel image (image posts)

A right code proves a read of that channel; a different code is a fabrication; "not found" is an abstention.
The answer key is written to bench/data/private/ (git-ignored) and committed to by SHA-256 before any system runs.

usage: python bench/build/canary.py build    # codes, voice, slides, MP4s, copy, gold, commitment
"""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "bench" / "data" / "private" / "canary"
PUBLIC = ROOT / "bench" / "canary"
KOKORO_PY = Path.home() / ".venvs" / "kokoro" / "bin" / "python"
TTS_ZH = ROOT / "tools" / "tts_kokoro_zh.py"
FONT_ZH = "/System/Library/Fonts/Hiragino Sans GB.ttc"
FONT_MONO = "/System/Library/Fonts/Menlo.ttc"
INK, PAPER, VIOLET, RED, GREY = "#03080a", "#fcfcfe", "#5b4be8", "#e2452a", "#8a8f98"

# Unambiguous alphabet: no 0/O, 1/I/L, 2/Z, 5/S, 8/B.
ALPHABET = "ACDEFGHJKMNPQRTUVWXY34679"
ZH_NOUNS = ["灯塔", "橙子", "海豚", "雪山", "钢琴", "风筝", "火车", "熊猫", "月亮", "森林", "蜡烛", "书包", "菠萝", "雨伞",
            "樱桃", "帆船", "地球", "蘑菇", "闹钟", "南瓜", "鲸鱼", "骆驼", "灯笼", "茶杯", "西瓜", "恐龙", "彩虹", "冰山",
            "椰子", "企鹅", "长颈鹿", "向日葵"]
EN_NOUNS = ["lighthouse", "orange", "dolphin", "mountain", "piano", "kite", "train", "panda", "candle", "backpack",
            "pineapple", "umbrella", "cherry", "sailboat", "planet", "mushroom", "pumpkin", "whale", "camel", "lantern",
            "teacup", "watermelon", "dinosaur", "rainbow", "iceberg", "coconut", "penguin", "giraffe", "sunflower", "violin"]
ZH_NUM = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九"}
EN_NUM_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
EN_NUM_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

# One post per platform account the author holds (Douyin, Bilibili, RedNote/Xiaohongshu, YouTube, TikTok, X).
POSTS = [
    {"id": "K01-douyin", "platform": "douyin", "lang": "zh", "kind": "video"},
    {"id": "K02-bilibili", "platform": "bilibili", "lang": "zh", "kind": "video"},
    {"id": "K03-rednote-video", "platform": "xiaohongshu", "lang": "zh", "kind": "video"},
    {"id": "K04-rednote-images", "platform": "xiaohongshu", "lang": "zh", "kind": "images"},
    {"id": "K05-youtube", "platform": "youtube", "lang": "en", "kind": "video"},
    {"id": "K06-tiktok", "platform": "tiktok", "lang": "en", "kind": "video"},
    {"id": "K07-x", "platform": "x", "lang": "en", "kind": "video"},
]


def code5() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(5))


def zh_number(n: int) -> str:
    tens, ones = divmod(n, 10)
    return ("" if tens == 1 else ZH_NUM[tens]) + "十" + (ZH_NUM[ones] if ones else "")


def en_number(n: int) -> str:
    tens, ones = divmod(n, 10)
    return EN_NUM_TENS[tens] + ("-" + EN_NUM_ONES[ones] if ones else "")


def make_codes(lang: str, kind: str, used: set[str]) -> dict:
    def fresh(prefix: str) -> str:
        while True:
            c = f"{prefix}-{code5()}"
            if c not in used:
                used.add(c)
                return c

    nouns = ZH_NOUNS if lang == "zh" else EN_NOUNS
    w1, w2 = secrets.SystemRandom().sample(nouns, 2)
    num = secrets.randbelow(78) + 21  # 21..98, two syllables or more, never a round ten
    while num % 10 == 0:
        num = secrets.randbelow(78) + 21
    codes = {
        "speech": {"words": [w1, w2], "number": num,
                   "spoken": f"{w1}，{w2}，{zh_number(num)}" if lang == "zh" else f"{w1}, {w2}, {en_number(num)}"},
        "caption": fresh("C"),
        "comment": fresh("M"),
    }
    if kind == "video":
        codes["on_screen"] = fresh("V")
    else:
        codes["image"] = fresh("I")
    return codes


# --- copy ---------------------------------------------------------------------------------------

def lines_for(post: dict, codes: dict) -> list[dict]:
    """Voice lines and the slide each one plays over. The speech code is said twice; nothing else is spoken."""
    s = codes["speech"]["spoken"]
    if post["lang"] == "zh":
        return [
            {"id": "L1", "slide": "title", "text": "这是一条 AI 阅读测试。这条视频里藏了四个暗号，看看你的 AI 能找到几个。"},
            {"id": "L2", "slide": "speech", "text": f"第一个暗号只用声音说：{s}。再说一遍：{s}。"},
            {"id": "L3", "slide": "screen", "text": "第二个暗号只写在画面上，我不念出来。"},
            {"id": "L4", "slide": "rest", "text": "第三个在文案里，第四个在置顶评论里。把链接丢给你的 AI，问它找到了哪几个。"},
            {"id": "L5", "slide": "end", "text": "关注我，下期公布哪些 AI 真的读懂了。"},
        ]
    return [
        {"id": "L1", "slide": "title", "text": "This is an AI reading test. Four codes are hidden in this video. How many can your AI find?"},
        {"id": "L2", "slide": "speech", "text": f"The first code is only spoken: {s}. Once more: {s}."},
        {"id": "L3", "slide": "screen", "text": "The second code is only on screen. I won't read it out."},
        {"id": "L4", "slide": "rest", "text": "The third is in the caption, and the fourth is in the pinned comment. Paste this link into your AI and ask which ones it found."},
        {"id": "L5", "slide": "end", "text": "Follow to see which AIs actually read it."},
    ]


def post_copy(post: dict, codes: dict) -> dict:
    """Title, caption and pinned comment. The caption code appears only here; the comment code only in the comment."""
    zh = post["lang"] == "zh"
    if post["kind"] == "images":
        title = "AI 阅读测试：这组图藏了 4 个暗号"
        caption = (f"这是一条 AI 阅读测试笔记。图里、文案里、置顶评论里一共藏了 4 个暗号。文案暗号：{codes['caption']}。"
                   "把这条笔记丢给你的 AI，问它找到了哪几个。")
        return {"title": title, "caption": caption, "pinned_comment": f"置顶暗号：{codes['comment']}（第 4 个暗号）",
                "topics": ["AI", "人工智能", "AI测试"]}
    if zh:
        title = "AI 阅读测试：这条视频藏了 4 个暗号"
        caption = (f"AI 阅读测试：声音里、画面上、文案里、置顶评论里各藏了一个暗号。文案暗号：{codes['caption']}。"
                   "把链接丢给你的 AI，问它找到了哪几个。")
        return {"title": title, "caption": caption, "pinned_comment": f"置顶暗号：{codes['comment']}（第 4 个暗号）",
                "topics": ["AI", "人工智能", "AI测试"]}
    title = "AI reading test: 4 codes are hidden in this video"
    caption = (f"An AI reading test: one code is spoken, one is on screen, one is in this caption and one is in the pinned "
               f"comment. Caption code: {codes['caption']}. Paste the link into your AI and ask which codes it found.")
    return {"title": title, "caption": caption, "pinned_comment": f"Pinned code: {codes['comment']} (code 4 of 4)",
            "topics": ["AI", "AItest"]}


# --- slides -------------------------------------------------------------------------------------

W, H = 1080, 1920


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def fitted(d: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, max_w: int) -> ImageFont.FreeTypeFont:
    """The same face, shrunk until `text` fits in max_w (nothing is ever clipped at the frame edge)."""
    while f.size > 24 and d.textlength(text, font=f) > max_w:
        f = ImageFont.truetype(f.path, f.size - 4)
    return f


def centered(d: ImageDraw.ImageDraw, y: int, text: str, f, fill=INK, w: int = W) -> int:
    f = fitted(d, text, f, w - 2 * 120)
    box = d.textbbox((0, 0), text, font=f)
    d.text(((w - (box[2] - box[0])) / 2, y), text, font=f, fill=fill)
    return y + (box[3] - box[1])


def pill(d: ImageDraw.ImageDraw, x: int, y: int, text: str, size: int = 48) -> None:
    f = font(FONT_ZH, size)
    tw = d.textlength(text, font=f)
    d.rounded_rectangle((x, y, x + tw + 60, y + size + 32), radius=24, fill=RED)
    d.text((x + 30, y + 8), text, font=f, fill=PAPER)


def slide(kind: str, post: dict, codes: dict, path: Path, w: int = W, h: int = H) -> None:
    zh = post["lang"] == "zh"
    img = Image.new("RGB", (w, h), PAPER)
    d = ImageDraw.Draw(img)
    big, mid, small = font(FONT_ZH, 96), font(FONT_ZH, 64), font(FONT_ZH, 44)
    pill(d, 120, 300, "AI 阅读测试" if zh else "AI READING TEST")
    tag = f"ReadProof · {post['id'][:3]}"
    d.text((120, h - 260), tag, font=small, fill=GREY)
    if kind == "title":
        y = centered(d, 640, "这条视频藏了" if zh else "Hidden in this video:", big)
        y = centered(d, y + 60, "4 个暗号" if zh else "4 codes", font(FONT_ZH, 160), VIOLET)
        centered(d, y + 90, "你的 AI 能找到几个？" if zh else "How many can your AI find?", mid)
    elif kind == "speech":
        y = centered(d, 600, "暗号 1 · 只在声音里" if zh else "Code 1 · spoken only", big)
        # A speaker glyph drawn, not a font emoji.
        cx, cy = W // 2, y + 260
        d.rectangle((cx - 150, cy - 60, cx - 70, cy + 60), fill=VIOLET)
        d.polygon([(cx - 70, cy - 60), (cx + 20, cy - 140), (cx + 20, cy + 140), (cx - 70, cy + 60)], fill=VIOLET)
        for r in (70, 130):
            d.arc((cx + 20 - r + 40, cy - r, cx + 20 + r + 40, cy + r), start=-50, end=50, fill=VIOLET, width=16)
        centered(d, cy + 260, "●● ●● ●●" if zh else "●●● ●●● ●●", mid, GREY)
    elif kind == "screen":
        y = centered(d, 560, "暗号 2 · 只在画面上" if zh else "Code 2 · on screen only", big)
        code = codes["on_screen"]
        f = font(FONT_MONO, 210)
        box = d.textbbox((0, 0), code, font=f)
        bw, bh = box[2] - box[0], box[3] - box[1]
        x0, y0 = (W - bw) / 2, y + 220
        d.rounded_rectangle((x0 - 60, y0 - 50, x0 + bw + 60, y0 + bh + 90), radius=36, outline=VIOLET, width=10)
        d.text((x0, y0), code, font=f, fill=INK)
        centered(d, y0 + bh + 200, "（不会念出来）" if zh else "(never read aloud)", mid, GREY)
    elif kind == "rest":
        y = centered(d, 640, "还有 2 个暗号" if zh else "2 more codes", big)
        y = centered(d, y + 120, "③ 在文案里" if zh else "③ in the caption", mid, VIOLET)
        centered(d, y + 70, "④ 在置顶评论里" if zh else "④ in the pinned comment", mid, VIOLET)
    elif kind == "end":
        y = centered(d, 700, "下期公布：" if zh else "Next time:", big)
        centered(d, y + 80, "哪些 AI 真的读懂了" if zh else "which AIs really read it", mid, VIOLET)
    img.save(path)


def image_set(post: dict, codes: dict, outdir: Path) -> list[Path]:
    """Four 3:4 carousel images; the image code is inside image 3 only."""
    paths = []
    w, h = 1080, 1440
    texts = [
        ("这组图里藏了暗号", "你的 AI 能找到吗？"),
        ("暗号在哪里？", "图里一个，文案里一个，置顶评论里一个"),
        ("图片暗号", codes["image"]),
        ("把这条笔记丢给你的 AI", "问它找到了哪几个"),
    ]
    for i, (a, b) in enumerate(texts, start=1):
        img = Image.new("RGB", (w, h), PAPER)
        d = ImageDraw.Draw(img)
        pill(d, 90, 120, "AI 阅读测试")
        d.text((90, 420), a, font=fitted(d, a, font(FONT_ZH, 84), w - 180), fill=INK)
        f = font(FONT_MONO, 170) if i == 3 else font(FONT_ZH, 56)
        d.text((90, 620), b, font=fitted(d, b, f, w - 180), fill=VIOLET if i == 3 else INK)
        d.text((90, h - 160), f"ReadProof · {post['id'][:3]} · {i}/4", font=font(FONT_ZH, 40), fill=GREY)
        p = outdir / f"image_{i}.png"
        img.save(p)
        paths.append(p)
    return paths


# --- voice and video ----------------------------------------------------------------------------

def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def duration(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)])
    return float(out.stdout.strip())


def voice(post: dict, lines: list[dict], outdir: Path) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    if post["lang"] == "zh":
        spec = outdir / "lines.json"
        spec.write_text(json.dumps([{"id": l["id"], "text": l["text"]} for l in lines], ensure_ascii=False))
        run([str(KOKORO_PY), str(TTS_ZH), "zf_001", "1.15", str(outdir), "--lines", str(spec)])
        return [outdir / f"{l['id']}.wav" for l in lines]
    paths = []
    for l in lines:
        aiff = outdir / f"{l['id']}.aiff"
        run(["say", "-v", "Samantha", "-r", "175", "-o", str(aiff), l["text"]])
        wav = outdir / f"{l['id']}.wav"
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), "-ar", "24000", "-ac", "1", str(wav)])
        paths.append(wav)
    return paths


def video(post: dict, codes: dict, lines: list[dict], wavs: list[Path], outdir: Path) -> Path:
    """One slide per voice line, held for the line plus a pause; the on-screen code holds at least 6 s."""
    parts = []
    for l, wav in zip(lines, wavs):
        png = outdir / f"slide_{l['id']}.png"
        slide(l["slide"], post, codes, png)
        hold = duration(wav) + 0.9
        if l["slide"] == "screen":
            hold = max(hold, 6.0)
        seg = outdir / f"seg_{l['id']}.mp4"
        run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(png), "-i", str(wav),
             "-filter_complex", f"[1:a]apad=whole_dur={hold:.2f}[a]", "-map", "0:v", "-map", "[a]",
             "-t", f"{hold:.2f}", "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20",
             "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", str(seg)])
        parts.append(seg)
    listing = outdir / "concat.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    raw = outdir / "raw.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(raw)])
    final = outdir / f"{post['id']}.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af", "loudnorm=I=-15:TP=-1.5:LRA=11",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", str(final)])
    shutil.copy(outdir / "slide_L1.png", outdir / "cover.png")
    for p in parts + [raw, listing]:
        p.unlink(missing_ok=True)
    return final


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


KEY = PRIVATE / "key.json"  # the answer key: codes, copy, voice lines. Made once; committed by hash.
MEDIA = PRIVATE / "media.json"  # what was rendered from the key (hashes, durations). Re-rendering never touches the key.


def canonical_bytes(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def make_key() -> dict:
    """Codes are drawn once. A second call refuses: changing codes after a commitment would void it."""
    PRIVATE.mkdir(parents=True, exist_ok=True)
    if KEY.exists():
        return json.loads(KEY.read_text())
    legacy = PRIVATE / "gold.json"  # first build wrote codes and media together; keep its codes
    used: set[str] = set()
    key = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "posts": {}}
    old = json.loads(legacy.read_text())["posts"] if legacy.exists() else {}
    for post in POSTS:
        codes = old[post["id"]]["codes"] if post["id"] in old else make_codes(post["lang"], post["kind"], used)
        entry = {**post, "codes": codes, "copy": post_copy(post, codes)}
        if post["kind"] == "video":
            entry["voice_lines"] = lines_for(post, codes)
        key["posts"][post["id"]] = entry
    if legacy.exists():
        key["created_at"] = json.loads(legacy.read_text())["created_at"]
    KEY.write_bytes(canonical_bytes(key))
    legacy.unlink(missing_ok=True)
    return key


def commit(key: dict) -> str:
    digest = hashlib.sha256(canonical_bytes(key)).hexdigest()
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / "COMMITMENT.md").write_text(
        "# Track K answer-key commitment\n\n"
        f"SHA-256 of the canonical answer key (`bench/data/private/canary/key.json`: UTF-8 JSON, sorted keys, no spaces):\n\n"
        f"`{digest}`\n\n"
        f"Codes drawn {key['created_at']} with Python's `secrets` module, before any system was run on Track K.\n"
        "The key is published after evaluation; anyone can then check that it hashes to this value.\n")
    return digest


def render(key: dict, only: set[str] | None = None) -> dict:
    media = json.loads(MEDIA.read_text()) if MEDIA.exists() else {}
    for pid, entry in key["posts"].items():
        if only and pid not in only:
            continue
        post = {k: entry[k] for k in ("id", "platform", "lang", "kind")}
        outdir = PRIVATE / pid
        outdir.mkdir(parents=True, exist_ok=True)
        if post["kind"] == "video":
            lines = entry["voice_lines"]
            vdir = outdir / "voice"
            wavs = [vdir / f"{l['id']}.wav" for l in lines]
            if not all(w.exists() for w in wavs):
                wavs = voice(post, lines, vdir)
            mp4 = video(post, entry["codes"], lines, wavs, outdir)
            media[pid] = {"video": mp4.name, "video_sha256": sha256_file(mp4), "duration_s": round(duration(mp4), 2)}
        else:
            imgs = image_set(post, entry["codes"], outdir)
            media[pid] = {"images": [p.name for p in imgs], "images_sha256": [sha256_file(p) for p in imgs]}
        (outdir / "copy.json").write_text(json.dumps(entry["copy"], ensure_ascii=False, indent=1))
        print(pid, media[pid].get("duration_s", "images"))
    MEDIA.write_text(json.dumps(media, indent=1))
    return media


if __name__ == "__main__":
    cmd = sys.argv[1:2]
    if cmd == ["build"]:
        k = make_key()
        render(k)
        print("commitment", commit(k))
    elif cmd == ["render"]:
        render(make_key(), set(sys.argv[2:]) or None)
    elif cmd == ["commit"]:
        print("commitment", commit(make_key()))
    else:
        print(__doc__)
