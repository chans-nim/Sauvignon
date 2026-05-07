from scripts.theme_sector_release_lib import theme_sector_tag_sort_key


def test_theme_sector_tag_sort_key_orders_chronologically() -> None:
    tags = [
        "theme-sector-20260509-1600",
        "thema-sector-20260507-0700",
        "theme-sector-20260507-1600",
        "thema-sector-20260508-0700",
    ]
    assert sorted(tags, key=theme_sector_tag_sort_key) == [
        "thema-sector-20260507-0700",
        "theme-sector-20260507-1600",
        "thema-sector-20260508-0700",
        "theme-sector-20260509-1600",
    ]


def test_theme_sector_tag_sort_key_theme_only() -> None:
    tags = [
        "theme-sector-20260509-1600",
        "theme-sector-20260507-0700",
        "theme-sector-20260507-1600",
        "theme-sector-20260508-0700",
    ]
    assert sorted(tags, key=theme_sector_tag_sort_key) == [
        "theme-sector-20260507-0700",
        "theme-sector-20260507-1600",
        "theme-sector-20260508-0700",
        "theme-sector-20260509-1600",
    ]
