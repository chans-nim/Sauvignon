"""Shared helpers for thema-sector GitHub releases (tag format, sort keys)."""

from __future__ import annotations

import re

THEMA_SECTOR_TAG_PREFIX = "thema-sector-"
THEMA_SECTOR_TAG_RE = re.compile(r"^thema-sector-(\d{8})-(\d{4})$")


def thema_sector_tag_sort_key(tag: str) -> tuple:
    m = THEMA_SECTOR_TAG_RE.match(str(tag).strip())
    if m:
        return (0, m.group(1), m.group(2))
    return (1, str(tag), "")
