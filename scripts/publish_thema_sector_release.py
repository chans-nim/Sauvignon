"""Backward compatibility: use `python -m scripts.publish_theme_sector_release`."""

from __future__ import annotations

from scripts.publish_theme_sector_release import main

if __name__ == "__main__":
    main()
