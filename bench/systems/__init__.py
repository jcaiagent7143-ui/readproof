"""Benchmark systems behind one interface: read(url) -> SysRead, answer(question, url, read=None) -> SysAnswer.

See base.py for the shared types, baselines.py for closed_book / naive_fetch / reader_service / browser_text,
readproof_sys.py for readproof_p1 / readproof_p0 and the P1 ablations, pacing.py for the politeness scheduler.
"""

from .base import PROBE_QUESTION, CallBudgetExceeded, CountingLLM, SysAnswer, SysRead, classify_reply, first_url
from .pacing import Breaker, DomainScheduler, PacedGate, PacedTransport, PlatformWatch, registrable_domain

BASELINES = ("naive_fetch", "browser_text", "reader_service")
READPROOF = ("readproof_p0", "readproof_p1")
ABLATIONS = ("readproof_p1_nowalls", "readproof_p1_noverify", "readproof_p1_nospeech", "readproof_p1_noscreen")
TRACK_A_SYSTEMS = ("naive_fetch", "browser_text", "reader_service", "readproof_p0", "readproof_p1")
TRACK_K_SYSTEMS = ("closed_book",) + BASELINES + READPROOF + ABLATIONS

__all__ = [
    "PROBE_QUESTION", "CallBudgetExceeded", "CountingLLM", "SysAnswer", "SysRead", "classify_reply", "first_url",
    "Breaker", "DomainScheduler", "PacedGate", "PacedTransport", "PlatformWatch", "registrable_domain",
    "BASELINES", "READPROOF", "ABLATIONS", "TRACK_A_SYSTEMS", "TRACK_K_SYSTEMS",
]
