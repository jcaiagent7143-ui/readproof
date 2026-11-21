"""PricedBudget (readproof/budget.py)."""

from __future__ import annotations

import pytest

from readproof.budget import BudgetExceeded, PricedBudget
from readproof.testing import MemoryLog


def test_charges_are_totalled_per_tool():
    b = PricedBudget(cap_usd=1.0)
    b.charge("llm", usd=0.10, seconds=2.0, model="gemma")
    b.charge("llm", usd=0.05, seconds=1.0)
    b.charge("asr", usd=0.0, seconds=30.0)
    s = b.summary()
    assert s["tools"]["llm"] == {"usd": pytest.approx(0.15), "seconds": 3.0, "calls": 2}
    assert s["tools"]["asr"] == {"usd": 0.0, "seconds": 30.0, "calls": 1}
    assert s["spent_usd"] == pytest.approx(0.15) and s["seconds"] == 33.0
    assert b.remaining_usd() == pytest.approx(0.85) and s["cap_usd"] == 1.0


def test_a_charge_past_the_cap_is_refused_before_it_is_recorded():
    log = MemoryLog()
    b = PricedBudget(cap_usd=0.5, log=log)
    b.charge("llm", usd=0.4)
    with pytest.raises(BudgetExceeded):
        b.charge("llm", usd=0.2)
    assert b.summary()["spent_usd"] == pytest.approx(0.4)
    assert b.summary()["tools"]["llm"]["calls"] == 1 and b.summary()["refused"] == 1
    b.charge("llm", usd=0.1)  # what still fits is still allowed
    assert b.remaining_usd() == pytest.approx(0.0)
    events = log.of("budget")
    assert [e["refused"] for e in events] == [False, True, False]
    assert events[1]["spent_usd"] == pytest.approx(0.4)  # the books at refusal time


def test_landing_exactly_on_the_cap_is_allowed():
    b = PricedBudget(cap_usd=1.0)
    for _ in range(10):
        b.charge("ocr", usd=0.1)  # 0.1 * 10 is 0.9999999999999999 in floats
    with pytest.raises(BudgetExceeded):
        b.charge("ocr", usd=0.001)


@pytest.mark.parametrize("usd,seconds", [(-0.01, 0.0), (0.0, -1.0), (float("nan"), 0.0), (float("inf"), 0.0)])
def test_negative_or_non_finite_charges_are_refused(usd, seconds):
    with pytest.raises(ValueError):
        PricedBudget().charge("llm", usd=usd, seconds=seconds)


def test_seconds_cap():
    b = PricedBudget(cap_usd=10.0, cap_seconds=60.0)
    b.charge("asr", seconds=50.0)
    with pytest.raises(BudgetExceeded):
        b.charge("asr", seconds=20.0)
    assert b.summary()["seconds"] == 50.0


def test_budget_events_carry_meta():
    log = MemoryLog()
    PricedBudget(log=log).charge("llm", usd=0.01, seconds=1.5, model="m", refused="caller meta, not the flag")
    e = log.of("budget")[0]
    assert (e["tool"], e["usd"], e["seconds"], e["refused"]) == ("llm", 0.01, 1.5, False)
    assert e["meta"] == {"model": "m", "refused": "caller meta, not the flag"}
    assert e["remaining_usd"] == pytest.approx(0.99)
