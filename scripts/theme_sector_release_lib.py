"""Shared helpers for theme-sector GitHub releases (tag format, sort keys). Legacy thema-sector-* tags remain valid."""

from __future__ import annotations

import re

THEME_SECTOR_TAG_PREFIX = "theme-sector-"
LEGACY_THEMA_SECTOR_TAG_PREFIX = "thema-sector-"
THEME_HISTORY_SNAPSHOT_ASSET = "theme_history_snapshot.json"

THEME_SECTOR_TAG_RE = re.compile(r"^theme-sector-(\d{8})-(\d{4})$")
LEGACY_THEMA_SECTOR_TAG_RE = re.compile(r"^thema-sector-(\d{8})-(\d{4})$")


def theme_sector_tag_sort_key(tag: str) -> tuple:
    for pat in (THEME_SECTOR_TAG_RE, LEGACY_THEMA_SECTOR_TAG_RE):
        m = pat.match(str(tag).strip())
        if m:
            return (0, m.group(1), m.group(2))
    return (1, str(tag), "")


def is_theme_sector_release_tag(tag: str, *, explicit_prefix: str | None = None) -> bool:
    """explicit_prefix 가 있으면 그 접두사만; 없으면 theme-sector-* 와 구 thema-sector-* 모두."""
    t = str(tag).strip()
    if explicit_prefix:
        return t.startswith(explicit_prefix)
    return t.startswith(THEME_SECTOR_TAG_PREFIX) or t.startswith(LEGACY_THEMA_SECTOR_TAG_PREFIX)
