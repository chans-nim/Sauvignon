"""Backward compatibility: use `scripts.theme_sector_release_lib`."""

from __future__ import annotations

from scripts.theme_sector_release_lib import (  # noqa: F401
    LEGACY_THEMA_SECTOR_TAG_PREFIX,
    LEGACY_THEMA_SECTOR_TAG_RE,
    THEME_SECTOR_TAG_PREFIX,
    THEME_SECTOR_TAG_RE,
    is_theme_sector_release_tag,
    theme_sector_tag_sort_key,
)

THEMA_SECTOR_TAG_PREFIX = THEME_SECTOR_TAG_PREFIX
THEMA_SECTOR_TAG_RE = THEME_SECTOR_TAG_RE
thema_sector_tag_sort_key = theme_sector_tag_sort_key
