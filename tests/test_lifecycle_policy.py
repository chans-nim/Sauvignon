from datetime import date

import duckdb
import pandas as pd

from src.master.lifecycle_policy import apply_lifecycle_overrides
from src.master.universe_policy import collectible_universe_sql


def _universe():
    return pd.DataFrame([
        {"symbol": "084180", "std_code": "KR7084180009", "market": "KOSDAQ", "is_active": True, "is_trading_halt": False},
        {"symbol": "005930", "std_code": "KR7005930003", "market": "KOSPI", "is_active": True, "is_trading_halt": False},
    ])


def test_delisted_listing_excluded_from_shared_collection_and_validation_scope():
    result = apply_lifecycle_overrides(_universe(), as_of=date(2026, 10, 6))
    with duckdb.connect() as con:
        con.register("universe", result)
        assert con.execute(f"SELECT symbol FROM universe WHERE {collectible_universe_sql()}").fetchall() == [("005930",)]
    assert len(result) == 2


def test_delisting_applies_on_effective_date_not_final_trading_date():
    assert apply_lifecycle_overrides(_universe(), as_of=date(2026, 9, 30))["is_active"].all()
    assert not apply_lifecycle_overrides(_universe(), as_of=date(2026, 10, 1)).iloc[0]["is_active"]


def test_short_code_reuse_does_not_exclude_different_listing():
    universe = _universe()
    universe.loc[0, "std_code"] = "KR7084180017"
    assert apply_lifecycle_overrides(universe, as_of=date(2026, 10, 6))["is_active"].all()
