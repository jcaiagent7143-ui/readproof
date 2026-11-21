"""Access profiles (DESIGN.md §2) as data. tests/test_rules.py pins what each field means.

P0 is what an unattended crawler may do: robots.txt and Content-Signal bind. P1 is a single fetch of a link a
user supplied; RFC 9309 scopes robots.txt to crawlers, so it is evaluated and logged but not binding. P2 is
content a person captured or uploaded: no browser, no official-player media.

All three declare the same identity; a profile never changes who we say we are.
"""

from __future__ import annotations

from . import USER_AGENT
from .types import Profile

P0 = Profile(
    name="P0",
    obey_robots=True,
    obey_content_signal=True,
    user_initiated=False,
    allow_browser=True,
    allow_embed_media=True,  # only where robots.txt allows; the gate decides each request
    user_agent=USER_AGENT,
)

P1 = Profile(
    name="P1",
    obey_robots=False,
    obey_content_signal=False,
    user_initiated=True,
    allow_browser=True,
    allow_embed_media=True,
    user_agent=USER_AGENT,
)

P2 = Profile(
    name="P2",
    obey_robots=False,
    obey_content_signal=False,
    user_initiated=True,
    allow_browser=False,
    allow_embed_media=False,
    user_agent=USER_AGENT,
)

ALL: tuple[Profile, ...] = (P0, P1, P2)

_ALIASES = {
    "p0": P0, "strict": P0,
    "p1": P1, "user-initiated": P1, "user_initiated": P1,
    "p2": P2, "user-assisted": P2, "user_assisted": P2,
}


def get(name: str) -> Profile:
    """Profile by name ("P0", "p1", "strict", "user-initiated", ...). Unknown names raise ValueError so a
    typo on the command line can never fall back to a laxer profile."""
    try:
        return _ALIASES[name.strip().lower()]
    except (KeyError, AttributeError):
        raise ValueError(f"unknown profile {name!r}; expected one of P0, P1, P2") from None
