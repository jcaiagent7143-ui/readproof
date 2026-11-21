# ReadProof

**Did the agent read it?** ReadProof is an open harness and a benchmark for agentic search over social media. It measures
whether an AI system actually obtains the post a user pastes — on Douyin, Xiaohongshu/RedNote, Bilibili, YouTube, TikTok,
Weibo, Toutiao, X and WeChat — under access rules that are enforced in code, and whether it answers from a page it never read.

- **Harness** (`readproof/`, Apache-2.0): declared identity, no cookies, no proxies, no captcha answering, no signature
  computation, at most one retry; robots.txt and Content-Signal evaluated on every request (binding under the strict profile);
  typed walls instead of page text; the official-player principle for media; local speech recognition (whisper.cpp) and
  on-screen text (Apple Vision); content-addressed evidence; quotes verified word for word; an append-only trajectory log.
- **Benchmark** (`bench/`, data CC BY 4.0): Track A measures access across the link forms users really paste; Track K uses
  posts planted by the author with codes hidden in exactly one channel (spoken, on screen, caption, comment, image).

## Results (2026-11-21, one residential origin; full tables in `paper/tables/`)

**Track A — 145 posts, 9 platforms, 522 post × link-form items.** Served = the post's reference fingerprint appears in what
the system obtained.

| System | Served |
|---|---|
| **ReadProof, user-initiated profile (P1)** | **81.8%** [78.4, 85.1] |
| ReadProof, strict robots profile (P0) | 55.1% [50.8, 59.4] |
| Headless browser text | 51.9% |
| Reader service (Jina Reader) | 51.8% |
| Plain HTTP fetch | 25.4% |

- Obeying robots.txt strictly costs **26.7 points** of coverage (P1 − P0): Xiaohongshu, Weibo, WeChat and X disallow every
  unnamed bot.
- The headless browser **answered from a block page in 30.2%** of the cases where it had obtained one; ReadProof returns a
  typed wall instead and never passes wall text to the model.

**Track K — 7 canary posts, 28 channel items.** ReadProof P1 read 9 codes correctly, the best open baseline 5. No system
invented a code, and no system read a pinned comment. Five commercial assistants asked the same questions in their default
web modes found 3 codes in 27 answers in total — 2 of them by Gemini on YouTube, Google's own platform.

## Quickstart

```bash
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -e '.[dev,browser]'
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest -q                       # the rules are tests: tests/test_rules.py
.venv/bin/readproof read "https://www.bilibili.com/video/<BV id>/" --profile P1 --json
```

Speech recognition uses `whisper-cli` (whisper.cpp) with a local model; on-screen text uses Apple Vision on macOS
(`tools/vision_ocr.swift`, compiled on first use), with tesseract as a fallback. Model calls go through any
OpenAI-compatible endpoint; by default only free models are allowed (`readproof/llm.py`).

## Benchmark

| Track | What it measures | Files |
|---|---|---|
| A — access | served / typed wall / answered-from-wall per link form, per access profile | `bench/seeds/`, `bench/trackA_run.py`, `bench/results/trackA_reads_public.jsonl` |
| K — planted canaries | which channels a system really reads; fabrication | `bench/trackK.py`, `bench/canary/COMMITMENT.md` (answer key committed by SHA-256 before any run) |

Third-party posts are referenced by URL, platform identifier and short title only; no page text or media is redistributed
(`bench/DATASHEET.md`). Scoring: `bench/score.py` (paired bootstrap 95% CIs).

## Paper

*Did the Agent Read It? ReadProof: Access-Aware Reading, Channel-Located Evidence and Verified Quotes for Agentic Search over
Social Media* — PDF in `paper/main.pdf`; citation in `CITATION.cff`.

## Disclosure

The author operates LinkDigest, a commercial service that reads social-media links. No LinkDigest production route is used by
any system in the reported results; the harness here is independent of it.

## License

Code: Apache-2.0 (`LICENSE`). Benchmark data and annotations: CC BY 4.0 (`bench/LICENSE-DATA.txt`).
