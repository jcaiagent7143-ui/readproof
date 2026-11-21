"""Access profiles (readproof/profiles.py) match DESIGN.md §2."""

from __future__ import annotations

import dataclasses

import pytest

from readproof import USER_AGENT
from readproof.profiles import ALL, P0, P1, P2, get


def test_profiles_match_the_design_table():
    assert (P0.obey_robots, P0.obey_content_signal, P0.user_initiated, P0.allow_browser, P0.allow_embed_media) == (
        True, True, False, True, True)
    assert (P1.obey_robots, P1.obey_content_signal, P1.user_initiated, P1.allow_browser, P1.allow_embed_media) == (
        False, False, True, True, True)
    assert (P2.obey_robots, P2.obey_content_signal, P2.user_initiated, P2.allow_browser, P2.allow_embed_media) == (
        False, False, True, False, False)
    for p in ALL:
        assert p.user_agent == USER_AGENT and p.max_retries == 1 and p.backoff_s > 0


@pytest.mark.parametrize("name,profile", [("P0", P0), ("p0", P0), ("strict", P0), (" P1 ", P1),
                                          ("user-initiated", P1), ("P2", P2), ("user_assisted", P2)])
def test_get(name, profile):
    assert get(name) is profile


@pytest.mark.parametrize("name", ["P3", "", "p", "lax", None])
def test_unknown_names_never_fall_back(name):
    with pytest.raises(ValueError):
        get(name)  # type: ignore[arg-type]


def test_profiles_are_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        P0.obey_robots = False  # type: ignore[misc]
