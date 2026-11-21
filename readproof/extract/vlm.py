"""Optional visual description of frames and images through an OpenAI-compatible chat endpoint.

Off unless the read's options say {"vlm": True}: descriptions are a model's words about a picture, not text the
post carries, so they are a separate channel (FRAME {"t"} / IMAGE {"index"}) and never mixed with OCR.

Cost rule (owner, 2026-11-21): the whole research may cost at most $3, ideally $0. Only model ids ending in ":free"
are called; any other id is refused with PaidModelRefused before a request is built, unless the environment sets
READPROOF_ALLOW_PAID=1. The default is google/gemma-4-31b-it:free.

Configuration: READPROOF_LLM_BASE (default https://openrouter.ai/api/v1), READPROOF_LLM_KEY, READPROOF_VLM_MODEL.
This is the one module in the read path that talks to a model API; like readproof/llm.py it uses httpx directly
(POST with a JSON body, which Net does not carry), with the declared User-Agent and environment proxies ignored.
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any

from ..interfaces import Context
from ..types import Channel, Record

DEFAULT_MODEL = "google/gemma-4-31b-it:free"
DEFAULT_BASE = "https://openrouter.ai/api/v1"
MAX_ITEMS = 8  # free endpoints are rate-limited; a read describes at most this many frames or images
PROMPT = (
    "Describe what this {what} shows in one to three plain sentences: people, objects, actions, setting. "
    "Do not transcribe text that appears in it and do not guess what is not visible."
)


class PaidModelRefused(RuntimeError):
    """A model id that does not end in ':free' was requested without READPROOF_ALLOW_PAID=1."""


def model_name() -> str:
    return os.environ.get("READPROOF_VLM_MODEL") or DEFAULT_MODEL


def check_model_allowed(model: str) -> None:
    """Raise PaidModelRefused unless `model` ends with ':free' or READPROOF_ALLOW_PAID=1 is set."""
    if model.endswith(":free") or os.environ.get("READPROOF_ALLOW_PAID") == "1":
        return
    raise PaidModelRefused(
        f"refusing model {model!r}: ReadProof only calls models whose id ends with ':free' (research cost cap "
        "$3, ideally $0). Set READPROOF_ALLOW_PAID=1 to allow a paid model."
    )


def enabled(ctx: Context) -> bool:
    return bool(ctx.options.get("vlm"))


def _describe(client: Any, base: str, model: str, image_path: str, what: str) -> tuple[str, float]:
    with open(image_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT.format(what=what)},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]}],
        "max_tokens": 200,
        "temperature": 0,
        "usage": {"include": True},
    }
    resp = client.post(base.rstrip("/") + "/chat/completions", json=body)
    resp.raise_for_status()
    data = resp.json()
    text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    cost = float(((data.get("usage") or {}).get("cost")) or 0.0)
    return text, cost


def describe(items: list[tuple[Any, str, str]], ctx: Context, *, kind: Channel, post_key: str,
             fetched_at: str) -> list[Record]:
    """FRAME (items = (t, path, sha)) or IMAGE (items = (index, path, sha)) Records; [] when disabled."""
    if not enabled(ctx) or not items:
        return []
    if kind not in (Channel.FRAME, Channel.IMAGE):
        raise ValueError("vlm.describe makes FRAME or IMAGE records only")
    model = model_name()
    check_model_allowed(model)  # before anything is sent
    key = os.environ.get("READPROOF_LLM_KEY")
    if not key:
        ctx.log.emit("extract_skipped", tool="vlm", reason="READPROOF_LLM_KEY is not set")
        return []
    import httpx

    base = os.environ.get("READPROOF_LLM_BASE") or DEFAULT_BASE
    what = "video frame" if kind is Channel.FRAME else "image from a social media post"
    records: list[Record] = []
    t0 = time.monotonic()
    usd = 0.0
    headers = {"User-Agent": ctx.profile.user_agent, "Authorization": f"Bearer {key}"}
    with httpx.Client(headers=headers, timeout=90, trust_env=False) as client:
        for key_, path, sha in items[: int(ctx.options.get("vlm_max", MAX_ITEMS))]:
            try:
                text, cost = _describe(client, base, model, path, what)
            except Exception as exc:  # one failed description must not fail the read
                ctx.log.emit("extract_error", tool="vlm", model=model, error=f"{type(exc).__name__}: {exc}"[:300])
                continue
            usd += cost
            if not text:
                continue
            locator = {"t": float(key_)} if kind is Channel.FRAME else {"index": int(key_)}
            records.append(Record(record_id=Record.make_id(post_key, kind, locator, text), post_key=post_key,
                                  channel=kind, locator=locator, text=text, source_sha256=sha,
                                  extractor=f"vlm:{model}", fetched_at=fetched_at))
    seconds = round(time.monotonic() - t0, 3)
    ctx.budget.charge("vlm", usd=usd, seconds=seconds, model=model, items=len(items), records=len(records))
    ctx.log.emit("extract", tool="vlm", model=model, records=len(records), seconds=seconds, usd=usd)
    return records
