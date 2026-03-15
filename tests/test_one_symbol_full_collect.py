"""
한 종목만 대상으로 10년치 수집을 실행한 뒤, silver에 연도별로 데이터가 채워졌는지 검증한다.
실제 KIS API를 호출하므로 .env 의 KIS_APP_KEY/SECRET 이 필요하다.

실행: 프로젝트 루트에서
  pytest tests/test_one_symbol_full_collect.py -v -s
  pytest tests/test_one_symbol_full_collect.py -v -s --symbol 000660
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

import pytest

# 프로젝트 루트
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.common.settings import settings
from src.storage.meta_store import ensure_tables, load_universe
from src.collect.collect_backfill import collect_one

SILVER_DIR = settings.project_root / "data" / "lake" / "silver" / "ohlcv_daily"
TARGET_START = "2016-01-01"
TARGET_END = "2025-12-31"
# 연도당 최소 기대 row 수 (영업일 약 250일, 전체 수집 검증용으로 200 이상)
MIN_ROWS_PER_YEAR = 200


def load_silver_for_symbol(symbol: str):
    import duckdb
    symbol = str(symbol).strip()
    paths = [p.as_posix() for p in SILVER_DIR.rglob("data.parquet") if f"symbol={symbol}" in p.as_posix()]
    if not paths:
        return None
    con = duckdb.connect()
    df = con.execute("SELECT * FROM read_parquet(?) ORDER BY date", [paths]).fetchdf()
    con.close()
    df["date"] = df["date"].dt.normalize()
    return df


def get_year_counts(df):
    """연도별 row 수 Series (year -> count)."""
    if df is None or df.empty:
        return {}
    df = df[(df["date"] >= TARGET_START) & (df["date"] <= TARGET_END)]
    if df.empty:
        return {}
    return df["date"].dt.year.value_counts().sort_index().to_dict()


def pytest_addoption(parser):
    parser.addoption("--symbol", default="005930", help="Symbol for one-symbol full collect test")


@pytest.fixture(scope="module")
def test_symbol(request):
    return request.config.getoption("--symbol", default="005930")


def test_one_symbol_full_collect(test_symbol: str):
    """한 종목에 대해 2016~2025 수집 후 silver 연도별 row 수 검증."""
    if not settings.kis_app_key or not settings.kis_app_secret:
        pytest.skip("KIS_APP_KEY / KIS_APP_SECRET not set")

    ensure_tables()
    universe = load_universe(limit=None)
    row = universe[universe["symbol"] == test_symbol]
    if row.empty:
        pytest.skip(f"symbol {test_symbol} not in universe")
    row = row.iloc[0]
    symbol = str(row["symbol"])
    market = str(row["market"])
    name = str(row["name"])

    # 수집 실행 (청크 단위로 2016~2025)
    sym, ok, err = collect_one(symbol, market, name, TARGET_START, TARGET_END)
    assert ok, f"collect_one failed: {err}"

    # silver 로드 및 연도별 집계
    df = load_silver_for_symbol(symbol)
    assert df is not None and not df.empty, "no silver data after collect"
    year_counts = get_year_counts(df)

    y_start = int(TARGET_START[:4])
    y_end = int(TARGET_END[:4])
    missing = []
    short = []
    for y in range(y_start, y_end + 1):
        cnt = year_counts.get(y, 0)
        if cnt == 0:
            missing.append(y)
        elif cnt < MIN_ROWS_PER_YEAR:
            short.append((y, cnt))

    # 결과 출력
    print("\n--- One-symbol full collect verification ---")
    print(f"symbol={symbol} market={market} name={name}")
    print(f"total rows (in range): {len(df[(df['date'] >= TARGET_START) & (df['date'] <= TARGET_END)])}")
    print("per year:")
    for y in range(y_start, y_end + 1):
        c = year_counts.get(y, 0)
        status = "ok" if c >= MIN_ROWS_PER_YEAR else "SHORT" if c > 0 else "MISSING"
        print(f"  {y}: {c} ({status})")
    if missing:
        print(f"missing years: {missing}")
    if short:
        print(f"short years (<{MIN_ROWS_PER_YEAR}): {short}")

    assert not missing, f"symbol {symbol} has no data for years: {missing}"
    assert not short, f"symbol {symbol} has too few rows for years: {short}"


