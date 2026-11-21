"""The shared contract. Every module reads and writes these types; nothing else crosses a module boundary.

Design rules this file encodes (DESIGN.md §2):
- A wall never reaches a model as page text. A read either serves Records or returns a typed WallType.
- Every Record is content-addressed: it names the SHA-256 of the snapshot it was extracted from.
- Channels are where evidence sits in a post, each with its own locator shape.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Channel(str, Enum):
    """Where a piece of evidence sits in a post. Locator shapes are fixed per channel."""

    SPEECH = "speech"  # timed transcript span            locator {"t0": float, "t1": float}
    ON_SCREEN = "on_screen"  # OCR of a video frame        locator {"t": float}
    FRAME = "frame"  # visual description of a frame      locator {"t": float}
    IMAGE_TEXT = "image_text"  # OCR of a carousel image  locator {"index": int}
    IMAGE = "image"  # visual description of an image     locator {"index": int}
    CAPTION = "caption"  # title, caption, description    locator {"field": "title" | "caption" | "description" | "author"}
    COMMENT = "comment"  # a comment the public page serves  locator {"rank": int}
    PAGE = "page"  # other visible page text (fallback)   locator {"offset": int}


class WallType(str, Enum):
    """Why a read was not served. The harness returns one of these instead of page text."""

    POLICY = "policy"  # our own gate refused: robots.txt group, Content-Signal, or the profile
    CHALLENGE = "challenge"  # bot check, verification page, captcha (never answered)
    LOGIN = "login"  # login wall or redirect to a login page
    TOKEN = "token"  # the platform says the share token is missing, wrong or expired
    DELETED = "deleted"  # removed, 404, 页面不见了, 作品不存在
    SELF_VISIBLE = "self_visible"  # visible only to its author
    APP_ONLY = "app_only"  # the platform serves it only in its app
    REGION = "region"  # region-locked
    SOFT_404 = "soft_404"  # redirected to a home or generic page that is not the post
    RATE_LIMITED = "rate_limited"  # 429 or a platform rate message
    EMPTY = "empty"  # HTTP 200 with no visible content and no known signature (app shell)
    NETWORK = "network"  # DNS, TLS, timeout, connection reset
    UNSUPPORTED = "unsupported"  # no reader for this platform or link form
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Profile:
    """An access profile is data, not prose. tests/test_rules.py pins what each field means."""

    name: str  # "P0" strict | "P1" user-initiated | "P2" user-assisted
    obey_robots: bool  # P0: a robots.txt Disallow for our declared agent refuses the request
    obey_content_signal: bool  # P0: Content-Signal ai-input=no refuses
    user_initiated: bool  # P1/P2: single fetches of a link a user supplied; robots logged, not binding
    allow_browser: bool  # may render in headless Chromium (fresh context, empty cookie jar)
    allow_embed_media: bool  # may keep media the official embed player itself requests
    user_agent: str  # the declared identity, sent on every request
    max_retries: int = 1  # at most one retry, same identity, same address, after backoff_s
    backoff_s: float = 2.0


@dataclass
class PostRef:
    """A post identified from a URL without any network access."""

    platform: str  # "douyin" | "xiaohongshu" | "bilibili" | "youtube" | "tiktok" | "weibo" | "toutiao" | "x" | "wechat" | "linkedin" | "web"
    post_id: str
    form: str  # the link form this URL is, e.g. "desktop", "mobile", "short", "share_text", "token_stripped"
    url: str
    extra: dict[str, Any] = field(default_factory=dict)  # e.g. {"xsec_token": "...", "user": "..."}

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.post_id}"


@dataclass
class GateDecision:
    url: str
    allowed: bool
    profile: str
    reason: str  # "allowed" | "robots_disallow" | "content_signal" | "profile" | "private_address" | "scheme"
    robots_url: str | None = None
    robots_group: str | None = None  # the User-agent group that matched ("*", "readproof", or None if no group)
    matched_rule: str | None = None  # e.g. "Disallow: /"
    robots_binding: bool = False  # did the decision follow robots (P0) or only log it (P1/P2)?
    content_signal: dict[str, str] | None = None


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int | None
    headers: dict[str, str]
    bytes: int
    sha256: str  # of the body; the snapshot's address in the evidence store
    fetched_at: str  # ISO 8601 UTC
    elapsed_ms: int
    origin: str  # network origin label, e.g. "residential:MY" or "datacenter:aws-us-east-1"
    user_agent: str
    method: str = "GET"
    redirects: list[str] = field(default_factory=list)
    error: str | None = None
    attempt: int = 1
    body: bytes | None = field(default=None, repr=False)  # never serialized; stored by sha256

    def to_log(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("body", None)
        return d


@dataclass
class WallVerdict:
    served: bool
    wall: WallType | None
    signal: str  # the evidence for the verdict, e.g. "visible_chars=0 bytes=72914 douyin-verify"
    visible_chars: int = 0


@dataclass
class Record:
    record_id: str
    post_key: str
    channel: Channel
    locator: dict[str, Any]
    text: str
    source_sha256: str  # snapshot this text was extracted from
    extractor: str  # e.g. "page:og", "whisper.cpp:ggml-small", "vision:VNRecognizeText", "oembed"
    fetched_at: str

    @staticmethod
    def make_id(post_key: str, channel: Channel | str, locator: dict[str, Any], text: str) -> str:
        ch = channel.value if isinstance(channel, Channel) else channel
        raw = json.dumps([post_key, ch, locator, text], ensure_ascii=False, sort_keys=True)
        return "r_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["channel"] = self.channel.value
        return d


@dataclass
class ReadResult:
    url: str  # what the caller passed (may be share text)
    post: PostRef | None
    served: bool
    wall: WallType | None
    wall_signal: str
    records: list[Record] = field(default_factory=list)
    snapshots: list[str] = field(default_factory=list)  # sha256 of every body fetched
    fetches: list[FetchResult] = field(default_factory=list)
    gate: list[GateDecision] = field(default_factory=list)
    reader: str | None = None  # which reader served it
    cost_usd: float = 0.0
    seconds: float = 0.0

    def channels(self) -> set[Channel]:
        return {r.channel for r in self.records}


@dataclass
class Citation:
    record_id: str
    quote: str
    verified: bool = False  # set by code (verify.py), never by a model


@dataclass
class Answer:
    question: str
    text: str | None  # None when abstained
    abstained: bool
    cause: str | None  # a WallType value, "unsupported" (no verified evidence), or None
    citations: list[Citation] = field(default_factory=list)
    channel: Channel | None = None  # channel of the first verified citation
    cost_usd: float = 0.0
    seconds: float = 0.0
    llm_calls: int = 0


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
