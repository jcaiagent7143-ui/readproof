# Datasheet: ReadProof-Bench v1 (following Gebru et al., "Datasheets for Datasets")

## Motivation
- **Purpose.** Measure whether AI search agents actually obtain the social-media posts users paste — across link forms,
  platforms and access profiles — and whether they answer from pages they never read. Existing search benchmarks either use
  fixed text corpora or pre-extracted content, so they skip the acquisition step this benchmark measures.
- **Creator.** Loong Nian Chew (LinkDigest). No external funding. Competing interest: the author operates LinkDigest, a
  commercial service that reads social-media links; no LinkDigest production route is used by any system in the reported results.

## Composition
- **Track A (access).** 145 public posts on nine platforms (Douyin, Xiaohongshu/RedNote, Bilibili, YouTube, TikTok, Weibo,
  Toutiao, X, WeChat articles), discovered on 2026-11-21 through public web search; up to four link forms per post
  (522 post × form items). Each item carries the post URL as found, its form, the platform, the search-result title used as the
  reference fingerprint, the topic domain and language. Released results contain no page text.
- **Track K (planted canaries).** Seven posts published by the author on the author's own accounts (Douyin, Bilibili, RedNote video,
  RedNote image note, YouTube, TikTok, X) on 2026-11-21. Each hides random codes in exactly one channel: spoken only, on screen
  only, caption only, author comment only, or inside one image. The answer key was committed by SHA-256 before any system ran
  (bench/canary/COMMITMENT.md) and is published after evaluation; the media are released in full (the author holds the rights).
- **What is not included.** No third-party media, no page text, no user accounts, no personal data, no private posts.

## Collection process
- Track A seeds were found with web search (no platform was crawled to build the seed list). Systems then read each link form
  once from a single residential connection (Malaysia) at a rate of at most one request every five seconds per site, with a
  circuit breaker after six consecutive refusals and at most 300 requests per platform per run.
- The ReadProof harness sent a declared user agent, no cookies, no proxies, at most one retry after 429/5xx, and never answered
  a captcha or computed a platform signature (rules pinned by tests in tests/test_rules.py). Baseline systems ran as shipped.

## Uses
- Intended: evaluating reading, access handling, abstention and channel coverage of search agents and harnesses on social media.
- Not intended: bulk collection of social-media content, re-identification of authors, or circumventing any platform's access rules.

## Distribution and maintenance
- Code: Apache-2.0. Annotations, seeds and results: CC BY 4.0 (bench/LICENSE-DATA.txt). Canary media: CC BY 4.0, by the author.
- Third-party posts are referenced by URL, platform identifier and short title only; a post removed by its author or platform
  stays removed (the live track records it as not served).
- Maintainer: the author, through GitHub issues. Link decay is expected; later versions will add dated re-probes.
