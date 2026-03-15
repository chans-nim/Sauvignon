"""
한 종목만 대상으로 10년치 수집을 실행한 뒤, silver에 연도별로 데이터가 채워졌는지 검증한다.
단위 테스트용: 전체 수집이 한 종목에서 기대대로 동작하는지 확인.

실행 (프로젝트 루트):
  python -m scripts.test_one_symbol_collect --symbol 005930
  python -m scripts.test_one_symbol_collect --symbol 000660
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.settings import settings
from src.storage.meta_store import ensure_tables, load_universe
from src.collect.collect_backfill import collect_one

SILVER_DIR = settings.project_root / "data" / "lake" / "silver" / "ohlcv_daily"
TARGET_START = "2016-01-01"
TARGET_END = "2025-12-31"
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
    if df is None or df.empty:
        return {}
    sub = df[(df["date"] >= TARGET_START) & (df["date"] <= TARGET_END)]
    if sub.empty:
        return {}
    return sub["date"].dt.year.value_counts().sort_index().to_dict()


def main() -> None:
    parser = argparse.ArgumentParser(description="One-symbol full collect test (2016~2025)")
    parser.add_argument("--symbol", default="005930", help="Symbol to collect and verify")
    parser.add_argument(
        "--no-reuse",
        action="store_true",
        help="Ignore existing silver and fetch all chunks (benchmark worst-case)",
    )
    args = parser.parse_args()
    symbol = args.symbol.strip()

    if not settings.kis_app_key or not settings.kis_app_secret:
        print("ERROR: KIS_APP_KEY / KIS_APP_SECRET not set in .env")
        sys.exit(2)

    ensure_tables()
    universe = load_universe(limit=None)
    row = universe[universe["symbol"] == symbol]
    if row.empty:
        print(f"ERROR: symbol {symbol} not in universe")
        sys.exit(2)
    row = row.iloc[0]
    market = str(row["market"])
    name = str(row["name"])

    print(f"Collecting {symbol} ({market}) {name} for {TARGET_START}..{TARGET_END} ...")
    sym, ok, err = collect_one(
        symbol,
        market,
        name,
        TARGET_START,
        TARGET_END,
        use_existing=not args.no_reuse,
    )
    if not ok:
        print(f"ERROR: collect_one failed: {err}")
        sys.exit(1)

    df = load_silver_for_symbol(symbol)
    if df is None or df.empty:
        print("ERROR: no silver data after collect")
        sys.exit(1)

    year_counts = get_year_counts(df)
    in_range = df[(df["date"] >= TARGET_START) & (df["date"] <= TARGET_END)]
    total = len(in_range)

    print("\n--- One-symbol full collect verification ---")
    print(f"symbol={symbol} market={market} name={name}")
    print(f"total rows (in range): {total}")
    print("per year:")
    y_start = int(TARGET_START[:4])
    y_end = int(TARGET_END[:4])
    missing = []
    short = []
    for y in range(y_start, y_end + 1):
        c = year_counts.get(y, 0)
        status = "ok" if c >= MIN_ROWS_PER_YEAR else "SHORT" if c > 0 else "MISSING"
        print(f"  {y}: {c} ({status})")
        if c == 0:
            missing.append(y)
        elif c < MIN_ROWS_PER_YEAR:
            short.append((y, c))

    if missing:
        print(f"missing years: {missing}")
    if short:
        print(f"short years (row count < {MIN_ROWS_PER_YEAR}): {short}")

    if missing or short:
        print("\nFAIL: not all years have sufficient data.")
        sys.exit(1)
    print("\nPASS: all years have at least", MIN_ROWS_PER_YEAR, "rows.")
    sys.exit(0)


if __name__ == "__main__":
    main()
