# Track A seed notes: trackA_posts_v1 (found 2026-11-21)

Seed file: `trackA_posts_v1.jsonl`, 145 posts, one JSON object per line, UTF-8, no duplicate URLs and no duplicate post IDs within a platform.

**How the posts were found.** Every post was found with the Claude WebSearch tool in `standard` mode. `extended` mode was never needed, because standard mode returned usable results for all nine platforms once the domain filter was used (see below). No post was opened or fetched. No browser or account was used, nothing was posted, and nothing was paid for. Everything comes from public web-search results. Nothing comes from LinkDigest data.

## Field conventions

- `platform` uses `douyin`, `xiaohongshu` (covers both xiaohongshu.com and rednote.com URLs), `bilibili`, `youtube`, `tiktok`, `weibo`, `toutiao`, `x` (covers x.com and twitter.com) or `wechat` (mp.weixin.qq.com).
- `domain` uses one of `food_recipes`, `travel`, `consumer_tech_ai`, `beauty_skincare`, `parenting`, `cars_evs`, `finance_news` or `entertainment_drama`.
- `title_or_snippet` is the result **title** exactly as the engine returned it. It keeps the engine's platform suffixes (" - 抖音", " - 小红书", "_哔哩哔哩_bilibili", " - YouTube", " - 今日头条", `Name on X: "…" / X`). It also keeps truncation marks ("…" on X, "..." on the Toutiao 微头条 item), doubled spaces and emoji. Nothing was taken from the search tool's prose summaries. Those summaries mix several results together and sometimes pin content on the wrong URL.
- `language` is the language of the shown title. `zh` covers Simplified and Traditional Chinese.
- `post_id` is derived from the URL only:
  - Douyin and TikTok: the numeric video ID.
  - XHS and rednote: the 24-hex note ID.
  - Bilibili: the BV ID, or `av…` for the single av-form URL.
  - YouTube: the `v` value, or the shorts/embed ID.
  - X: the status ID.
  - Weibo: the `mid`, `comos:<id>`, or the ttarticle `id`.
  - Toutiao: the article, 微头条 (`/w/`) or video ID.
  - WeChat: the `/s/<token>` value for short links, or `__biz=…&mid=…&idx=…` for long links.
- One extra field, `query_allowed_domains`, records the WebSearch domain filter that was sent with `query`. Without it a search cannot be repeated: the `site:` operator in the query text did not work (see "What did not work").

## Counts

| platform | food | travel | tech/AI | beauty | parenting | cars/EV | finance/news | ent./drama | total | languages |
|---|---|---|---|---|---|---|---|---|---|---|
| douyin | 2 | 2 | 2 | 2 | 2 | 2 | **1** | 2 | 15 | zh 15 |
| xiaohongshu / rednote | 2 | 2 | 3 | 2 | 2 | 2 | 2 | 2 | 17 (13 xiaohongshu.com, 4 rednote.com) | zh 14, en 3 |
| bilibili | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 16 | zh 16 |
| youtube | 3 | 2 | 2 | 2 | 2 | 2 | **1** | 2 | 16 | en 13, zh 2, ja 1 |
| tiktok | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 16 | en 13, zh 3 |
| weibo | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 16 | zh 16 |
| toutiao | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 16 | zh 16 |
| x | 2 | 3 | 2 | 2 | 2 | 2 | 2 | 2 | 17 | en 13, zh 3, ja 1 |
| wechat | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 16 | zh 16 |
| **total** | 19 | 19 | 19 | 18 | 18 | 18 | 16 | 18 | **145** | zh 101, en 42, ja 2 |

Approximate post dates, decoded from IDs where the ID carries a timestamp:

| platform | earliest | latest | before 2025 |
|---|---|---|---|
| Douyin | 2020-03 | 2026-10 | 2 posts |
| XHS | 2025-10 | 2026-09 | none |
| TikTok | 2023-08 | 2026-04 | 8 posts |
| X | 2019-04 | 2026-11 | 4 posts |
| Toutiao | 2024-11 | 2026-11 | 2 posts (the 微头条 `/w/` ID was not decoded) |

Bilibili, Weibo and WeChat IDs carry no plain timestamp. Some of those items are probably older, e.g. Bilibili `av19891708`. Expect some posts to be gone by probe time. That is useful, because it tests whether a system correctly reports that it could not read the post.

## What worked

The shape that worked everywhere was **topic words plus a platform word, with the tool's domain filter set to the platform**. This filter is `allowed_domains`, recorded in each row as `query_allowed_domains`. Topic words were given in the platform's language.

| platform | query shape | what came back |
|---|---|---|
| Douyin | `<topic> #<hashtag> 抖音 video`, e.g. `旅行攻略 收藏 #旅行推荐官 抖音 video` | `douyin.com/video/<id>` titled "<caption> - 抖音" |
| XHS | `<topic> 小红书 笔记`, e.g. `护肤 小红书 笔记` | `xiaohongshu.com/explore/<id>` titled "<note title> - 小红书" |
| rednote | English queries with the filter set to rednote.com, e.g. `rednote skincare routine discovery item` | `rednote.com/discovery/item/<id>` and `/en/discovery/item/<id>` (English titles on the `/en/` path) |
| Bilibili | `<topic> 视频`, e.g. `新能源 电车 评测 试驾 视频` | `bilibili.com/video/BV…/` titled "<title>_哔哩哔哩_bilibili" |
| YouTube | plain English topic, or Traditional-Chinese topic words such as `電動車 評測 試駕 中文` | `watch?v=` pages |
| TikTok | `<topic> video tiktok` | `tiktok.com/@user/video/<id>`, titled with the full caption |
| X | `<topic> status post` | `x.com/<user>/status/<id>` titled `Name on X: "<text>" / X` |
| Weibo | `<topic> 微博 detail` | `weibo.com/2/detail/<mid>` |
| Toutiao | `<topic> 今日头条 (文章)` | `toutiao.com/article/<id>/` titled "<title> - 今日头条" |
| WeChat | `<topic> 公众号 文章`, with `2026` added for freshness | With `2026`, the newer `/s/<token>` short links came back. Without it, results were mostly long-form `__biz` links, many to older articles (e.g. a 2017 travel ranking, a 2020-era homeschooling post) |

## What did not work

### The `site:` operator in the query text

**Ignored for most platforms.** `site:youtube.com/watch …`, `site:tiktok.com/@ …`, `site:x.com status …`, `site:xiaohongshu.com/explore …` and `site:m.weibo.cn/detail …` all returned other sites: Dailymotion, Lemon8, Google News story pages, Thread Reader, sina.cn and recipe blogs.

**Douyin: on-domain results, but every title was the crawler's user-agent string.** With `site:` in the query, every Douyin result was titled `Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)`:

| query | UA-titled results |
|---|---|
| `site:douyin.com/video 露营` | 9/9 |
| `site:douyin.com/note 护肤` | 8 of 8 douyin.com results (all `/video/` or `/hashtag/`, none `/note/`; the 9th result was a douban.com page) |
| `v.douyin.com 短剧` | 10/10 (`/htmlmap/` pages) |
| `"v.douyin.com" 复制此链接 打开Dou音搜索` | 8/10 |

Examples:
- `https://www.douyin.com/video/7661489803170761103`
- `https://www.douyin.com/video/7637805126647844105`
- `https://www.douyin.com/hashtag/1727461080987726`
- `https://www.douyin.com/htmlmap/newvideo_5_1`

The same kind of `douyin.com/video/<id>` page came back with real caption titles when the domain filter was used instead. This was not verified, since posts were not opened, but the likely explanation is that the index holds a second copy of Douyin pages whose title is the crawler's UA. That copy would be a bot-response or placeholder page, not the post. This is exactly Track A's "answers from a block page" case: a system whose retrieval lands on that copy has no post content to give. The UA-titled URLs were **kept out of the seed file**, because they have no fingerprint.

**Bilibili: hostname-only titles.** With `site:`, Bilibili returned `/video/av<number>/` URLs titled only `www.bilibili.com`. Examples:
- `https://www.bilibili.com/video/av117008515468111/`
- `https://www.bilibili.com/video/av116973820250659/`
- `https://www.bilibili.com/video/av58205065/`

### Short-link queries

Searching for `v.douyin.com`, `b23.tv` or `xhslink.com` returned docs, downloader repos and Douyin sitemap pages. None of them was a post.

## What web search could not find

This is a finding in its own right: it shows which post types web search does not index.

### Douyin
- **Personal-finance posts:** almost none. Two finance queries returned only SEO keyword pages (`/shipin/<id>`, `/zhuanti/<id>`) and `so.douyin.com` search pages. One stock-commentary video (`7473782091507944744`) turned up on the second query, so Douyin has only one finance seed.
- **Image posts (`douyin.com/note/<id>`, 图文):** never seen.
- **Short links:** no `v.douyin.com/<code>` links and no `iesdouyin.com/share/video/` pages. `iesdouyin.com` appeared only as `share/user/` profile links and `page.iesdouyin.com/traffic-aggregation/<id>` SEO pages.
- **Most results are not posts.** Across the 19 domain-filtered Douyin queries, only 33 of 187 results (about 18%) were post pages (`/video/<id>` or `m.douyin.com/share/video/<id>`). Seven queries returned no post at all: 红烧肉, 旅游攻略 vlog, 理财 基金 存钱, 年轻人 存钱, 房贷, 定投 and the 图文/note query. The rest were SEO or aggregation pages:
  - `so.douyin.com/s?…` search pages
  - `/shipin/<id>` keyword landing pages, titled like "红烧肉炖多久不油腻-抖音"; some are titled just "Douyin"
  - `/zhuanti/<id>`, `/hot/<id>`, `/jingxuan/<channel>` and `/htmlmap/…`

### Xiaohongshu / rednote
- **Note URLs are always tokenless.** Note URLs came back as `xiaohongshu.com/explore/<id>`, plus one old `/discovery/item/<id>`. None carried `xsec_token`. `xsec_token` appeared only on `user/profile/` URLs. The index therefore holds no tokenised note links; link forms with tokens have to be built in the probe run.
- **No `xhslink.com` short links.** None was indexed. The form was seen only inside third-party pages.
- **Many SEO Q&A pages.** `xiaohongshu.com/mobile/question/<n>` pages filled many result slots.
- **rednote.com notes are indexed separately.** They appear under `/discovery/item/<id>` and `/en/discovery/item/<id>`, and the `/en/` path carries English titles. The IDs use the same 24-hex format as xiaohongshu.com. Whether the same ID resolves on both hosts was not checked.
- **Cars:** few car-ownership notes were found. The query `小米SU7 用车 半年 真实 感受 小红书` returned 2 unrelated results.

### Bilibili
- **No `b23.tv` links.** None was indexed.
- **`m.bilibili.com` pages carry English, apparently machine-translated, titles.** Example: `https://m.bilibili.com/video/BV1eXN46JEZW`, titled "This time it really reached my limit! Altitude sickness special forces + 4000km bus ride, the sunset senior tour group is too exhausting [Part 2]_Bilibili". One `www.bilibili.com` result had an English title without the `_哔哩哔哩_bilibili` suffix: `https://www.bilibili.com/video/BV1zn4y1o7zB/`, titled "Top 5 Chinese Short Drama Rankings for the First Week of July". Both were **excluded**, because the fingerprint would be a translation.

### YouTube
- Nothing was missing.
- **No `youtu.be` URLs were indexed as results.** One appeared only inside a TikTok caption.

### TikTok
- **Many hub pages.** Result lists were dominated by `/discover/`, `/tag/` and `/channel/` hub pages.
- **Few individual Chinese-language posts.** Chinese queries mostly returned tag and discover pages.
- **`vm.tiktok.com` and `vt.tiktok.com` short links are indexed as standalone URLs**, with the title of the page they resolve to. See the URL forms below.

### Weibo
- **No `m.weibo.cn/detail/<mid>` URLs** (the requested shape) were ever returned.
- **Status pages have no usable title.**
  - `m.weibo.cn/status/<id>` pages are titled just `微博`, e.g. `https://m.weibo.cn/status/4929532836710435` and `https://m.weibo.cn/status/HwuPxjzUP?jumpfrom=weibocom`.
  - `weibo.com/<uid>/<bid>` pages are titled `微博正文 - 微博`, e.g. `https://weibo.com/3782417723/Jgsym0xsn`.
  - `weibo.com/2/detail/5348796542223747` is titled `微博正文`.
  - All of these are excluded.
- **Detail pages have summary titles, not post text.** What is indexed with titles is `weibo.com/2/detail/<mid>`. Those titles read like generated headline summaries (e.g. "秋冬仍需防晒 薇诺娜清透防晒乳修屏障兼防晒"), not the post's own words. A correct reader may not reproduce them word for word, so score Weibo semantically.
- **Many `comos:` SEO pages.** `weibo.com/2/detail/comos:<id>` pages are Sina "comos" articles. Many are GEO/SEO pieces ending in "+FAQ" (e.g. "国内美食最佳目的地怎么选？3大风味带+人均30元起拆解+FAQ"). Two are kept for URL-form coverage: `comos:nizmwas9943793` and `comos:nivawnc8079295`.
- **Very fresh coverage.** The tool's summary described one detail page (`5367298832728279`) as posted about 17 minutes before the search.

### Toutiao
- **What is indexed:** `/article/<id>/` (well indexed) and one 微头条 `/w/<id>/` post.
- **Video posts appear only as `m.toutiao.com/video/<id>/?…share_token=…` share links.**
- **Never seen:** `www.toutiao.com/video/<id>/`, `/group/<id>/`, `/item/<id>/` or `m.toutiao.com/is/<code>/` short links.
- **Many non-post pages:** `/zixun/<id>/` keyword pages, `/topic/`, `/keyword/`, `/c/user/token/` profiles and `sitemap-*` pages.

### WeChat
- **Two article forms are indexed:**
  - `mp.weixin.qq.com/s/<22-char token>`
  - `mp.weixin.qq.com/s?__biz=…&mid=…&idx=…&sn=…&chksm=…`, often damaged with `&amp=&amp=&amp=…`, an HTML-entity artifact from the indexer. These URLs are kept exactly as found; the probe should also test the cleaned form.
- **Some titles are the URL itself**, so there is no fingerprint (excluded):
  - `http://mp.weixin.qq.com/s?__biz=MzIzMTAxNjI0MA==&m...`
  - `https://mp.weixin.qq.com/s/jy2_HDSgyqBEROw9FEIafg`

### X
- **Fresh and full-text.** Coverage was fresh (down to 2026-11-13) and titles carry the full post text, cut off with "…" at about 280 characters.
- **Some titles have no usable fingerprint (excluded):**
  - `https://x.com/Teslarati/status/1995952829429600285?lang=en`, titled `TESLARATI on X: "https://t.co/NoTlx0o8V8" / X`
  - `https://x.com/jiemian_news/status/2056289067281658194`, titled `X on X / X`

## URL forms seen in the wild (verbatim)

Elisions marked `[…]` cut only long tracking or device-ID values.

### Douyin
- **Canonical post:** `https://www.douyin.com/video/7506900431965359394`
- **Mobile share post:**
  - `https://m.douyin.com/share/video/7457443186969972006`
  - `https://m.douyin.com/share/video/7257826504007077176`
- **SEO pages:**
  - Keyword page with an app parameter: `https://www.douyin.com/shipin/7272442640270919717?previous_page=app_code_link`
  - `https://www.douyin.com/zhuanti/7186152726798321724`
  - Mobile: `https://m.douyin.com/zhuanti/7170031797286209570`
  - `https://www.douyin.com/hot/2662936`
  - `https://page.iesdouyin.com/traffic-aggregation/387148`
- **Search page**, which carries a `group_id`: `https://so.douyin.com/s?app_theme=light&enter_method=video_title_challenge&group_id=7406707943967608105&innerHeight=732&innerWidth=412&is_no_width_reload=1&keyword=%E8%80%81%E5%BC%8F%E7%BA%A2%E7%83%A7%E8%82%89&query_correct_type=1&reloadNavStart=1736640000000&search_entrance=aweme`
- **Profile share link** with device and share IDs: `https://www.iesdouyin.com/share/user/MS4wLjABAAAA4agizOz-4PhU8bGa_vPa4UE8jXcAiInynzZwualHezg?u_code=eij8i158&did=[…]&iid=[…]&with_sec_did=1&sec_uid=MS4wLjABAAAA4agizOz-4PhU8bGa_vPa4UE8jXcAiInynzZwualHezg&from_ssr=1&app=aweme&tt_from=copy`
- **Profile:** `https://www.douyin.com/user/MS4wLjABAAAA4c-2IfSUEx2WxGORbdqRhwySd7e3W3YAyxrW0y2Yg-NTvMULl1HaZxeYwIQEhCB0`
- **Share text:** the `复制此链接，打开Dou音搜索` template was referenced only in API docs (apifox.com); no `v.douyin.com/<code>` link surfaced.

### Xiaohongshu / rednote
- **Tokenless note:** `https://www.xiaohongshu.com/explore/69cbd48f000000002200235a`
- **Old note form:** `https://www.xiaohongshu.com/discovery/item/5b4a183707ef1c50ae3c6cf6`
- **rednote:**
  - `https://www.rednote.com/discovery/item/69e35edd000000001e00d8b0`
  - `https://www.rednote.com/en/discovery/item/6a17c2a8000000003502f81a`
  - `https://www.rednote.com/explore?m_source=pwa`
- **xsec_token (profiles only):**
  - `https://www.xiaohongshu.com/user/profile/65426b57000000000400809b?xsec_token=ABE1l2G32P5oW_-VdSIhSwsADXaQ0iXiLkDkclXfP0Rmg%3D&xsec_source=pc_note`
  - Empty token: `https://www.xiaohongshu.com/user/profile/617fb8fb0000000010004f91?xsec_token=&xsec_source=pc_note`
  - App-share form, truncated inside the index itself: `https://www.xiaohongshu.com/user/profile/6598c4c8000000002001f583?xsec_token=YBiygfZvP8ffCVSzf4uvQJv-qK2aXrclwNGJwTWF4gFZs%3D&xsec_source=app_share&xhsshare=CopyLink&appuid=6598c4c80`
  - Entity-damaged: `https://www.xiaohongshu.com/user/profile/5e37d3e1000000000100b059?xsec_token=YBu6OAnypa4Dv9jKvDN1LOndtUIZLAt_YEivULDqppCM8%3D&amp=&xsec_source=app_share&amp=&xhsshare=CopyLink&amp=&appuid=5e37d3e1000000000100b059&amp=&apptime=1739944374&amp=&share_id=8e5abe68695d42a1ac792eb015c4af08`
- **SEO Q&A:** `https://www.xiaohongshu.com/mobile/question/2214705`
- **Tag page:** `https://www.xiaohongshu.com/mobile/tags/2297192396364206574?name=%E5%AF%BB%E6%89%BE%E4%B8%A2%E5%A4%B1%E9%B9%A6%E9%B9%89`
- **xhslink:** seen only as a page title, `https://xhslink.com/m/ 小红书短链` (gitcode.csdn.net). Downloader READMEs list `https://xhslink.com/分享码`, according to the tool summary.

### Bilibili
- **Canonical** (trailing slash): `https://www.bilibili.com/video/BV13Axv6EEjU/`
- **Recommendation parameter:** `https://www.bilibili.com/video/BV1nNHqeDEej/?spm_id_from=333.788.recommend_more_video.8`
- **av form:**
  - `https://www.bilibili.com/video/av19891708/`
  - From the `site:` path: `https://www.bilibili.com/video/av117008515468111/`
- **Mobile:** `https://m.bilibili.com/video/BV1eXN46JEZW`
- **Copy-link share** (a course page): `https://m.bilibili.com/cheese/play/ss12852?csource=Share_copylink&from_spmid=main.space.0.module&plat_id=362&share_from=lesson&share_medium=android&share_plat=android&share_session_id=4b664a80-b874-4674-a777-56573158ecb4&share_source=COPY&share_tag=s_i&spmid=united.player-video-detail.0.0&timestamp=1707281418&unique_k=Ppq4Bsw`
- **Search page:** `https://search.bilibili.com/all?keyword=%E7%94%B5%E8%A7%86%E5%89%A7%E8%A7%A3%E8%AF%B4`
- **Article:** `https://www.bilibili.com/read/cv6429626/`
- **b23.tv:** seen only as an example in a short-link generator README (`https://b23.tv/F78kbY`, per the tool summary of github.com/xlzy520/bili-short-url). The same summary of a BWIKI page says b23 codes expire after about a year, unless the code embeds an av, BV, ss, cv or ep ID.

### YouTube
- **Canonical:** `https://www.youtube.com/watch?v=OTAwvdRJn6M`
- **With tracking parameters:**
  - `https://www.youtube.com/watch?v=4Mtc2E7fkZ4&pp=ygUMI2l0ZW5hcnlwbGFu`
  - `https://www.youtube.com/watch?v=4SxAWpr97Yg&xstg=CAMSBhUDzO3xHw%3D%3D`
  - `https://www.youtube.com/watch?v=SjvbXamDWg4&vl=en-US`
  - `https://www.youtube.com/watch?v=_OMO1kFkI54&vl=fr`
- **The same video indexed twice with different `pp`:**
  - `https://www.youtube.com/watch?v=C_XHkTu-raw`
  - `https://www.youtube.com/watch?v=C_XHkTu-raw&pp=0gcJCf0Ao7VqN5tD`
- **Shorts:**
  - `https://www.youtube.com/shorts/rL5QEE27Si0`
  - `https://www.youtube.com/shorts/uABCf6G1uCc`
- **Embed:** `https://www.youtube.com/embed/GBvQvi79Fw4`
- **Truncated playlist** in the index: `https://www.youtube.com/playlist?list=PLe5IT7KD_q-o`
- **Channel:** `https://www.youtube.com/@ofam2020foodfarm/featured`
- **youtu.be with a share token**, found inside a TikTok caption: `https://youtu.be/skoNLz9rI-w?si=lVpBF4xJ50YNxYpS`

### TikTok
- **Canonical post:** `https://www.tiktok.com/@ddlovato/video/7480598109484010794`
- **With a language parameter:** `https://www.tiktok.com/@rileejsmith/video/7285135399062670634?lang=en`
- **Short links indexed as URLs:**
  - `https://vm.tiktok.com/ZMhFETCgD` (titled with a post caption)
  - `https://vt.tiktok.com/ZSHhQAfGq` (titled with a post caption)
  - `https://vm.tiktok.com/ZMR5egKx9` (titled "TikTok - Make Your Day")
  - `https://vm.tiktok.com/TTPdYUoUqQ` (titled "Explore - Find your favorite videos on TikTok")
- **In a page title:** `https://vt.tiktok.com/zsbkhkwrt/` (tributemosthaunted.co.uk)
- **Hub pages:**
  - `https://www.tiktok.com/discover/recipe?lang=en`
  - `https://www.tiktok.com/tag/recipevideos?refer=embed`
  - `https://tiktok.com/channel/travel-itinerary`
  - AMP: `https://www.tiktok.com/amp/tag/%E8%B7%9F%E8%91%97%E6%8A%96%E9%9F%B3%E5%AD%B8%E5%81%9A%E8%8F%9C`
  - Tag with an ID suffix: `https://www.tiktok.com/tag/%E5%A5%BD%E7%9C%8B%E7%9A%84%E7%9F%AD%E5%89%A7%E6%8E%A8%E8%8D%90__7593159298469907721`
- **Lemon8 mirrors** of TikTok-style posts, e.g. `https://www.lemon8-app.com/@mizzteebee/7460977867057267246?region=us`, also came back for `site:tiktok.com` queries.

### Weibo
- **Detail page:**
  - `https://weibo.com/2/detail/5367298832728279`
  - `https://weibo.com/2/detail/comos:niyvkxz3080946`
- **Articles:**
  - `https://weibo.com/ttarticle/x/m/geoshow/id/2309405344467770278067?domain=auto`
  - `https://weibo.com/ttarticle/x/m/geoshow/id/2309405348167934935110?domain=local`
  - `https://weibo.com/ttarticle/p/show?id=2309405187215650586882`
- **Status pages, title only "微博" / "微博正文 - 微博":**
  - `https://m.weibo.cn/status/5151286867268456`
  - `https://m.weibo.cn/status/HwuPxjzUP?jumpfrom=weibocom`
  - `https://weibo.com/3782417723/Jgsym0xsn`
  - `https://www.weibo.com/3782417723/I65WGngvB`
- **Profile:** `https://m.weibo.cn/u/3782417723?luicode=20000061&lfid=5134008289069983`
- **Search and topic pages:**
  - `https://s.weibo.com/weibo?Refer=hotmore&q=ultrasun`
  - `https://m.weibo.cn/search?containerid=231522type%3D1&isnewpage=1&lfid=231522type%3D1&luicode=10000011&q=%23%E5%B0%8F%E7%8E%8B%E5%AD%90LePetitPrince&q=%23%E8%82%A1%E6%B0%91%23&t=10&t=10#&featurecode=hbshare1`
- **Not seen:** `m.weibo.cn/detail/<mid>`, `t.cn` short links.

### Toutiao
- **Article:** `https://www.toutiao.com/article/7602485839775466011/`
- **微头条:** `https://www.toutiao.com/w/1866851830309895/`
- **Video share links:**
  - `https://m.toutiao.com/video/7442608827129728282/?app=news_article_dazi&timestamp=1740408807&share_token=9dc07ba1-c9c2-479b-a227-7e79a0027bd6`
  - `https://m.toutiao.com/video/7338427323869298723/?app=news_article&timestamp=1710024362&upstream_biz=thunber_search_share&share_token=b12d6e14-2a85-4a5d-9b56-471228ff3a17`
- **Article shared from 悟空浏览器:** `https://m.toutiao.com/sj/i7086280676248388108?app=gold_browser&timestamp=1698853530&use_new_style=1&req_id=2023110123453015F0A192DFDFB24F196A&group_id=7086280676248388108&share_type=article&is_share_backflow=1&share_token=4e580244-e4cc-4c09-a371-2058c74e76bf`
- **Non-post pages:**
  - `https://www.toutiao.com/zixun/7510845239645456434/`
  - `https://www.toutiao.com/topic/7472926401642137636/`
  - `https://www.toutiao.com/keyword/561467453`
  - `https://www.toutiao.com/c/user/token/MS4wLjABAAAAwP-PExvJCemiENaHlh8uZez54QSCxOM14VSRuT0dWiE/`
  - `https://www.toutiao.com/sitemap-weitoutiao-44-30-34`

### WeChat
- **Short form:** `https://mp.weixin.qq.com/s/OQ1HxnAuWkzgI4H_7CykSw`
- **Long form:** `https://mp.weixin.qq.com/s?__biz=MzI5MjA1MTMxNA%3D%3D&chksm=f7cdeacec0ba63d890f98834df535b583ea3b501883bdc04f8214a0b5eece452dd1f0d472aff&idx=1&mid=2654866131&scene=21&sn=6d85c7daa3b63fdf0f55e5019d36ccd3`
- **Long form with entity damage:** `https://mp.weixin.qq.com/s?__biz=MzIwNjE3ODMzMQ%3D%3D&amp=&amp=&amp=&amp=&idx=1&mid=2652535479&scene=21&sn=2b11d807c71fa1697d2c9d6b13febb04`
- **Unescaped `__biz`, no chksm:** `https://mp.weixin.qq.com/s?__biz=MjM5NjU4NTE0MA==&idx=1&mid=2658058239&sn=5501b23006598c4325db2f3b1ffc695f`
- **With `lang` and `token`:** `https://mp.weixin.qq.com/s?__biz=MzU2MDE2MDE0OA%3D%3D&amp=&amp=&amp=&amp=&amp=&amp=&amp=&chksm=fc0eacd3cb7925c5c7f71816cb375a2bd20a6dd33dfca1bef22b00cb8057fe0695a8ac54fe78&idx=2&lang=zh_CN&mid=2247508008&scene=21&sn=271996138eccc97309336d88517e37ea&token=1399868694`
- **Forwarded-share parameters:** `https://mp.weixin.qq.com/s?__biz=MzE5MTQ3NDcwMQ%3D%3D&chksm=9739447aec02696acbecb642e466ec80af1f2a623815ae3486c943d60a1c784e88819701280b&idx=1&mid=2247485127&mpshare=1&scene=1&sharer_shareinfo=086c5abbf83ef7b5f59608a4d5fb4cd4&sharer_shareinfo_first=086c5abbf83ef7b5f59608a4d5fb4cd4&sn=60b2522c13a46b114bce9edba92f2856&srcid=0911p2igGMBwnJl7X58RR5sL`
- **Other `scene` values:** `scene=27`, `scene=0` and no scene at all also occur.

### X
- **Canonical:** `https://x.com/Allrecipes/status/2085335136774590795`
- **twitter.com host:** `https://twitter.com/DaddingAround/status/1116312421709352960`
- **With `lang`:** `https://twitter.com/Stash/status/1293219695965995009?lang=en`
- **With `amp`:** `https://x.com/Readygov/status/1859983990507102487?amp=`
- **t.co inside titles:** `https://t.co/dW13ZlXo6V`, `https://t.co/JeUUxsLC0G`
- **t.co indexed as its own URL:** `https://t.co/JPbY7NTUNp`, titled "GitHub - gnipbao/whiteboard-video-engine · GitHub"
- **Mirrors and aggregators**, returned on the `site:` path:
  - `https://threadreaderapp.com/thread/2072739795810525522.html`
  - `https://news.google.com/stories/…` pages titled "Posts on X"

## Selection notes and caveats for the probe run

### Excluded on purpose
- Posts with UA, generic or URL-only titles (listed above).
- Political or geopolitical posts, e.g. China–Japan travel-warning posts and a post mocking CNN parenting advice.
- Celebrities' private-life items, e.g. Weibo `comos:` items about named stars' single parenting.
- Posts centred on specific named children.
- Full-episode short-drama re-uploads on YouTube and TikTok, which are likely unlicensed.
- Translated titles.

### Fingerprint strength varies
- **Strong:** X and TikTok titles carry the full text.
- **Weak:** some Douyin and XHS titles are hashtags only ("#家常菜 #家常菜教程 #教你做美食 - 抖音", "护肤记录。 - 小红书"-style).
- **Not the post's own words:** Weibo `2/detail` titles look like summaries.

Use semantic matching plus checks on the post's other content, not exact string match.

### Brand, promotional and SEO-style items are included
They are normal public content. Examples:
- Promotional: Colorkey promo on Weibo, Winona sunscreen, a 红果 short-drama promo account on TikTok, a Douyin "free short dramas" post.
- SEO/GEO-style: two Weibo `comos:` "+FAQ" pieces, Toutiao listicles.

### Thin cells
- Douyin finance (1 post): web search does not surface Douyin finance posts.
- YouTube finance (1 post).
- Douyin has 15 posts; the other platforms have 16–17.

### Kept for link-form diversity
These URLs carry odd shapes on purpose:
- `m.douyin.com/share/video/`
- `rednote.com/en/discovery/item/`
- `bilibili av19891708` and `?spm_id_from=`
- YouTube `/shorts/`, `/embed/`, `&pp=` and `&xstg=`
- TikTok `?lang=en`
- `twitter.com` hosts
- Weibo `comos:` and `ttarticle`
- Toutiao `/w/` and `m.toutiao.com/video/…share_token=`
- WeChat short form and `&amp=`-damaged long form

When generating link forms, canonicalise from `post_id` rather than editing these URLs in place.
