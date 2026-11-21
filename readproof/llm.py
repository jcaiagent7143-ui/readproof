"""OpenAI-compatible chat client with a hard cost rule and an on-disk cache.

Cost rule (owner, 2026-11-21: the whole study may cost at most $3, ideally nothing): only model ids ending in
":free" are allowed unless READPROOF_ALLOW_PAID=1. A refused model raises PaidModelRefused before any request.

Every response is cached by the SHA-256 of the request (model, messages, temperature, max_tokens, json flag), so a
re-run of an experiment replays the same answers at no cost and a reviewer can re-score our runs exactly.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from . import USER_AGENT

DEFAULT_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemma-4-26b-a4b-it:free"  # open weights; 31b free tier was rate-limited upstream 11-21


class PaidModelRefused(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    cached: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def json(self) -> Any:
        """The first JSON object or array in the reply (models wrap JSON in prose or code fences)."""
        return parse_json(self.text)


def parse_json(text: str) -> Any:
    t = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = t.find(opener)
        if start < 0:
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError(f"no JSON object in reply: {t[:200]!r}")


def check_model_allowed(model: str) -> None:
    if not model.endswith(":free") and os.environ.get("READPROOF_ALLOW_PAID") != "1":
        raise PaidModelRefused(
            f"{model} is a paid model; the study's cost rule allows only ':free' models "
            "unless READPROOF_ALLOW_PAID=1 is set deliberately")


class ChatClient:
    def __init__(self, model: str | None = None, *, base_url: str | None = None, api_key: str | None = None,
                 cache_dir: str | Path = "runs/llm_cache", log: Any | None = None, timeout: float = 180.0,
                 max_retries: int = 6, transport: httpx.BaseTransport | None = None) -> None:
        self.model = model or os.environ.get("READPROOF_LLM_MODEL", DEFAULT_MODEL)
        self.base_url = (base_url or os.environ.get("READPROOF_LLM_BASE", DEFAULT_BASE)).rstrip("/")
        self.api_key = api_key or os.environ.get("READPROOF_LLM_KEY") or os.environ.get("OPENROUTER_API_KEY", "")
        self.cache_dir = Path(cache_dir)
        self.log = log
        self.max_retries = max_retries
        self._client = httpx.Client(timeout=timeout, trust_env=False, transport=transport)

    def _key(self, payload: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def chat(self, messages: list[dict[str, Any]], *, model: str | None = None, temperature: float = 0.0,
             max_tokens: int = 1024, json_mode: bool = False, purpose: str = "") -> LLMResult:
        model = model or self.model
        check_model_allowed(model)
        payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        key = self._key(payload)
        path = self.cache_dir / key[:2] / f"{key}.json"
        if path.exists():
            data = json.loads(path.read_text("utf-8"))
            res = LLMResult(text=data["text"], model=data["model"], prompt_tokens=data.get("prompt_tokens", 0),
                            completion_tokens=data.get("completion_tokens", 0), cost_usd=0.0, seconds=0.0, cached=True)
            self._emit(res, key, purpose)
            return res
        if not self.api_key:
            raise LLMError("no API key: set READPROOF_LLM_KEY (or OPENROUTER_API_KEY)")
        headers = {"Authorization": f"Bearer {self.api_key}", "User-Agent": USER_AGENT,
                   "HTTP-Referer": "https://github.com/jcaiagent7143-ui/readproof", "X-Title": "ReadProof"}
        t0 = time.monotonic()
        last = ""
        for attempt in range(self.max_retries):
            try:
                r = self._client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            except httpx.HTTPError as exc:
                last = repr(exc)
                time.sleep(min(60, 2 ** attempt))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = f"{r.status_code} {r.text[:200]}"
                retry_after = r.headers.get("retry-after")
                time.sleep(float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else min(90, 3 * 2 ** attempt))
                continue
            if r.status_code != 200:
                raise LLMError(f"{r.status_code} {r.text[:300]}")
            data = r.json()
            if "error" in data:
                last = json.dumps(data["error"])[:300]
                time.sleep(min(60, 2 ** attempt))
                continue
            choice = (data.get("choices") or [{}])[0]
            text = ((choice.get("message") or {}).get("content")) or ""
            usage = data.get("usage") or {}
            res = LLMResult(text=text, model=data.get("model", model), prompt_tokens=int(usage.get("prompt_tokens") or 0),
                            completion_tokens=int(usage.get("completion_tokens") or 0), cost_usd=float(usage.get("cost") or 0.0),
                            seconds=round(time.monotonic() - t0, 2), raw=data)
            if text:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"text": text, "model": res.model, "prompt_tokens": res.prompt_tokens,
                                            "completion_tokens": res.completion_tokens, "request": payload},
                                           ensure_ascii=False), "utf-8")
            self._emit(res, key, purpose)
            return res
        raise LLMError(f"gave up after {self.max_retries} attempts: {last}")

    def _emit(self, res: LLMResult, key: str, purpose: str) -> None:
        if self.log is not None:
            self.log.emit("llm", model=res.model, purpose=purpose, request_sha256=key, cached=res.cached,
                          prompt_tokens=res.prompt_tokens, completion_tokens=res.completion_tokens,
                          cost_usd=res.cost_usd, seconds=res.seconds)
