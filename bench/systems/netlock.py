"""One study, one connection: a cross-process lock held by every phase that sends platform requests.

The politeness scheduler (pacing.py) only sees one process. Track A's reads and Track K's reads both go to the same
platforms (Douyin, Bilibili, YouTube, TikTok, X), so a second process reading at the same time could put two
requests to one domain less than 5 s apart. Each runner's network phase therefore holds this lock; a second runner
waits (or, with wait=False, refuses to start).
"""

from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

LOCK = Path(__file__).resolve().parents[2] / "runs" / "network.lock"


@contextmanager
def network_phase(name: str, *, wait: bool = True, poll_s: float = 30.0) -> Iterator[None]:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK, "a+")
    try:
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not wait:
                    raise SystemExit(f"{name}: another benchmark process is sending platform requests "
                                     f"({LOCK}); not starting a second one")
                print(f"{name}: waiting for the other benchmark process to finish its network phase", flush=True)
                time.sleep(poll_s)
        fh.seek(0)
        fh.truncate()
        fh.write(f"{name} pid={os.getpid()} since={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
        fh.flush()
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()
