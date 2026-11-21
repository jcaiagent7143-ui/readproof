"""Offline tests for the politeness scheduler, circuit breaker, reply classification and the link-form plan."""

from __future__ import annotations

import threading

import httpx

from bench.systems import base, pacing


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def test_registrable_domain():
    rd = pacing.registrable_domain
    assert rd("https://m.weibo.cn/detail/1") == "weibo.cn"
    assert rd("www.xiaohongshu.com") == "xiaohongshu.com"
    assert rd("open.douyin.com") == "douyin.com"
    assert rd("mp.weixin.qq.com") == "qq.com"
    assert rd("a.b.example.com.cn") == "example.com.cn"
    assert rd("youtu.be") == "youtu.be"
    assert rd("127.0.0.1") == "127.0.0.1"


def test_scheduler_gap_per_domain_and_counts():
    clock = FakeClock()
    s = pacing.DomainScheduler(5.0, clock=clock, sleep=clock.sleep)
    with s.bind("douyin") as frame:
        assert s.reserve("https://www.douyin.com/a") == 0
        assert s.reserve("https://open.douyin.com/b") == 5.0  # same registrable domain
        assert s.reserve("https://www.bilibili.com/c") == 0  # another domain: no wait
        clock.t += 1.0
        assert s.reserve("https://www.douyin.com/d") == 4.0
    assert frame["requests"] == 4 and s.requests_for("douyin") == 4
    assert frame["domains"]["douyin.com"] == 3


def test_scheduler_hold_and_touch():
    clock = FakeClock()
    s = pacing.DomainScheduler(5.0, clock=clock, sleep=clock.sleep)
    s.reserve("x.com", hold_s=3.0)
    assert s.reserve("x.com") == 8.0
    s.touch(["x.com"])
    assert s.reserve("x.com", count=False) == 5.0


def test_scheduler_threads_never_overlap():
    s = pacing.DomainScheduler(5.0)
    slots: list[float] = []
    lock = threading.Lock()

    def worker():
        for _ in range(20):
            slot, _now = s.assign("example.com")
            with lock:
                slots.append(slot)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    slots.sort()
    assert len(slots) == 160
    assert all(b - a >= 5.0 - 1e-9 for a, b in zip(slots, slots[1:]))


def test_paced_transport_paces_every_hop():
    clock = FakeClock()
    s = pacing.DomainScheduler(5.0, clock=clock, sleep=clock.sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://example.com/end"})
        return httpx.Response(200, text="ok")

    client = httpx.Client(transport=pacing.PacedTransport(httpx.MockTransport(handler), s), follow_redirects=True)
    with s.bind("web") as frame:
        r = client.get("https://example.com/start")
    assert r.status_code == 200 and frame["requests"] == 2 and clock.slept == [5.0]


def test_paced_gate_paces_allowed_browser_checks_only():
    clock = FakeClock()
    s = pacing.DomainScheduler(5.0, clock=clock, sleep=clock.sleep)

    class D:
        def __init__(self, allowed):
            self.allowed = allowed

    class G:
        profile = "P1"

        def check(self, url, *, purpose="read"):
            return D("blocked" not in url)

    g = pacing.PacedGate(G(), s, hold_s=0.0)
    g.check("https://a.com/1", purpose="read")
    g.check("https://a.com/blocked", purpose="browser")
    assert s.requests_for("") == 0 and g.paced == 0
    with s.bind("p"):
        g.check("https://a.com/2", purpose="browser")
        g.check("https://a.com/3", purpose="browser")
    assert g.paced == 2 and clock.slept == [5.0]


def test_breaker_trips_after_six_and_resets_on_served():
    b = pacing.Breaker(6)
    for _ in range(5):
        assert not b.record("douyin", "naive_fetch", "challenge")
    assert not b.record("douyin", "naive_fetch", None)  # served resets the streak
    for i in range(6):
        opened = b.record("douyin", "naive_fetch", "login" if i % 2 else "rate_limited")
    assert opened and b.is_open("douyin", "naive_fetch")
    for _ in range(10):
        b.record("douyin", "browser_text", "deleted")  # not a refusal
    assert not b.is_open("douyin", "browser_text")


def test_platform_watch_needs_two_systems():
    w = pacing.PlatformWatch(window=4, min_systems=2)
    for _ in range(4):
        assert not w.record("tiktok", "naive_fetch", "challenge", 1)
    assert not w.is_stopped("tiktok")  # one system only
    w.record("tiktok", "browser_text", "challenge", 1)
    assert w.is_stopped("tiktok")
    assert not w.record("x", "naive_fetch", "challenge", 0)  # no request sent: not counted


def test_first_url_and_classify_reply():
    assert base.first_url("7.43 复制打开抖音 https://www.douyin.com/video/1 复制此链接") == "https://www.douyin.com/video/1"
    assert base.first_url("see (https://x.com/a/status/1).") == "https://x.com/a/status/1"
    assert base.first_url("no link here") is None
    assert base.classify_reply("NOT_FOUND") == (True, "token")
    assert base.classify_reply("") == (True, "empty")
    assert base.classify_reply("The page does not show the post; it is a login page.")[0]
    assert base.classify_reply("A recipe for crispy pan-fried tofu.") == (False, "answered")


def test_plan_forms_and_offline_targets():
    from bench import trackA_run

    seeds = trackA_run.load_seeds()
    plan = trackA_run.build_tasks(seeds, None, None)
    for platform, posts in plan["by_platform"].items():
        for post in posts:
            names = [f for f, _ in post["forms"]]
            assert 1 <= len(names) <= 4 and len(set(names)) == len(names)
            if not plan["notes"][post["post_key"]].get("unparsed"):
                assert "share_text" in names, (platform, names)
    # every parsed form of a Douyin post is one ReadProof read; the share text carries the desktop URL
    dy = plan["by_platform"]["douyin"][0]
    targets = {trackA_run.readproof_target(t) for _, t in dy["forms"]}
    assert len(targets) == 1
    forms = dict(dy["forms"])
    assert trackA_run.target_for("naive_fetch", forms["share_text"]) == trackA_run.target_for("naive_fetch",
                                                                                                forms["desktop"])
    assert trackA_run.target_for("naive_fetch", forms["modal"]) != trackA_run.target_for("naive_fetch", forms["desktop"])


def test_counting_llm_patience_and_ceiling(monkeypatch, tmp_path):
    import time as time_mod

    from bench.systems import base as base_mod

    class Res:
        def __init__(self, cached=False):
            self.text, self.cached = "NOT_FOUND", cached

    class Client:
        model = "m:free"
        cache_dir = tmp_path

        def __init__(self):
            self.calls = 0

        def _key(self, payload):
            return "ab" + "0" * 62

        def chat(self, messages, **kw):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError('429 {"error": "temporarily rate-limited upstream"}')
            return Res()

    slept = []
    monkeypatch.setattr(time_mod, "sleep", lambda s: slept.append(s))
    llm = base_mod.CountingLLM(Client(), max_uncached=1, ledger=str(tmp_path / "ledger"))
    llm.patience_s = 3600
    assert llm.chat([{"role": "user", "content": "x"}]).text == "NOT_FOUND"
    assert llm.client.calls == 3 and llm.uncached == 1 and len(slept) == 2
    # the daily ceiling is reached: an uncached request is refused, a cached one still goes through
    import pytest

    with pytest.raises(base_mod.CallBudgetExceeded):
        llm.chat([{"role": "user", "content": "y"}])
    (tmp_path / "ab").mkdir()
    (tmp_path / "ab" / ("ab" + "0" * 62 + ".json")).write_text("{}")
    llm.chat([{"role": "user", "content": "y"}])


def test_clean_surrogates():
    from bench.systems.baselines import _clean_surrogates

    bad = "ok \ud83d end"
    out = _clean_surrogates(bad)
    out.encode("utf-8")  # no longer raises
    assert out.startswith("ok ") and out.endswith(" end")
