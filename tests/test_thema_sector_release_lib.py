from scripts.thema_sector_release_lib import thema_sector_tag_sort_key


def test_thema_sector_tag_sort_key_orders_chronologically() -> None:
    tags = [
        "thema-sector-20260509-1600",
        "thema-sector-20260507-0700",
        "thema-sector-20260507-1600",
        "thema-sector-20260508-0700",
    ]
    assert sorted(tags, key=thema_sector_tag_sort_key) == [
        "thema-sector-20260507-0700",
        "thema-sector-20260507-1600",
        "thema-sector-20260508-0700",
        "thema-sector-20260509-1600",
    ]
