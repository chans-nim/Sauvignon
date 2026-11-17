import json
from datetime import date

from scripts.prune_delisted_theme_stocks import confirmed_delisted_codes


def test_confirmed_delisting_closes_stale_master_gap(tmp_path):
    path = tmp_path / "lifecycle.json"
    path.write_text(json.dumps([
        {
            "symbol": "097780",
            "delisting_date": "2026-11-12",
            "source_url": "https://example.test/official-disclosure",
        },
        {
            "symbol": "999999",
            "delisting_date": "2026-11-19",
            "source_url": "https://example.test/future-disclosure",
        },
    ]), encoding="utf-8")

    assert confirmed_delisted_codes(as_of=date(2026, 11, 18), path=path) == {"097780"}
