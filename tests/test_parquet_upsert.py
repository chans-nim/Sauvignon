from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.storage import parquet_store


@pytest.fixture
def temp_silver_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    temp_dir = tmp_path / "silver"
    temp_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(parquet_store, "SILVER_DIR", temp_dir)
    return temp_dir


def test_overwrite_same_symbol_date_with_latest_row(temp_silver_dir: Path) -> None:
    # 검증: 같은 (symbol, date)는 나중에 upsert 된 값으로 overwrite 된다.
    df1 = pd.DataFrame(
        [
            {
                "symbol": "005930",
                "market": "KOSPI",
                "date": pd.Timestamp("2025-12-30"),
                "open": 100,
                "high": 110,
                "low": 90,
                "close": 105,
                "volume": 1000,
                "value": 105000,
                "ingested_at": pd.Timestamp("2026-03-14 09:00:00"),
            }
        ]
    )
    df2 = pd.DataFrame(
        [
            {
                "symbol": "005930",
                "market": "KOSPI",
                "date": pd.Timestamp("2025-12-30"),
                "open": 101,
                "high": 115,
                "low": 95,
                "close": 111,
                "volume": 2000,
                "value": 222000,
                "ingested_at": pd.Timestamp("2026-03-14 10:00:00"),
            }
        ]
    )

    parquet_store.upsert_ohlcv_from_df(df1)
    parquet_store.upsert_ohlcv_from_df(df2)

    out_path = temp_silver_dir / "market=KOSPI" / "symbol=005930" / "year=2025" / "data.parquet"
    out = pd.read_parquet(out_path)
    assert len(out) == 1
    assert int(out.iloc[0]["close"]) == 111
    assert int(out.iloc[0]["volume"]) == 2000


def test_split_by_year_and_symbol_partitions(temp_silver_dir: Path) -> None:
    # 검증: 여러 종목/연도가 섞여 있어도 market/symbol/year 파티션으로 분리 저장된다.
    df = pd.DataFrame(
        [
            {
                "symbol": "005930",
                "market": "KOSPI",
                "date": pd.Timestamp("2024-12-30"),
                "open": 100,
                "high": 110,
                "low": 90,
                "close": 105,
                "volume": 1000,
                "value": 105000,
                "ingested_at": pd.Timestamp("2026-03-14 09:00:00"),
            },
            {
                "symbol": "005930",
                "market": "KOSPI",
                "date": pd.Timestamp("2025-01-02"),
                "open": 106,
                "high": 112,
                "low": 101,
                "close": 108,
                "volume": 1200,
                "value": 129600,
                "ingested_at": pd.Timestamp("2026-03-14 09:00:00"),
            },
            {
                "symbol": "000660",
                "market": "KOSPI",
                "date": pd.Timestamp("2025-01-02"),
                "open": 200,
                "high": 210,
                "low": 190,
                "close": 205,
                "volume": 1500,
                "value": 307500,
                "ingested_at": pd.Timestamp("2026-03-14 09:00:00"),
            },
        ]
    )

    saved = parquet_store.upsert_ohlcv_from_df(df)
    expected = {
        temp_silver_dir / "market=KOSPI" / "symbol=005930" / "year=2024" / "data.parquet",
        temp_silver_dir / "market=KOSPI" / "symbol=005930" / "year=2025" / "data.parquet",
        temp_silver_dir / "market=KOSPI" / "symbol=000660" / "year=2025" / "data.parquet",
    }
    assert set(saved) == expected
    for path in expected:
        assert path.exists()
