"""
수집된 silver 데이터에서 한 종목의 10년치 가격 차트를 그리는 테스트 스크립트.

사용법 (프로젝트 루트에서):
  python -m scripts.chart_ticker --symbol 005930
  python -m scripts.chart_ticker --symbol 000660 --out chart_000660.png

의존성: pip install -r requirements.txt (matplotlib 포함)
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.settings import settings
from src.common.logger import get_logger

log = get_logger(__name__)

SILVER_DIR = settings.project_root / "data" / "lake" / "silver" / "ohlcv_daily"

TARGET_START = "2016-01-01"
TARGET_END = "2025-12-31"
# 연도당 최소 기대 row 수 (한 종목 풀수집 검증 기준과 동일)
MIN_ROWS_PER_YEAR = 201


def load_ticker_ohlcv(symbol: str):
    """symbol에 해당하는 모든 silver parquet를 읽어 하나의 DataFrame으로 반환."""
    import duckdb

    symbol = str(symbol).strip()
    # data/lake/silver/ohlcv_daily/market=KOSPI/symbol=005930/year=2024/data.parquet
    parquet_files = []
    for p in SILVER_DIR.rglob("data.parquet"):
        if f"symbol={symbol}" in p.as_posix():
            parquet_files.append(p.as_posix())

    if not parquet_files:
        raise FileNotFoundError(f"no silver data for symbol={symbol} under {SILVER_DIR}")

    con = duckdb.connect()
    df = con.execute(
        "SELECT * FROM read_parquet(?) ORDER BY date",
        [parquet_files],
    ).fetchdf()
    con.close()
    df["date"] = df["date"].dt.normalize()
    return df.sort_values("date").reset_index(drop=True)


def summarize_coverage(df, symbol: str) -> None:
    """지정된 기간(2016~2025)에 대해 연도별 row 수를 요약하고, 부족한 연도가 있으면 로그로 알려준다."""
    if df.empty:
        log.error("no data for symbol=%s", symbol)
        return
    sub = df[(df["date"] >= TARGET_START) & (df["date"] <= TARGET_END)]
    if sub.empty:
        log.warning(
            "symbol=%s has no data in range %s..%s",
            symbol,
            TARGET_START,
            TARGET_END,
        )
        return
    year_counts = sub["date"].dt.year.value_counts().sort_index().to_dict()
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
    log.info("coverage summary for symbol=%s %s..%s", symbol, TARGET_START, TARGET_END)
    for y in range(y_start, y_end + 1):
        c = year_counts.get(y, 0)
        status = "ok" if c >= MIN_ROWS_PER_YEAR else "SHORT" if c > 0 else "MISSING"
        log.info("  %s: %s (%s)", y, c, status)
    if missing:
        log.warning("symbol=%s missing years in %s..%s: %s", symbol, TARGET_START, TARGET_END, missing)
    if short:
        log.warning(
            "symbol=%s has years with too few rows (<%s): %s",
            symbol,
            MIN_ROWS_PER_YEAR,
            short,
        )


def plot_ohlcv(df, symbol: str, title: str | None, out_path: str | None):
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    if df.empty:
        raise ValueError("데이터가 비어 있어 차트를 그릴 수 없습니다.")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 6), height_ratios=[3, 1], sharex=True)
    fig.subplots_adjust(hspace=0.05)

    x = df["date"]
    ax1.plot(x, df["close"], color="steelblue", linewidth=1, label="Close")
    ax1.set_ylabel("Close (KRW)")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax1.xaxis.set_major_locator(mdates.YearLocator())
    if title:
        ax1.set_title(title)

    ax2.bar(x, df["volume"] / 1e6, color="gray", alpha=0.6, width=1)
    ax2.set_ylabel("Volume (M)")
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    plt.xticks(rotation=45)

    if out_path:
        plt.savefig(out_path, dpi=150, bbox_inches="tight")
        log.info("saved %s", out_path)
    else:
        plt.show()
    plt.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="한 종목 10년치 가격 차트")
    parser.add_argument("--symbol", required=True, help="종목 티커 (예: 005930, 000660)")
    parser.add_argument("--out", default=None, help="저장할 이미지 경로 (없으면 화면 표시)")
    parser.add_argument("--title", default=None, help="차트 제목 (기본: 종목코드 + 기간)")
    args = parser.parse_args()

    symbol = args.symbol.strip()
    log.info("load silver data for symbol=%s", symbol)
    df = load_ticker_ohlcv(symbol)

    if df.empty:
        log.error("no rows for symbol=%s", symbol)
        sys.exit(1)

    min_date = df["date"].min()
    max_date = df["date"].max()
    title = args.title or f"{symbol} ({min_date.date()} ~ {max_date.date()})"
    log.info("rows=%s date_range=%s ~ %s", len(df), min_date.date(), max_date.date())

    # 2016~2025 구간에 대해 실제 데이터가 충분히 있는지 검증/요약
    summarize_coverage(df, symbol)

    plot_ohlcv(df, symbol, title, args.out)


if __name__ == "__main__":
    main()
