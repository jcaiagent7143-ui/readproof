"""Platform specs (readproof.interfaces.PlatformSpec), one module per platform, and the registry.

REGISTRY is ordered: the first spec whose ``match`` accepts a URL owns it, and ``web`` (any http(s) URL)
is last so that it only ever takes what no platform claims.
"""

from __future__ import annotations

from ..interfaces import PlatformSpec
from . import bilibili, douyin, linkedin, tiktok, toutiao, web, wechat, weibo, x, xiaohongshu, youtube

REGISTRY: list[PlatformSpec] = [
    douyin.SPEC,
    xiaohongshu.SPEC,
    bilibili.SPEC,
    youtube.SPEC,
    tiktok.SPEC,
    weibo.SPEC,
    toutiao.SPEC,
    x.SPEC,
    wechat.SPEC,
    linkedin.SPEC,
    web.SPEC,
]

_BY_NAME: dict[str, PlatformSpec] = {spec.name: spec for spec in REGISTRY}


def for_url(url: str) -> PlatformSpec | None:
    """The spec that owns this URL, or None when it is not an http(s) URL at all."""
    for spec in REGISTRY:
        if spec.match(url):
            return spec
    return None


def by_name(name: str) -> PlatformSpec:
    """The spec registered under `name` (PostRef.platform). Raises KeyError for an unknown name."""
    try:
        return _BY_NAME[name]
    except KeyError:
        raise KeyError(f"unknown platform {name!r}; known: {', '.join(_BY_NAME)}") from None


__all__ = ["REGISTRY", "for_url", "by_name"]
