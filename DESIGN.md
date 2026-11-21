# ReadProof — design (v0.1, 2026-11-21)

ReadProof is an open harness and a benchmark for agentic search over social media. Its thesis, from the
survey (`reports/社交媒体 AI 搜索 Agent 框架综述.md` in the LinkDigest repo): frontier agents fail on social
posts **before reasoning**. They fail at acquisition (verification pages, login walls, share tokens), at
noticing a wall (a 200 OK block page is treated as content), and at extraction (the answer is in speech,
on-screen text, carousel images or comments). No benchmark measures this pipeline.

- **Harness (`readproof/`, Apache-2.0)**: access rules enforced in code; typed walls instead of page text;
  official-player reading; local speech and on-screen text extraction; content-addressed evidence;
  verbatim quote verification; append-only trajectory log; MCP tools.
- **Benchmark (`bench/`, annotations CC BY 4.0)**: Track A (access across link forms), Track B
  (channel-located evidence), Track K (planted-canary panel on the author's own posts, exact gold),
  Track C (counted aggregation; pilot).

## 1. Non-negotiable rules (each pinned by a test in `tests/test_rules.py`)

1. **Declared identity.** Every request sends `User-Agent: ReadProof/<version> (+https://github.com/jcaiagent7143-ui/readproof)`.
   The browser reader appends the same token to Chromium's UA. Never a phone or desktop browser string.
2. **No cookies carried.** The HTTP client never sends a `Cookie` header and keeps no jar. The browser reader
   starts every read in a fresh context with an empty jar and discards it after the read; it never imports
   cookies or storage state. No logged-in session is ever used.
3. **No captcha is answered, no signature is generated, no device identity is forged.** Code that computes a
   platform's request signature (X-Bogus, a_bogus, x-s, w_rid computed outside the platform's own page) must not
   exist in this repository. The official player or page may compute what it computes inside the browser.
4. **No change of identity or address after a refusal.** No proxies (environment proxies are ignored), no
   rotation. At most one retry, same identity, after a back-off, and only after 429, 5xx or a transport error.
   Never after 403, 412, a challenge or a login wall.
5. **Public addresses only** (SSRF guard): refuse private, loopback, link-local, multicast and reserved IPs,
   after DNS resolution, on every hop.
6. **A wall never reaches a model as page text.** A read returns Records only when a wall verdict says served.
7. **Robots and Content-Signal are always evaluated and logged.** Under P0 they are binding.
8. **Platform deny list.** LinkedIn is refused under every profile (its terms prohibit automated access and its
   robots.txt disallows all bots); reason `terms`.

## 2. Access profiles (`readproof/profiles.py`)

| Profile | Robots / Content-Signal | Who triggers | Browser | Official-player media |
|---|---|---|---|---|
| P0 strict | binding for our declared token, else the `*` group (RFC 9309); `ai-input=no` refuses | anyone | yes | yes, if allowed |
| P1 user-initiated | evaluated and logged, not binding (single fetch of a user-supplied link; RFC 9309 scopes robots to crawlers) | a user supplied this link | yes | yes |
| P2 user-assisted | not applicable: content a person captured or uploaded | a person | no | no |

Every result table reports the profile and the network origin (`residential:<cc>` or `datacenter:<provider>`).

## 3. Modules and ownership

```
readproof/types.py        shared dataclasses (fixed contract)
readproof/interfaces.py   Protocols: Log, Store, Gate, Net, Budget, PlatformSpec, Reader, Context, ReaderOutput
readproof/profiles.py     P0, P1, P2 + USER_AGENT
readproof/robots.py       RFC 9309 parser/matcher (+ Content-Signal), cached per origin for 24 h
readproof/policy.py       PolicyGate(profile, robots, deny list, SSRF) -> GateDecision
readproof/net.py          HttpNet: httpx, declared UA, no cookies, no proxies, SSRF per hop, retry rule, origin label
readproof/log.py          JsonlLog (append-only, fsync on close), event ids, replay reader
readproof/evidence.py     FileStore: blobs/<sha[:2]>/<sha>, records.jsonl, index by post_key
readproof/budget.py       PricedBudget: per-tool USD/seconds, caps, summary
readproof/platforms/      one module per platform implementing PlatformSpec; registry in __init__.py
readproof/canonical.py    extract_url(share_text); resolve(url, ctx): short links one hop at a time (HEAD, no follow), each hop gated;
                          rewrite to the canonical form without fetching a disallowed hop
readproof/walls.py        visible_text(html) and judge(fetch, text, spec) -> WallVerdict (platform rules first, then generic)
readproof/readers/        oembed.py, page.py, browser.py, embed.py (official player; keeps media the player requests)
readproof/extract/        asr.py (whisper.cpp local), ocr.py (Apple Vision via tools/vision_ocr.swift; tesseract fallback),
                          frames.py (ffmpeg scene + uniform sampling, dHash dedup), vlm.py (optional frame/image description)
readproof/verify.py       norm/locate/verify_quote (ported from LinkDigest verify.py), support checks
readproof/retrieve.py     per-channel char-n-gram BM25 over Records + reciprocal-rank fusion
readproof/llm.py          OpenAI-compatible client (OpenRouter free models by default), response cache by request hash
readproof/agent.py        read–verify–count loop (Answer with citations, typed abstention, one re-ask)
readproof/aggregate.py    label_posts + count (code) + adjusted classify-and-count with bootstrap CI
readproof/harness.py      Harness.read(url) and Harness.answer(question, urls): wires everything
readproof/mcp_server.py   MCP tools: read, search_records, verify, label_posts, count, budget
readproof/cli.py          `readproof read URL --profile P1`, `readproof bench ...`
bench/                    seeds, builders, baseline systems, scoring
paper/                    LaTeX + TikZ figures
```

## 4. The read path (`Harness.read`)

1. `canonical.extract_url(text)` pulls the first URL out of app share text (「复制打开抖音…」, 小红书 share text).
2. `gate.check(url)`; a refusal returns `not_served{policy}` with no request sent.
3. `canonical.resolve`: a short link (v.douyin.com, xhslink.com/.cn, b23.tv, vm/vt.tiktok.com, t.co, youtu.be) is
   expanded one hop at a time with HEAD (GET if HEAD fails), redirects not followed by the client; every hop is gated;
   it stops as soon as a platform `parse()` recognises the Location. Unknown short code → redirect to home → `soft_404`.
4. `spec.parse` → PostRef; `spec.canonical` chooses the URL to read. Rewrites never fetch the disallowed hop.
5. Reader chain per platform, cheapest first: `oembed` → `page` → `embed` (official player, media) → `browser`.
   Each fetch is judged by `walls.judge`; the first served reader supplies Records; walls are recorded with their
   evidence (bytes, visible chars, signature).
6. Extraction (budgeted): CAPTION/PAGE records from the served page; SPEECH from media via whisper.cpp; ON_SCREEN from
   frames via Apple Vision OCR; IMAGE_TEXT from carousel images; COMMENT only when the public page itself serves them.
7. Every body goes to the store; every Record names the snapshot it came from.

## 5. Wall taxonomy and signatures (`readproof/walls.py`, platform `judge`)

Generic rules (after platform rules): HTTP 429 → rate_limited; 404/410 → deleted; 401 → login; redirect to a login path
→ login; redirect to the site root or a generic listing → soft_404; Cloudflare / Akamai / Tencent / ByteDance challenge
markers → challenge; visible text < 40 characters with no title → empty; transport error → network.
Known platform signatures (from LinkDigest's production logs): Douyin/Toutiao verification page (~72,914 bytes,
0 visible characters, `verify` scripts); Xiaohongshu `/404/sec_…`, code 300031 (JS wall), 「页面不见了」, `/login?redirectPath=`;
Bilibili `-412` (refused address), `-404`/啥都木有 (deleted); Weibo `Sina Visitor System` / passport redirect;
Douyin player `status_self_see` / declined post; WeChat 「环境异常」 / 「该内容已被发布者删除」; X login interstitial.

## 6. Link forms (Track A generator, `spec.forms`)

Desktop and mobile URLs; app share text with the URL embedded; short links (only real ones, from a share button);
`xsec_token` kept / stripped / altered (expired); rednote.com vs xiaohongshu.com; `/explore/` vs `/discovery/item/`;
Toutiao `/group/` vs `/article/` vs m.toutiao.com; Bilibili BV vs av, `?p=`, b23.tv; YouTube watch / shorts / youtu.be /
`?si=`; TikTok `/@u/video/` vs vm.tiktok.com; Weibo `weibo.com/<uid>/<bid>` vs `m.weibo.cn/detail/<mid>`; X `x.com` vs
`twitter.com` vs `mobile.`; controls: one short-code character case-flipped, a deleted post, a self-visible post.

## 7. Benchmark (`bench/`)

- **Track A — access.** ~120 public posts across 9 platforms × their link forms. Gold per post: existence and a reference
  fingerprint (title/caption) from the discovery source and a reference capture; controls with known wall types.
  Scored per link form: served (fingerprint match), correct abstention, wall-type accuracy on controls, answer-from-wall
  (the system answers although its read was a wall), honesty penalty (P1 − P0 coverage), origin effect.
- **Track K — planted canaries.** The author's own posts on Douyin, Bilibili, YouTube, TikTok, RedNote/Xiaohongshu and X,
  each carrying distinct random codes in exactly one channel: spoken only, on screen only, caption only, pinned comment
  only, carousel image only. Exact gold, no contamination (codes differ per post), media releasable (author holds rights).
  A right code proves a read; a different code is a fabrication; "not found" is an abstention.
- **Track B — channel-located evidence.** Questions answerable from exactly one channel of one third-party post. Gold
  from the agreement of two independent extractors (e.g. whisper.cpp and an omni model for speech; Apple Vision and a
  VLM for on-screen text), closed-book filter (a strong model fails without the read), answerability filter (it
  passes with the gold record), author audit of a sample.
- **Track C — counting (pilot).** Sets of posts on one topic; per-post labels with quotes; "N of M served posts".
- **Release:** IDs, URLs, hashes, spans and short quotes for third-party content (never media); full media for Track K;
  hidden test answers for a leaderboard; canary GUID in every file.

## 8. Systems (`bench/systems/`)

All model-using systems share one backbone per run (default `google/gemma-4-26b-a4b-it:free`, open weights).
`closed_book` (question + URL string); `naive_fetch` (GET as shipped by common agent loaders, HTML→text, 20k chars);
`reader_service` (r.jina.ai markdown); `browser_text` (headless Chromium innerText); `readproof` (ours, P1) and its
ablations (−walls, −canonical, −speech, −on_screen, −verify, P0, datacenter origin); commercial assistants (manual
audit on Track K). LinkDigest production is evaluated only internally, never in the paper (it uses routes outside P0/P1).

## 9. Metrics (`bench/score.py`)

Coverage@form; abstention precision/recall; answer-from-wall rate; wall-type accuracy; channel accuracy; exact-code
accuracy (Track K); quote validity (exact substring of a stored Record); count error (Track C); USD and seconds per
correct answer, split into media extraction and model tokens; compliance log checks (no cookie, declared UA, retries).
Paired bootstrap 95% CIs (10,000 resamples) for every comparison.

## 10. Builders' rules

- Do not post, publish, push to a public place, spend money, create accounts, or use any logged-in browser session.
- Do not touch LinkDigest production systems or data. Read its source in `~/.cache/ld-test/repo/linkdigest/` for platform knowledge only.
- Live network checks: at most 20 requests per platform per builder session, ≥2 s apart per host, declared UA only.
- Unit tests run offline with fixtures. `uv run pytest -q` must pass.
- Python ≥ 3.12. Core deps: httpx. Extras: `browser` (playwright), `mcp` (mcp). Typed, small functions, docstrings that say why.
- Never modify `readproof/types.py` or `readproof/interfaces.py`; propose changes in your report instead.

## 3.1 Function names every builder relies on (fixed)

```python
# readproof/profiles.py
P0: Profile; P1: Profile; P2: Profile
def get(name: str) -> Profile
# readproof/robots.py
class RobotsTxt:                      # parsed file
    @classmethod
    def parse(cls, text: str) -> "RobotsTxt"
    def decide(self, user_agent_token: str, path_and_query: str) -> tuple[bool, str | None, str | None]  # allowed, group, rule
    content_signal: dict[str, str]    # e.g. {"search": "yes", "ai-input": "yes", "ai-train": "no"}
class RobotsCache:
    def __init__(self, fetch: Callable[[str], FetchResult], ttl_s: int = 86400)
    def for_url(self, url: str) -> tuple[RobotsTxt | None, str]   # parsed file or None, and status "ok"|"unavailable"|"unreachable"
# readproof/policy.py
DEFAULT_DENY: dict[str, str]          # {"linkedin.com": "terms"}
class PolicyGate:
    def __init__(self, profile: Profile, robots: RobotsCache | None = None, deny: dict[str, str] = DEFAULT_DENY,
                 resolve: Callable[[str], list[str]] | None = None, log: Log | None = None)
    def check(self, url: str, *, purpose: str = "read") -> GateDecision
# readproof/net.py
class HttpNet:
    def __init__(self, profile: Profile, *, origin: str = "residential:unknown", gate: Gate | None = None,
                 timeout: float = 20.0, transport: httpx.BaseTransport | None = None, log: Log | None = None)
    def fetch(self, url, *, method="GET", headers=None, follow_redirects=False, max_bytes=50_000_000, purpose="read") -> FetchResult
# readproof/log.py
class JsonlLog: def __init__(self, path: str, run_id: str | None = None); def emit(self, event, **fields) -> str; def close(self)
def read_log(path: str) -> Iterator[dict]
# readproof/evidence.py
class FileStore: def __init__(self, root: str)   # put/get/add_records/records_for as in interfaces.Store
# readproof/budget.py
class BudgetExceeded(Exception); class PricedBudget: def __init__(self, cap_usd: float = 1.0, log: Log | None = None)
# readproof/platforms/__init__.py
REGISTRY: list[PlatformSpec]; def for_url(url: str) -> PlatformSpec | None; def by_name(name: str) -> PlatformSpec
# readproof/canonical.py
def extract_url(text: str) -> str | None
@dataclass class Resolution: input: str; url: str | None; ref: PostRef | None; spec: PlatformSpec | None;
                             fetches: list[FetchResult]; gate: list[GateDecision]; verdict: WallVerdict | None
def resolve(text_or_url: str, ctx: Context) -> Resolution
# readproof/walls.py
def visible_text(html: str) -> tuple[str, str]           # (title, visible text)
def judge(fetch: FetchResult, spec: PlatformSpec | None = None) -> WallVerdict
# readproof/verify.py
def norm(text: str) -> str; def locate(quote, passages, *, across=False) -> int | None
def verify_quote(quote: str, record: Record) -> bool; def substantial(quote: str) -> bool
# readproof/harness.py
class Harness:
    def __init__(self, profile: Profile, *, root: str = "runs", origin: str = "residential:unknown", options: dict | None = None)
    def read(self, text_or_url: str) -> ReadResult
```
Test doubles: `readproof/testing.py` (MemoryLog, MemoryStore, NullBudget, AllowGate, FakeNet/Scripted, make_context).
