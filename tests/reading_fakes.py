"""Doubles for the reading-layer tests: a PlatformSpec, a Resolution and profiles, all offline.

The platform builder's modules (platforms/, canonical.py, walls.py) are written in parallel; these tests pin the
readers and the harness against the Protocols, not against those implementations. A FakeSpec's `judge` is the
platform rule: walls.judge (or the readers' stand-in while walls.py is absent) consults it first.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable

from readproof.types import FetchResult, GateDecision, PostRef, Profile, WallType, WallVerdict

UA = "ReadProof/0.1.0 (+https://github.com/jcaiagent7143-ui/readproof)"
P1_NO_BROWSER = Profile(name="P1", obey_robots=False, obey_content_signal=False, user_initiated=True,
                        allow_browser=False, allow_embed_media=True, user_agent=UA)


class FakeSpec:
    def __init__(self, name: str, *, post_id: str = "p1", judge: Callable[[FetchResult, str], WallVerdict | None] | None = None,
                 oembed: str | None = None, embed: str | None = None, canonical: str | None = None) -> None:
        self.name = name
        self.post_id = post_id
        self._judge = judge
        self._oembed = oembed
        self._embed = embed
        self._canonical = canonical

    def match(self, url: str) -> bool:
        return True

    def parse(self, url: str) -> PostRef | None:
        return PostRef(platform=self.name, post_id=self.post_id, form="desktop", url=url)

    def canonical(self, ref: PostRef) -> str:
        return self._canonical or ref.url

    def forms(self, ref: PostRef, **known: str) -> list[tuple[str, str]]:
        return []

    def embed_url(self, ref: PostRef) -> str | None:
        return self._embed

    def oembed_url(self, url: str) -> str | None:
        return self._oembed

    def judge(self, fetch: FetchResult, visible_text: str) -> WallVerdict | None:
        return self._judge(fetch, visible_text) if self._judge else None


def wall_when(marker: str, wall: WallType = WallType.CHALLENGE) -> Callable[[FetchResult, str], WallVerdict | None]:
    """A platform rule: the body containing `marker` is that wall."""

    def rule(fetch: FetchResult, visible_text: str) -> WallVerdict | None:
        body = (fetch.body or b"").decode("utf-8", "replace")
        if marker in body:
            return WallVerdict(False, wall, f"fake-platform marker {marker!r}", len(visible_text))
        return None

    return rule


@dataclass
class FakeResolution:
    input: str
    url: str | None
    ref: PostRef | None
    spec: Any
    fetches: list[FetchResult] = field(default_factory=list)
    gate: list[GateDecision] = field(default_factory=list)
    verdict: WallVerdict | None = None


def resolver_for(spec: FakeSpec, url: str) -> Callable[[str, Any], FakeResolution]:
    def resolve(text: str, ctx: Any) -> FakeResolution:
        return FakeResolution(input=text, url=url, ref=spec.parse(url), spec=spec)

    return resolve


def have(*tools: str) -> bool:
    return all(shutil.which(t) or os.path.exists(t) for t in tools)


CJK_FONT = next((f for f in ("/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
                             "/System/Library/Fonts/Supplemental/Arial Unicode.ttf") if os.path.exists(f)), None)


def render_text_png(path: str, lines: list[str], *, size: str = "1200x400") -> None:
    """A white PNG with black text lines, drawn by ffmpeg's drawtext with a CJK font."""
    txt = path + ".txt"
    with open(txt, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=white:s={size}:d=1", "-vf",
                    f"drawtext=fontfile='{CJK_FONT}':textfile='{txt}':fontsize=64:fontcolor=black:x=60:y=100:line_spacing=40",
                    "-frames:v", "1", path], check=True, capture_output=True)


def make_talking_video(path: str, *, text_lines: list[str], speech: str, seconds: float = 4.0) -> None:
    """An MP4 whose picture shows `text_lines` and whose sound is macOS `say` speaking `speech`."""
    aiff = path + ".aiff"
    subprocess.run(["say", "-o", aiff, speech], check=True, capture_output=True)
    txt = path + ".txt"
    with open(txt, "w", encoding="utf-8") as fh:
        fh.write("\n".join(text_lines) + "\n")
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c=white:s=960x540:d={seconds}:r=25", "-i", aiff,
                    "-filter_complex",
                    f"[0:v]drawtext=fontfile='{CJK_FONT}':textfile='{txt}':fontsize=56:fontcolor=black:x=40:y=160:line_spacing=30[v]",
                    "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-t", str(seconds), path], check=True, capture_output=True)
