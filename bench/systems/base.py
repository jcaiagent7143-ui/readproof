"""The common interface every benchmark system implements, and what the baselines share.

    read(text_or_url)                 -> SysRead
    answer(question, url, read=None)  -> SysAnswer

A SysRead says what the system obtained (status, bytes, the visible text it would hand a model) and an observer's
wall label: for readproof the harness's own ReadResult.wall, for a baseline readproof.walls.judge applied to what
the baseline fetched. The label is an observation for the tables; whether a read was *served* is decided by the gold
fingerprint check in bench/score.py, never by the label.

Baselines answer from text with no verification: the model gets the question and the first MODEL_CHARS characters
of what the system obtained, and either answers or says NOT_FOUND. The URL is deliberately not in the prompt: an
answer then can only come from the obtained text, which is what the answer-from-wall probe measures (with the URL
the rate could only go up).
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

TEXT_CAP = 30_000
MODEL_CHARS = 20_000
ANSWER_MAX_TOKENS = 300

PROBE_QUESTION = ("What is this post about? Reply in one sentence, or NOT_FOUND if the content does not show the "
                  "post.")

BASELINE_SYSTEM = (
    "You answer questions about a social-media post. You are given the text a tool obtained when it opened the "
    "user's link. Use only that text. If the text does not contain the answer, reply with exactly NOT_FOUND.")
CLOSED_BOOK_SYSTEM = (
    "You answer questions about a social-media post that a user linked. You cannot open the link. "
    "If you do not know the answer, reply with exactly NOT_FOUND.")

_URL = re.compile(r"https?://\S+", re.I)
_NOT_FOUND = re.compile(r"\bNOT[_ ]?FOUND\b", re.I)
# A reply that declines in words instead of the token still declines; counted as an abstention (conservative:
# it can only lower a baseline's answer-from-wall rate).
_DECLINE = re.compile(
    r"(does(?:n't| not) (?:show|contain|include|display|provide|mention|appear)|do(?:es)? not have (?:any )?"
    r"(?:information|content)|no (?:post|content|information) (?:is )?(?:shown|visible|available|provided|present)|"
    r"not (?:shown|visible|available|present|included|provided|displayed)|cannot (?:be )?(?:determine|find|see|tell|"
    r"identify|answer)|can't (?:determine|find|see|tell)|unable to|there is no (?:post|content|code)|"
    r"无法|不能确定|没有(?:显示|找到|提供|包含)|未(?:显示|找到|提供|包含)|看不到|找不到|不包含)", re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_COMMIT: dict[str, str] = {}


def readproof_commit() -> str:
    """`git rev-parse --short HEAD` of the repository, plus "+dirty" when readproof/ has uncommitted changes, so
    every results row names the harness version that produced it."""
    if "v" not in _COMMIT:
        import subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        try:
            head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True,
                                  timeout=10).stdout.strip() or "unknown"
            dirty = subprocess.run(["git", "status", "--porcelain", "--", "readproof"], cwd=root, capture_output=True,
                                   text=True, timeout=10).stdout.strip()
        except Exception:
            head, dirty = "unknown", ""
        _COMMIT["v"] = head + ("+dirty" if dirty else "")
    return _COMMIT["v"]


def readproof_tree() -> str:
    """SHA-256 (first 12 hex) of the readproof/ package source: every .py file's path and bytes, sorted. With
    uncommitted changes this, not the commit, names the exact harness that produced a row."""
    if "tree" not in _COMMIT:
        from pathlib import Path

        root = Path(__file__).resolve().parents[2] / "readproof"
        h = hashlib.sha256()
        for f in sorted(root.rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            h.update(str(f.relative_to(root)).encode("utf-8") + b"\0")
            h.update(f.read_bytes() + b"\0")
        _COMMIT["tree"] = h.hexdigest()[:12]
    return _COMMIT["tree"]


def first_url(text: str) -> str | None:
    """What a generic agent passes to its fetch tool: the first http(s) token, trailing ASCII punctuation off."""
    m = _URL.search(text or "")
    if not m:
        return None
    url = m.group(0)
    while url and url[-1] in ".,;:!?'\")]}>":
        url = url[:-1]
    return url or None


def sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def classify_reply(reply: str | None) -> tuple[bool, str]:
    """(abstained, how): 'empty', 'token' (NOT_FOUND), 'declined' (in words), or 'answered'."""
    t = (reply or "").strip()
    if not t:
        return True, "empty"
    if _NOT_FOUND.search(t):
        return True, "token"
    if _DECLINE.search(t) and len(t) < 400:
        return True, "declined"
    return False, "answered"


@dataclass
class SysRead:
    system: str
    url: str  # the input exactly as given (a URL or app share text)
    status: int | None
    final_url: str | None
    bytes: int
    visible_chars: int
    text: str  # what this system would hand a model (capped at TEXT_CAP)
    wall: str | None  # readproof: ReadResult.wall; baselines: walls.judge observer label; None = served by that label
    seconds: float
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    raw: Any = field(default=None, repr=False, compare=False)  # ReadResult for readproof; never serialized

    def to_json(self) -> dict[str, Any]:
        return {"system": self.system, "url": self.url, "status": self.status, "final_url": self.final_url,
                "bytes": self.bytes, "visible_chars": self.visible_chars, "text": self.text, "wall": self.wall,
                "seconds": round(self.seconds, 3), "error": self.error, "meta": self.meta}


@dataclass
class SysAnswer:
    system: str
    question: str
    url: str
    answer: str | None
    abstained: bool
    cause: str | None  # readproof: wall type / not_found / unsupported; baselines: token / declined / empty / error
    evidence_channel: str | None
    seconds: float
    llm_calls: int
    trace: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"system": self.system, "question": self.question, "url": self.url, "answer": self.answer,
                "abstained": self.abstained, "cause": self.cause, "evidence_channel": self.evidence_channel,
                "seconds": round(self.seconds, 3), "llm_calls": self.llm_calls, "trace": self.trace,
                "error": self.error}


class CallBudgetExceeded(RuntimeError):
    """The study's own daily ceiling on uncached model calls was reached."""


class CountingLLM:
    """readproof.llm.ChatClient with a count of uncached calls and a hard daily ceiling (free endpoints)."""

    def __init__(self, client: Any, *, max_uncached: int = 700, ledger: str | None = None) -> None:
        self.client = client
        self.max_uncached = max_uncached
        self.ledger = ledger
        self.uncached = 0
        self.cached = 0
        self.errors = 0
        self._lock = threading.Lock()
        self._day = time.strftime("%Y-%m-%d")
        self._prior = self._ledger_count()

    def _ledger_count(self) -> int:
        """Uncached calls already made today by earlier runs of this study (one line per call in the ledger)."""
        if not self.ledger:
            return 0
        try:
            with open(self.ledger, "r", encoding="utf-8") as fh:
                return sum(1 for line in fh if line.startswith(self._day))
        except FileNotFoundError:
            return 0

    @property
    def today(self) -> int:
        return self._prior + self.uncached

    def is_cached(self, messages: list[dict[str, Any]], **kw: Any) -> bool:
        """Would ChatClient answer this from its cache? (Same payload and key as ChatClient.chat builds.)"""
        try:
            payload: dict[str, Any] = {"model": kw.get("model") or self.client.model, "messages": messages,
                                       "temperature": kw.get("temperature", 0.0), "max_tokens": kw.get("max_tokens", 1024)}
            if kw.get("json_mode"):
                payload["response_format"] = {"type": "json_object"}
            key = self.client._key(payload)
            return (self.client.cache_dir / key[:2] / f"{key}.json").exists()
        except Exception:
            return False

    patience_s: float = 0.0  # set > 0 to wait out upstream rate limits on the free endpoint
    poll_s: float = 90.0
    _cooldown_until: float = 0.0

    def _chat_patient(self, messages: list[dict[str, Any]], **kw: Any) -> Any:
        """ChatClient.chat, but an upstream 429 ("temporarily rate-limited upstream") is waited out: one attempt every
        ~90 s until patience_s has passed (the free endpoint recovers in short windows, so a steady poll finds them
        sooner than an exponential back-off). All waiting threads share one cool-down, so a rate-limited endpoint sees
        one attempt at a time, not one per worker."""
        deadline = time.monotonic() + self.patience_s
        k = 0
        while True:
            wait = self._cooldown_until - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                return self.client.chat(messages, **kw)
            except Exception as exc:
                text = str(exc)
                limited = "429" in text or "rate-limited" in text or "rate limit" in text.lower()
                if not limited or time.monotonic() >= deadline:
                    raise
                pause = self.poll_s * (1.0 + 0.2 * random.random())
                k += 1
                with self._lock:
                    self._cooldown_until = max(self._cooldown_until, time.monotonic() + pause)
                    self.errors += 1

    def chat(self, messages: list[dict[str, Any]], **kw: Any) -> Any:
        with self._lock:
            over = self.today >= self.max_uncached
        if over and not self.is_cached(messages, **kw):
            raise CallBudgetExceeded(f"{self.today} uncached model calls today (ceiling {self.max_uncached})")
        res = self._chat_patient(messages, **kw) if self.patience_s > 0 else self.client.chat(messages, **kw)
        with self._lock:
            if getattr(res, "cached", False):
                self.cached += 1
            else:
                self.uncached += 1
                if self.ledger:
                    with open(self.ledger, "a", encoding="utf-8") as fh:
                        fh.write(f"{time.strftime('%Y-%m-%d')}T{time.strftime('%H:%M:%S')} {kw.get('purpose', '')}\n")
        return res


def ask_text(llm: Any, system: str, question: str, text: str, *, purpose: str) -> tuple[str | None, bool, str, int,
                                                                                         str | None]:
    """One model call on obtained text. Returns (reply, abstained, how, calls, error)."""
    user = f"Text the tool obtained (first {MODEL_CHARS:,} characters):\n\"\"\"\n{(text or '')[:MODEL_CHARS]}\n\"\"\"\n\n" \
           f"Question: {question}"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        res = llm.chat(messages, max_tokens=ANSWER_MAX_TOKENS, purpose=purpose)
    except CallBudgetExceeded:
        raise
    except Exception as exc:  # the free endpoint gave up: recorded, never guessed
        return None, True, "error", 1, f"{type(exc).__name__}: {str(exc)[:300]}"
    reply = (res.text or "").strip()
    abstained, how = classify_reply(reply)
    return reply, abstained, how, 1, None


def ask_closed_book(llm: Any, question: str, url: str, *, purpose: str) -> tuple[str | None, bool, str, int, str | None]:
    messages = [{"role": "system", "content": CLOSED_BOOK_SYSTEM},
                {"role": "user", "content": f"Post link: {url}\n\nQuestion: {question}"}]
    try:
        res = llm.chat(messages, max_tokens=ANSWER_MAX_TOKENS, purpose=purpose)
    except CallBudgetExceeded:
        raise
    except Exception as exc:
        return None, True, "error", 1, f"{type(exc).__name__}: {str(exc)[:300]}"
    reply = (res.text or "").strip()
    abstained, how = classify_reply(reply)
    return reply, abstained, how, 1, None


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=False, default=str)
