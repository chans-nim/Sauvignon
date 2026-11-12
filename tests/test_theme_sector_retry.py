import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from scripts.resolve_latest_trading_date import resolve_latest_trading_date
from scripts.validate_theme_sector_release import validate_release_assets


def test_resolver_uses_latest_date_shared_by_all_anchors():
    class Client:
        def fetch_price_history_frame(self, symbols, days=14):
            assert symbols == ["005930", "000660", "069500"]
            return pd.DataFrame(
                {
                    "005930": [100, 101],
                    "000660": [200, 201],
                    "069500": [300, None],
                },
                index=pd.to_datetime(["2026-11-09", "2026-11-10"]),
            )

    assert resolve_latest_trading_date(Client()) == "2026-11-09"


def test_resolver_excludes_premarket_reference_row_for_today():
    class Client:
        def fetch_price_history_frame(self, symbols, days=14):
            return pd.DataFrame(
                {symbol: [100, 100] for symbol in symbols},
                index=pd.to_datetime(["2026-11-12", "2026-11-13"]),
            )

    result = resolve_latest_trading_date(
        Client(),
        now=datetime(2026, 11, 13, 5, 30, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    assert result == "2026-11-12"


def test_existing_release_requires_matching_date_tag_and_quality(tmp_path):
    (tmp_path / "overview.json").write_text(json.dumps({"metadata": {
        "analysis_trading_date": "2026-11-09",
        "theme_snapshot_date": "2026-11-09",
        "recommendation_data_valid": True,
        "market_data_quality": {"valid": True, "expected_count": 100},
    }}), encoding="utf-8")
    (tmp_path / "theme_history_index.json").write_text(json.dumps({
        "snapshot_date": "2026-11-09", "release_tag": "theme-sector-20261109",
    }), encoding="utf-8")
    (tmp_path / "theme_history_snapshot.json").write_text(json.dumps({
        "date": "2026-11-09", "metric_rows": [{"date": "2026-11-09"}],
    }), encoding="utf-8")

    validate_release_assets(tmp_path, "2026-11-09", "theme-sector-20261109")
    with pytest.raises(ValueError, match="release validation failed"):
        validate_release_assets(tmp_path, "2026-11-10", "theme-sector-20261110")
