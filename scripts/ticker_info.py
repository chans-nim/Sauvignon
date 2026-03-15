"""
티커(종목코드)를 입력하면 해당 종목의 수집/저장 정보를 한눈에 정리한 리포트로 보여준다.
오늘 날짜와 각 필드의 의미(설명)를 포함한다.

사용법 (프로젝트 루트에서):
  python -m scripts.ticker_info --symbol 005930
  python -m scripts.ticker_info --symbol 000540 --out 000540_info.txt
"""
from __future__ import annotations
import argparse
from datetime import datetime
import sys
from pathlib import Path

if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.settings import settings
from src.storage import meta_store, parquet_store

SILVER_DIR = settings.project_root / "data" / "lake" / "silver" / "ohlcv_daily"
RAW_OHLCV_DIR = settings.project_root / "data" / "raw" / "ohlcv"
TARGET_START = "2016-01-01"
TARGET_END = "2025-12-31"
MIN_ROWS_PER_YEAR = 200

# 리포트 내 필드별 의미 (한글)
FIELD_MEANINGS = {
    "report_date": "리포트를 생성한 날짜(현재 시각 기준).",
    "name": "유니버스에 등록된 종목 한글명.",
    "market": "시장 구분. KOSPI / KOSDAQ 등.",
    "last_success_date": "일봉 수집이 마지막으로 성공한 거래일(데이터 기준일).",
    "last_attempt_at": "해당 종목에 대해 수집을 마지막으로 시도한 시각.",
    "retry_count": "수집 실패 후 재시도한 횟수. 0이면 최근 수집이 성공한 상태.",
    "last_error": "가장 최근 수집 실패 시 API/처리 오류 메시지.",
    "updated_at": "collect_state 레코드가 마지막으로 갱신된 시각.",
    "path": "Silver 파티션 기준 경로 (market/symbol/year 단위 저장).",
    "parquets": "연도별 파티션 수. 연도 하나당 data.parquet 한 개.",
    "total_rows": "Silver에 저장된 해당 종목의 전체 일봉 행 수.",
    "date_range": "Silver 데이터의 실제 최초일~최종일.",
    "in_range_rows": "2016-01-01 ~ 2025-12-31 구간 내 행 수(검증용).",
    "per_year": "연도별 거래일 수. ok=200행 이상, SHORT=200미만, MISSING=해당 연도 없음.",
    "raw_files": "KIS API 원본 응답을 저장한 JSON 파일 수. 날짜 폴더별로 수집일 기준 보관.",
}


def get_universe_row(symbol: str):
    """유니버스에서 종목 정보 반환. 없으면 None."""
    meta_store.ensure_tables()
    df = meta_store.load_universe(limit=None)
    row = df[df["symbol"] == symbol.strip()]
    if row.empty:
        return None
    return row.iloc[0].to_dict()


def get_collect_state(symbol: str):
    """collect_state에서 해당 종목 1d 타임프레임 정보 반환. 없으면 None."""
    con = meta_store.connect()
    row = con.execute(
        """
        SELECT last_success_date, last_attempt_at, retry_count, last_error, updated_at
        FROM collect_state
        WHERE symbol = ? AND timeframe = '1d'
        """,
        [symbol],
    ).fetchone()
    con.close()
    if not row:
        return None
    return {
        "last_success_date": row[0],
        "last_attempt_at": row[1],
        "retry_count": row[2],
        "last_error": row[3],
        "updated_at": row[4],
    }


def get_silver_info(symbol: str, market: str):
    """Silver 파티션 경로, 연도별 행 수, 전체 기간/행 수 반환."""
    base = SILVER_DIR / f"market={market}" / f"symbol={symbol}"
    if not base.exists():
        return None

    import duckdb

    paths = [p.as_posix() for p in base.rglob("data.parquet")]
    if not paths:
        return None

    con = duckdb.connect()
    df = con.execute(
        "SELECT date, year(date) AS y FROM read_parquet(?) ORDER BY date",
        [paths],
    ).fetchdf()
    con.close()
    df["date"] = df["date"].dt.normalize()

    total_rows = len(df)
    min_date = df["date"].min()
    max_date = df["date"].max()
    year_counts = df["y"].value_counts().sort_index().to_dict()

    # 2016~2025 구간 요약
    sub = df[(df["date"] >= TARGET_START) & (df["date"] <= TARGET_END)]
    in_range_rows = len(sub)
    in_range_years = sub["date"].dt.year.value_counts().sort_index().to_dict() if not sub.empty else {}

    return {
        "base_path": str(base),
        "parquet_count": len(paths),
        "total_rows": total_rows,
        "min_date": min_date,
        "max_date": max_date,
        "year_counts": year_counts,
        "in_range_rows": in_range_rows,
        "in_range_year_counts": in_range_years,
    }


def get_raw_files(symbol: str):
    """해당 종목의 raw JSON 파일 목록 (날짜별 디렉터리, 파일명)."""
    if not RAW_OHLCV_DIR.exists():
        return []
    out = []
    for day_dir in sorted(RAW_OHLCV_DIR.iterdir()):
        if not day_dir.is_dir():
            continue
        for f in day_dir.glob(f"{symbol}*.json"):
            out.append((day_dir.name, f.name))
    return sorted(out, key=lambda x: (x[0], x[1]))


def format_report(
    symbol: str,
    universe: dict | None,
    state: dict | None,
    silver: dict | None,
    raw_list: list,
    report_date: datetime | None = None,
) -> str:
    if report_date is None:
        report_date = datetime.now()
    report_ts = report_date.strftime("%Y-%m-%d %H:%M:%S")

    lines = []
    lines.append("=" * 64)
    lines.append("  종목 수집/저장 현황 리포트")
    lines.append("=" * 64)
    lines.append("")
    lines.append(f"  리포트 작성일: {report_ts}")
    lines.append(f"  종목코드:       {symbol}")
    lines.append("")

    # 1. 유니버스
    lines.append("-" * 64)
    lines.append("[1] Universe (유니버스)")
    lines.append(f"    의미: {FIELD_MEANINGS['name'].rstrip('.')} / {FIELD_MEANINGS['market'].rstrip('.')}")
    lines.append("")
    if universe:
        lines.append(f"    name:   {universe.get('name', '')}")
        lines.append(f"    market: {universe.get('market', '')}")
    else:
        lines.append("    (not in universe)")

    # 2. 수집 상태
    lines.append("")
    lines.append("-" * 64)
    lines.append("[2] Collect state (수집 상태, 1d 일봉)")
    lines.append("    의미: 마지막 수집 시도/성공 정보 및 오류 여부.")
    lines.append("")
    if state:
        lines.append(f"    last_success_date: {state.get('last_success_date')}  # 데이터 기준 마지막 성공 거래일")
        lines.append(f"    last_attempt_at:   {state.get('last_attempt_at')}   # 마지막 수집 시도 시각")
        lines.append(f"    retry_count:       {state.get('retry_count')}       # 실패 후 재시도 횟수 (0=정상)")
        lines.append(f"    updated_at:        {state.get('updated_at')}        # 레코드 갱신 시각")
        err = state.get("last_error")
        if err:
            lines.append(f"    last_error:        {str(err)[:70]}{'...' if len(str(err)) > 70 else ''}")
    else:
        lines.append("    (no collect_state)")

    # 3. Silver
    lines.append("")
    lines.append("-" * 64)
    lines.append("[3] Silver (ohlcv_daily, 정제 일봉)")
    lines.append("    의미: 연도별 파티션에 저장된 일봉 행 수/기간. per year는 2016~2025 검증 기준(200행/년).")
    lines.append("")
    if silver:
        lines.append(f"    path:        {silver['base_path']}")
        lines.append(f"    parquets:    {silver['parquet_count']} (year partitions)")
        lines.append(f"    total_rows: {silver['total_rows']}")
        lines.append(f"    date_range: {silver['min_date'].date()} ~ {silver['max_date'].date()}")
        lines.append(f"    in_range ({TARGET_START}..{TARGET_END}): {silver['in_range_rows']} rows")
        lines.append("    per year (in range):")
        y_start = int(TARGET_START[:4])
        y_end = int(TARGET_END[:4])
        for y in range(y_start, y_end + 1):
            c = silver["in_range_year_counts"].get(y, 0)
            status = "ok" if c >= MIN_ROWS_PER_YEAR else "SHORT" if c > 0 else "MISSING"
            lines.append(f"      {y}: {c} ({status})")
    else:
        lines.append("    (no silver data)")

    # 4. Raw
    lines.append("")
    lines.append("-" * 64)
    lines.append("[4] Raw (JSON, API 원본)")
    lines.append(f"    의미: {FIELD_MEANINGS['raw_files']}")
    lines.append("")
    if raw_list:
        lines.append(f"    files: {len(raw_list)}")
        by_date = {}
        for d, fname in raw_list:
            by_date.setdefault(d, []).append(fname)
        for d in sorted(by_date.keys())[:5]:
            lines.append(f"      {d}: {len(by_date[d])} file(s)")
        if len(by_date) > 5:
            lines.append(f"      ... and {len(by_date) - 5} more date(s)")
    else:
        lines.append("    (no raw files)")

    # 5. 필드 의미 정리
    lines.append("")
    lines.append("-" * 64)
    lines.append("[필드 의미 정리]")
    lines.append("")
    for key, meaning in FIELD_MEANINGS.items():
        if key == "report_date":
            continue
        lines.append(f"  - {key}: {meaning}")
    lines.append("")
    lines.append("=" * 64)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="종목별 수집/저장 정보 요약")
    parser.add_argument("--symbol", required=True, help="종목코드 (예: 005930, 000540)")
    parser.add_argument("--out", default=None, help="결과를 저장할 파일 경로 (없으면 stdout)")
    args = parser.parse_args()
    symbol = args.symbol.strip()

    universe = get_universe_row(symbol)
    if universe is None:
        print(f"ERROR: symbol '{symbol}' not found in universe.", file=sys.stderr)
        sys.exit(2)
    market = str(universe["market"])

    state = get_collect_state(symbol)
    silver = get_silver_info(symbol, market)
    raw_list = get_raw_files(symbol)

    report = format_report(symbol, universe, state, silver, raw_list, report_date=datetime.now())

    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"Wrote: {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
