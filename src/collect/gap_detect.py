"""
Silver 데이터를 읽어 연도별로 비어 있거나 부족한 구간을 찾는다.
반환: (symbol, market, name, start_date, end_date) 리스트 — 재수집 대상 구간.
"""
from __future__ import annotations
from datetime import datetime as dt, timedelta
from pathlib import Path
from typing import List, Tuple

import duckdb
import pandas as pd

from src.common.settings import settings
from src.storage import meta_store

SILVER_DIR = settings.project_root / "data" / "lake" / "silver" / "ohlcv_daily"

# 연도당 최소 거래일 수 미만이면 갭으로 간주 (전체 수집 검증 기준과 동일)
MIN_ROWS_PER_YEAR = 200


def get_silver_parquet_paths() -> List[str]:
    paths = []
    if not SILVER_DIR.exists():
        return paths
    for p in SILVER_DIR.rglob("data.parquet"):
        paths.append(p.as_posix())
    return paths


def _effective_start_date(
    listing_date: pd.Timestamp | None,
    first_seen_date: pd.Timestamp | None,
    target_start: pd.Timestamp,
) -> pd.Timestamp:
    dates = [d for d in [listing_date, first_seen_date, target_start] if pd.notna(d)]
    if not dates:
        return target_start
    return max(pd.Timestamp(d).normalize() for d in dates)


def get_gaps(
    target_start: str = "2016-01-01",
    target_end: str = "2025-12-31",
    min_rows_per_year: int = MIN_ROWS_PER_YEAR,
) -> pd.DataFrame:
    """
    Silver에서 연도별 row 수를 세고, min_rows_per_year 미만인 (symbol, year)를 갭으로 보고
    (symbol, market, name, start_date, end_date) DataFrame을 반환한다.
    """
    paths = get_silver_parquet_paths()
    if not paths:
        return pd.DataFrame(columns=["symbol", "market", "name", "start_date", "end_date"])

    con = duckdb.connect()
    try:
        # 연도별 symbol별 행 수
        by_sym_year = con.execute("""
            SELECT symbol, year(date) AS y, COUNT(*) AS cnt
            FROM read_parquet(?)
            WHERE date >= ? AND date <= ?
            GROUP BY symbol, year(date)
        """, [paths, target_start, target_end]).fetchdf()
        first_seen = con.execute("""
            SELECT symbol, MIN(date) AS first_date
            FROM read_parquet(?)
            GROUP BY symbol
        """, [paths]).fetchdf()
    finally:
        con.close()

    # target 연도 범위
    target_start_ts = pd.Timestamp(target_start).normalize()
    target_end_ts = pd.Timestamp(target_end).normalize()
    y_start = int(target_start_ts.year)
    y_end = int(target_end_ts.year)
    all_years = list(range(y_start, y_end + 1))
    first_seen_map = {
        str(row["symbol"]): pd.Timestamp(row["first_date"]).normalize()
        for _, row in first_seen.iterrows()
        if pd.notna(row["first_date"])
    }

    # 각 symbol에 대해 부족한 연도 찾기
    meta_store.ensure_tables()
    con_meta = meta_store.connect()
    try:
        universe_df = con_meta.execute("""
            SELECT symbol, name, market, listing_date
            FROM universe
            WHERE is_active = TRUE
            ORDER BY market, symbol
        """).fetchdf()
    finally:
        con_meta.close()

    rows: List[Tuple[str, str, str, str, str]] = []
    for _i, u in universe_df.iterrows():
        symbol = str(u["symbol"])
        market = str(u["market"])
        name = str(u["name"])
        listing_date = pd.Timestamp(u["listing_date"]).normalize() if pd.notna(u["listing_date"]) else None
        first_seen_date = first_seen_map.get(symbol)
        effective_start = _effective_start_date(listing_date, first_seen_date, target_start_ts)
        sym_data = by_sym_year[by_sym_year["symbol"] == symbol]
        have_years = set(sym_data["y"].tolist()) if not sym_data.empty else set()
        for y in all_years:
            if y < effective_start.year:
                continue
            cnt = int(sym_data[sym_data["y"] == y]["cnt"].sum()) if not sym_data.empty else 0
            is_partial_start_year = (
                y == effective_start.year
                and effective_start > pd.Timestamp(year=y, month=1, day=1)
            )
            if is_partial_start_year:
                if cnt == 0:
                    s = max(effective_start, target_start_ts).date().isoformat()
                    e = f"{y}-12-31"
                    if y == y_end:
                        e = target_end
                    rows.append((symbol, market, name, s, e))
                continue

            if y not in have_years or cnt < min_rows_per_year:
                s = f"{y}-01-01"
                e = f"{y}-12-31"
                if y == y_end:
                    e = target_end
                if y == y_start:
                    s = target_start
                rows.append((symbol, market, name, s, e))

    if not rows:
        return pd.DataFrame(columns=["symbol", "market", "name", "start_date", "end_date"])
    return pd.DataFrame(rows, columns=["symbol", "market", "name", "start_date", "end_date"])


def get_gaps_merged(
    target_start: str = "2016-01-01",
    target_end: str = "2025-12-31",
    min_rows_per_year: int = MIN_ROWS_PER_YEAR,
) -> pd.DataFrame:
    """
    get_gaps()와 동일하지만, 같은 종목의 인접한 연도 구간을 하나로 합쳐 반환한다.
    호출 횟수·parquet 병합 횟수를 줄여 속도를 올리기 위함.
    """
    gap_df = get_gaps(target_start, target_end, min_rows_per_year)
    if gap_df.empty:
        return gap_df

    # 종목별로 start_date 순 정렬 후 인접 구간 병합
    merged_rows: List[Tuple[str, str, str, str, str]] = []
    for symbol, grp in gap_df.groupby("symbol", sort=False):
        market = str(grp["market"].iloc[0])
        name = str(grp["name"].iloc[0])
        intervals = sorted(grp[["start_date", "end_date"]].apply(tuple, axis=1).tolist())
        # 병합: (s1,e1), (s2,e2) -> e1+1일 >= s2 이면 (s1, max(e1,e2))
        cur_s, cur_e = intervals[0]
        for s, e in intervals[1:]:
            cur_end = dt.fromisoformat(cur_e).date()
            next_start = dt.fromisoformat(s).date()
            if (cur_end + timedelta(days=1)) >= next_start:
                cur_e = max(cur_e, e)
            else:
                merged_rows.append((symbol, market, name, cur_s, cur_e))
                cur_s, cur_e = s, e
        merged_rows.append((symbol, market, name, cur_s, cur_e))

    return pd.DataFrame(merged_rows, columns=["symbol", "market", "name", "start_date", "end_date"])


def get_gap_intervals(
    target_start: str = "2016-01-01",
    target_end: str = "2025-12-31",
    min_rows_per_year: int = MIN_ROWS_PER_YEAR,
    merge: bool = True,
) -> pd.DataFrame:
    """
    증분/백필 후 공통으로 호출할 수 있는 갭 조회 래퍼.
    merge=True 이면 같은 종목의 인접 연도 구간을 병합한다.
    """
    if merge:
        return get_gaps_merged(
            target_start=target_start,
            target_end=target_end,
            min_rows_per_year=min_rows_per_year,
        )
    return get_gaps(
        target_start=target_start,
        target_end=target_end,
        min_rows_per_year=min_rows_per_year,
    )
