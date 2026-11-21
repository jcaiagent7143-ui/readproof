"""Priced tools (DESIGN.md §3, §9): every model call, transcription or OCR pass is charged in USD and seconds.

Why: the benchmark reports cost per correct answer, and a run must stop before it spends more than its
cap. A charge that would pass the cap raises BudgetExceeded BEFORE it is recorded, so the books never show
money the cap did not allow. Negative charges are refused: a credit would silently raise the cap.
"""

from __future__ import annotations

import math
from typing import Any

from .interfaces import Log

_EPS = 1e-9  # float sums of small prices must not refuse a charge that lands exactly on the cap


class BudgetExceeded(Exception):
    """A charge would take spending past the cap."""


class PricedBudget:
    """Implements interfaces.Budget."""

    def __init__(self, cap_usd: float = 1.0, log: Log | None = None, *, cap_seconds: float | None = None) -> None:
        self.cap_usd = float(cap_usd)
        self.cap_seconds = cap_seconds
        self.log = log
        self.spent_usd = 0.0
        self.seconds = 0.0
        self.refused = 0
        self._tools: dict[str, dict[str, float]] = {}

    def charge(self, tool: str, *, usd: float = 0.0, seconds: float = 0.0, **meta: Any) -> None:
        usd, seconds = float(usd), float(seconds)
        if not (math.isfinite(usd) and math.isfinite(seconds)) or usd < 0 or seconds < 0:
            raise ValueError(f"charges must be finite and non-negative (got usd={usd}, seconds={seconds})")
        over_usd = self.spent_usd + usd > self.cap_usd + _EPS
        over_s = self.cap_seconds is not None and self.seconds + seconds > self.cap_seconds + _EPS
        if over_usd or over_s:
            self.refused += 1
            self._emit(tool, usd, seconds, meta, refused=True)
            what = f"${self.spent_usd + usd:.6f} > cap ${self.cap_usd:.6f}" if over_usd else \
                f"{self.seconds + seconds:.1f} s > cap {self.cap_seconds:.1f} s"
            raise BudgetExceeded(f"{tool}: {what}")
        self.spent_usd += usd
        self.seconds += seconds
        t = self._tools.setdefault(tool, {"usd": 0.0, "seconds": 0.0, "calls": 0})
        t["usd"] += usd
        t["seconds"] += seconds
        t["calls"] += 1
        self._emit(tool, usd, seconds, meta, refused=False)

    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)

    def summary(self) -> dict[str, Any]:
        """Totals and per-tool totals: {"cap_usd", "spent_usd", "remaining_usd", "seconds", "refused",
        "tools": {tool: {"usd", "seconds", "calls"}}}."""
        return {
            "cap_usd": self.cap_usd,
            "spent_usd": self.spent_usd,
            "remaining_usd": self.remaining_usd(),
            "seconds": self.seconds,
            "cap_seconds": self.cap_seconds,
            "refused": self.refused,
            "tools": {k: dict(v) for k, v in self._tools.items()},
        }

    def _emit(self, tool: str, usd: float, seconds: float, meta: dict[str, Any], *, refused: bool) -> None:
        if self.log is not None:
            self.log.emit("budget", tool=tool, usd=usd, seconds=seconds, refused=refused,
                          spent_usd=self.spent_usd, remaining_usd=self.remaining_usd(), meta=meta)
