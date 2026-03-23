"""
Silver에서 지정일(기본: 전체 Silver의 MAX(date))에 volume=0 인 행을 찾아 KIS로 재수집·병합한다.

장중·마감 직후 수집으로 당일 봉이 volume=0 으로 남는 경우, 이후 run에서 갱신되도록 보조한다.
기존 구간을 덮어쓰기 위해 use_existing=False 로 해당 일만 다시 받는다.

  python -m scripts.repair_zero_volume_day
  python -m scripts.repair_zero_volume_day --date 2026-03-19
  python -m scripts.repair_zero_volume_day --dry-run
  python -m scripts.repair_zero_volume_day --limit 50
  python -m scripts.repair_zero_volume_day --include-zero-close
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.collect.collect_backfill import collect_one
from src.common.logger import get_logger
from src.common.settings import settings
from src.storage import meta_store
from src.transform.build_snapshot import silver_parquet_paths

log = get_logger(__name__)

META_DB = settings.project_root / "meta" / "meta.duckdb"


def load_zero_volume_candidates(
    paths: list,
    target: str,
    *,
    only_positive_close: bool = True,
) -> pd.DataFrame:
    """Silver에서 해당 일자 volume=0 후보(symbol, market, name). paths는 silver_parquet_paths() 결과."""
    vol_filter = "COALESCE(s.volume, 0) = 0"
    close_filter = " AND COALESCE(s.close, 0) > 0" if only_positive_close else ""
    if not META_DB.exists():
        raise FileNotFoundError(f"meta.duckdb not found: {META_DB}")
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{META_DB.as_posix()}' AS meta (READ_ONLY)")
        q = f"""
            SELECT DISTINCT s.symbol, s.market, u.name
            FROM read_parquet(?) AS s
            INNER JOIN meta.universe u ON u.symbol = s.symbol AND u.is_active = TRUE
            WHERE CAST(s.date AS DATE) = CAST(? AS DATE)
              AND {vol_filter}
              {close_filter}
            ORDER BY s.symbol
        """
        return con.execute(q, [paths, target]).fetchdf()
    finally:
        con.close()


def load_low_volume_candidates(
    paths: list,
    target: str,
    *,
    ratio_threshold: float = 0.1,
    lookback_days: int = 30,
    min_baseline_volume: int = 1000,
    min_history_points: int = 10,
    only_positive_close: bool = True,
) -> pd.DataFrame:
    """
    Silver에서 해당 일자 저거래량 이상치 후보를 찾는다.
    - 기준: target 당일 volume > 0 이고, 과거 lookback_days의 중앙값 대비 ratio_threshold 이하
    - 노이즈 완화: baseline(중앙값) < min_baseline_volume 종목은 제외
    """
    if ratio_threshold <= 0 or ratio_threshold >= 1:
        raise ValueError("ratio_threshold must be in (0, 1)")
    if lookback_days < 1:
        raise ValueError("lookback_days must be >= 1")
    if min_baseline_volume < 0:
        raise ValueError("min_baseline_volume must be >= 0")
    if min_history_points < 1:
        raise ValueError("min_history_points must be >= 1")
    close_filter = " AND COALESCE(t.close, 0) > 0" if only_positive_close else ""
    if not META_DB.exists():
        raise FileNotFoundError(f"meta.duckdb not found: {META_DB}")
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{META_DB.as_posix()}' AS meta (READ_ONLY)")
        q = """
            WITH today AS (
              SELECT symbol, market, CAST(volume AS BIGINT) AS volume, close
              FROM read_parquet(?)
              WHERE CAST(date AS DATE) = CAST(? AS DATE)
            ),
            hist AS (
              SELECT
                symbol,
                median(CAST(volume AS DOUBLE)) AS baseline_volume,
                COUNT(*) AS history_points
              FROM read_parquet(?)
              WHERE CAST(date AS DATE) < CAST(? AS DATE)
                AND CAST(date AS DATE) >= CAST(? AS DATE) - (?::INTEGER * INTERVAL '1 day')
                AND COALESCE(volume, 0) > 0
              GROUP BY symbol
            )
            SELECT DISTINCT
              t.symbol,
              t.market,
              u.name,
              CAST(t.volume AS BIGINT) AS volume,
              CAST(h.baseline_volume AS BIGINT) AS baseline_volume,
              CAST(h.history_points AS BIGINT) AS history_points,
              CASE
                WHEN h.baseline_volume > 0 THEN CAST(t.volume AS DOUBLE) / h.baseline_volume
                ELSE NULL
              END AS volume_ratio
            FROM today t
            INNER JOIN hist h ON h.symbol = t.symbol
            INNER JOIN meta.universe u ON u.symbol = t.symbol AND u.is_active = TRUE
            WHERE COALESCE(t.volume, 0) > 0
              AND h.baseline_volume >= ?::BIGINT
              AND h.history_points >= ?::BIGINT
              AND CAST(t.volume AS DOUBLE) <= h.baseline_volume * ?::DOUBLE
              {close_filter}
            ORDER BY volume_ratio ASC, t.symbol
        """.format(close_filter=close_filter)
        return con.execute(
            q,
            [paths, target, paths, target, target, lookback_days, min_baseline_volume, min_history_points, ratio_threshold],
        ).fetchdf()
    finally:
        con.close()


def audit_silver_date(paths: list, target: str) -> dict:
    """해당 일자 Silver 행 통계(볼륨 0 등)."""
    con = duckdb.connect()
    try:
        row = con.execute(
            """
            SELECT
              COUNT(*) AS rows_on_date,
              SUM(CASE WHEN COALESCE(volume, 0) = 0 THEN 1 ELSE 0 END) AS vol0,
              SUM(CASE WHEN COALESCE(volume, 0) = 0 AND COALESCE(close, 0) > 0 THEN 1 ELSE 0 END) AS vol0_close_pos,
              COUNT(DISTINCT symbol) AS symbols
            FROM read_parquet(?)
            WHERE CAST(date AS DATE) = CAST(? AS DATE)
            """,
            [paths, target],
        ).fetchdf().iloc[0]
        return {
            "date": target,
            "rows_on_date": int(row["rows_on_date"]),
            "volume_zero": int(row["vol0"]),
            "volume_zero_close_positive": int(row["vol0_close_pos"]),
            "distinct_symbols": int(row["symbols"]),
        }
    finally:
        con.close()


def run_repair_loop(target: str, rows: list[dict], *, limit: int | None) -> tuple[int, int, int]:
    """collect_one 루프. 반환: (처리 건수, 성공, 실패)"""
    meta_store.ensure_tables()
    if limit is not None:
        rows = rows[:limit]
    ok = 0
    fail = 0
    for r in rows:
        sym = str(r["symbol"])
        mkt = str(r["market"])
        name = str(r.get("name") or sym)
        _, success, err = collect_one(
            sym,
            mkt,
            name,
            target,
            target,
            use_existing=False,
            coverage_threshold=0.7,
        )
        if success:
            ok += 1
        else:
            fail += 1
            log.warning("repair fail %s: %s", sym, err)
    return len(rows), ok, fail


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair silver rows with volume=0 on a given date (re-fetch from KIS)")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: MAX(date) in silver)")
    parser.add_argument("--dry-run", action="store_true", help="List symbols only, no API calls")
    parser.add_argument("--limit", type=int, default=None, help="Max symbols to repair")
    parser.add_argument(
        "--include-zero-close",
        action="store_true",
        help="Also repair volume=0 rows with close<=0 (default: only close>0 and volume=0)",
    )
    args = parser.parse_args()
    only_pos_close = not args.include_zero_close

    paths = silver_parquet_paths()
    if not paths:
        log.info("No silver parquet paths; nothing to repair.")
        return

    if args.date:
        target = args.date
    else:
        con = duckdb.connect()
        try:
            row = con.execute("SELECT MAX(date)::VARCHAR AS d FROM read_parquet(?)", [paths]).fetchone()
        finally:
            con.close()
        if not row or row[0] is None:
            log.info("Could not determine MAX(date); nothing to repair.")
            return
        target = str(row[0])[:10]

    try:
        df = load_zero_volume_candidates(paths, target, only_positive_close=only_pos_close)
    except FileNotFoundError as e:
        log.error("%s", e)
        sys.exit(2)

    n = len(df)
    log.info("repair_zero_volume_day: date=%s candidates=%s (only_positive_close=%s)", target, n, only_pos_close)
    if n == 0:
        return

    if args.dry_run:
        print(df.to_string(index=False))
        return

    rows = df.to_dict(orient="records")
    processed, ok, fail = run_repair_loop(target, rows, limit=args.limit)

    run_id = f"repair_zero_vol_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    meta_store.log_run(
        run_id,
        "repair_zero_volume_day",
        "success" if fail == 0 else "partial",
        processed,
        ok,
        fail,
        note=f"date={target}",
    )
    log.info("repair_zero_volume_day done: ok=%s fail=%s", ok, fail)


if __name__ == "__main__":
    main()
