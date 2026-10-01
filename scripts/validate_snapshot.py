from __future__ import annotations

import argparse
import os
from pathlib import Path

import duckdb

from src.collect.gap_detect import MIN_ROWS_PER_YEAR
from src.common.settings import settings
from src.master.universe_policy import collectible_universe_sql

SNAPSHOT_DIR = settings.project_root / "data" / "snapshot"
META_DB = settings.project_root / "meta" / "meta.duckdb"


def resolve_snapshot_path(tag: str | None, file_path: str | None) -> Path:
    if file_path:
        return Path(file_path).resolve()
    if not tag:
        raise ValueError("either --tag or --file-path is required")
    path = SNAPSHOT_DIR / f"{tag}.parquet"
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate snapshot/full parquet completeness against active universe")
    parser.add_argument("--tag", default=None, help="Snapshot or full tag without extension")
    parser.add_argument("--file-path", default=None, help="Direct parquet path")
    parser.add_argument("--target-start", default="2016-01-01")
    parser.add_argument("--target-end", default="2025-12-31")
    parser.add_argument("--min-rows-per-year", type=int, default=MIN_ROWS_PER_YEAR)
    parser.add_argument("--sample-limit", type=int, default=15)
    parser.add_argument(
        "--allow-missing-meta",
        action="store_true",
        help="If meta.duckdb is missing, run summary/integrity/coverage only (skip universe checks).",
    )
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when any release-blocking quality check fails.")
    parser.add_argument(
        "--file-only",
        action="store_true",
        help="Strictly validate parquet contents without active-universe checks (for externally staged assets).",
    )
    parser.add_argument("--expected-min-date", default=None)
    parser.add_argument("--expected-max-date", default=None, help="Required MAX(date), normally the collection target date.")
    parser.add_argument("--expected-row-count", default=None)
    parser.add_argument(
        "--completeness-start-date",
        default=None,
        help="Require every expected active symbol on every observed market date from this date through MAX(date).",
    )
    parser.add_argument(
        "--max-zero-volume-close-positive",
        type=int,
        default=-1,
        help="Optional blocker for suspicious zero-volume rows; -1 reports without blocking.",
    )
    parser.add_argument("--max-missing-active-on-max-date", type=int, default=0)
    args = parser.parse_args()

    snapshot_path = resolve_snapshot_path(args.tag, args.file_path)
    snapshot = snapshot_path.as_posix()
    meta_available = META_DB.exists()
    if not meta_available and not args.allow_missing_meta and not args.file_only:
        raise FileNotFoundError(META_DB)

    issues: list[str] = []
    con = duckdb.connect()
    try:
        print("[summary]")
        summary = con.execute(
                """
                SELECT
                    COUNT(*) AS total_rows,
                    COUNT(DISTINCT symbol) AS symbols,
                    MIN(date) AS min_date,
                    MAX(date) AS max_date
                FROM read_parquet(?)
                """,
                [snapshot],
            ).fetchdf()
        print(summary.to_string(index=False))
        if summary.empty or int(summary.iloc[0]["total_rows"] or 0) <= 0:
            issues.append("snapshot has no rows")
        actual_row_count = 0 if summary.empty else int(summary.iloc[0]["total_rows"] or 0)
        if args.expected_row_count and actual_row_count != int(args.expected_row_count):
            issues.append(f"row_count mismatch: expected={args.expected_row_count} actual={actual_row_count}")
        actual_max_date = None if summary.empty else str(summary.iloc[0]["max_date"])[:10]
        actual_min_date = None if summary.empty else str(summary.iloc[0]["min_date"])[:10]
        github_output = (os.getenv("GITHUB_OUTPUT") or "").strip()
        if github_output:
            with open(github_output, "a", encoding="utf-8") as f:
                f.write(f"row_count={actual_row_count}\n")
                f.write(f"min_date={actual_min_date or ''}\n")
                f.write(f"max_date={actual_max_date or ''}\n")
        if args.expected_min_date and actual_min_date != str(args.expected_min_date):
            issues.append(f"min_date mismatch: expected={args.expected_min_date} actual={actual_min_date}")
        if args.expected_max_date and actual_max_date != str(args.expected_max_date):
            issues.append(f"max_date mismatch: expected={args.expected_max_date} actual={actual_max_date}")
        print()

        print("[integrity]")
        integrity = con.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM (
                        SELECT symbol, date
                        FROM read_parquet(?)
                        GROUP BY symbol, date
                        HAVING COUNT(*) > 1
                    )) AS duplicate_symbol_date_keys,
                    (SELECT COUNT(*) FROM read_parquet(?) WHERE close <= 0 OR volume < 0) AS invalid_price_or_volume_rows
                """,
                [snapshot, snapshot],
            ).fetchdf()
        print(integrity.to_string(index=False))
        if not integrity.empty:
            dupes = int(integrity.iloc[0]["duplicate_symbol_date_keys"] or 0)
            invalid = int(integrity.iloc[0]["invalid_price_or_volume_rows"] or 0)
            if dupes:
                issues.append(f"duplicate symbol/date keys={dupes}")
            if invalid:
                issues.append(f"invalid price/volume rows={invalid}")
        print()

        print("[zero_volume_on_max_date]")
        zero_stats = con.execute(
                """
                WITH mx AS (SELECT MAX(date) AS d FROM read_parquet(?)),
                z AS (
                  SELECT s.symbol, s.market, s.date, s.close, s.volume
                  FROM read_parquet(?) AS s, mx
                  WHERE s.date = mx.d
                )
                SELECT
                  (SELECT d::VARCHAR FROM mx) AS max_date,
                  (SELECT COUNT(*) FROM z WHERE COALESCE(volume, 0) = 0) AS rows_volume_zero,
                  (SELECT COUNT(*) FROM z WHERE COALESCE(volume, 0) = 0 AND COALESCE(close, 0) > 0) AS rows_vol0_close_pos,
                  (SELECT COUNT(*) FROM z) AS rows_on_max_date
                """,
                [snapshot, snapshot],
            ).fetchdf()
        print(zero_stats.to_string(index=False))
        if not zero_stats.empty:
            zero_close_pos = int(zero_stats.iloc[0]["rows_vol0_close_pos"] or 0)
            if args.max_zero_volume_close_positive >= 0 and zero_close_pos > args.max_zero_volume_close_positive:
                issues.append(
                    f"zero-volume rows with positive close={zero_close_pos} "
                    f"> allowed={args.max_zero_volume_close_positive}"
                )
        print(
            con.execute(
                """
                WITH mx AS (SELECT MAX(date) AS d FROM read_parquet(?))
                SELECT s.symbol, s.market, s.close, s.volume
                FROM read_parquet(?) AS s, mx
                WHERE s.date = mx.d AND COALESCE(s.volume, 0) = 0 AND COALESCE(s.close, 0) > 0
                ORDER BY s.symbol
                LIMIT 20
                """,
                [snapshot, snapshot],
            ).fetchdf().to_string(index=False)
        )
        print("(sample up to 20: volume=0 and close>0 on max date; run scripts.repair_zero_volume_day on silver to re-fetch)")
        print()

        print("[coverage_by_year]")
        print(
            con.execute(
                """
                SELECT
                    year(date) AS year,
                    COUNT(DISTINCT symbol) AS symbols,
                    COUNT(*) AS rows,
                    MIN(date) AS y_min,
                    MAX(date) AS y_max
                FROM read_parquet(?)
                WHERE date >= ? AND date <= ?
                GROUP BY year(date)
                ORDER BY year(date)
                """,
                [snapshot, args.target_start, args.target_end],
            ).fetchdf().to_string(index=False)
        )
        print()

        if args.file_only:
            if issues and args.strict:
                raise SystemExit("snapshot validation failed: " + "; ".join(issues))
            return

        if not meta_available:
            print("[universe_vs_snapshot]")
            print("(meta.duckdb not found; skipped. Re-run with meta present for full universe/short-year checks.)")
            print()
            if args.strict:
                issues.append("meta.duckdb is missing; active-universe completeness was not checked")
            if issues and args.strict:
                raise SystemExit("snapshot validation failed: " + "; ".join(issues))
            return

        con.execute(f"ATTACH '{META_DB.as_posix()}' AS meta (READ_ONLY)")

        print("[universe_vs_snapshot]")
        print(
            con.execute(
                f"""
                WITH active AS (
                    SELECT symbol FROM meta.universe u WHERE {collectible_universe_sql('u')}
                ),
                snapshot_symbols AS (
                    SELECT DISTINCT symbol FROM read_parquet(?)
                )
                SELECT
                    (SELECT COUNT(*) FROM active) AS active_symbols,
                    (SELECT COUNT(*) FROM snapshot_symbols) AS snapshot_symbols,
                    (SELECT COUNT(*) FROM active a LEFT JOIN snapshot_symbols s USING(symbol) WHERE s.symbol IS NULL) AS active_symbols_missing_any_data
                """,
                [snapshot],
            ).fetchdf().to_string(index=False)
        )
        print()

        print("[active_symbols_on_max_date]")
        active_latest = con.execute(
            f"""
            WITH mx AS (
                SELECT MAX(date)::DATE AS d FROM read_parquet(?)
            ),
            expected AS (
                SELECT u.symbol
                FROM meta.universe u, mx
                WHERE {collectible_universe_sql('u')}
                  AND COALESCE(u.is_trading_halt, FALSE) = FALSE
                  AND (u.listing_date IS NULL OR u.listing_date <= mx.d)
            ),
            present AS (
                SELECT DISTINCT s.symbol
                FROM read_parquet(?) s, mx
                WHERE CAST(s.date AS DATE) = mx.d
            )
            SELECT
                (SELECT d::VARCHAR FROM mx) AS max_date,
                (SELECT COUNT(*) FROM expected) AS expected_active_symbols,
                (SELECT COUNT(*) FROM present) AS present_symbols,
                (SELECT COUNT(*) FROM expected e LEFT JOIN present p USING(symbol) WHERE p.symbol IS NULL) AS missing_active_symbols
            """,
            [snapshot, snapshot],
        ).fetchdf()
        print(active_latest.to_string(index=False))
        if not active_latest.empty:
            missing_latest = int(active_latest.iloc[0]["missing_active_symbols"] or 0)
            if missing_latest > args.max_missing_active_on_max_date:
                issues.append(
                    f"active symbols missing on max date={missing_latest} "
                    f"> allowed={args.max_missing_active_on_max_date}"
                )
        print()

        if args.completeness_start_date:
            print("[active_symbol_date_completeness]")
            range_completeness = con.execute(
                f"""
                WITH bounds AS (
                    SELECT MAX(date)::DATE AS max_date FROM read_parquet(?)
                ),
                market_dates AS (
                    SELECT DISTINCT CAST(date AS DATE) AS d
                    FROM read_parquet(?), bounds
                    WHERE CAST(date AS DATE) BETWEEN CAST(? AS DATE) AND bounds.max_date
                ),
                expected AS (
                    SELECT d.d, u.symbol
                    FROM market_dates d
                    JOIN meta.universe u
                      ON {collectible_universe_sql('u')}
                     AND COALESCE(u.is_trading_halt, FALSE) = FALSE
                     AND (u.listing_date IS NULL OR u.listing_date <= d.d)
                ),
                present AS (
                    SELECT DISTINCT CAST(date AS DATE) AS d, symbol
                    FROM read_parquet(?), bounds
                    WHERE CAST(date AS DATE) BETWEEN CAST(? AS DATE) AND bounds.max_date
                ),
                missing AS (
                    SELECT e.d, e.symbol
                    FROM expected e
                    LEFT JOIN present p USING(d, symbol)
                    WHERE p.symbol IS NULL
                )
                SELECT
                    (SELECT COUNT(*) FROM market_dates) AS observed_market_dates,
                    (SELECT COUNT(*) FROM expected) AS expected_symbol_dates,
                    (SELECT COUNT(*) FROM missing) AS missing_symbol_dates
                """,
                [snapshot, snapshot, args.completeness_start_date, snapshot, args.completeness_start_date],
            ).fetchdf()
            print(range_completeness.to_string(index=False))
            if not range_completeness.empty:
                missing_symbol_dates = int(range_completeness.iloc[0]["missing_symbol_dates"] or 0)
                if missing_symbol_dates:
                    issues.append(
                        f"missing active symbol/date rows from {args.completeness_start_date}={missing_symbol_dates}"
                    )
            print()

        print("[missing_or_short_symbol_years]")
        missing_or_short = con.execute(
            f"""
            WITH first_seen AS (
                SELECT symbol, MIN(date) AS first_date
                FROM read_parquet(?)
                GROUP BY symbol
            ),
            active AS (
                SELECT
                    u.symbol,
                    u.name,
                    u.market,
                    u.listing_date,
                    COALESCE(u.listing_date, f.first_date, CAST(? AS DATE)) AS effective_start_date,
                    year(COALESCE(u.listing_date, f.first_date, CAST(? AS DATE))) AS effective_start_year
                FROM meta.universe u
                LEFT JOIN first_seen f ON u.symbol = f.symbol
                WHERE {collectible_universe_sql('u')}
            ),
            years AS (
                SELECT * FROM generate_series(year(CAST(? AS DATE)), year(CAST(? AS DATE)))
            ),
            counts AS (
                SELECT symbol, year(date) AS year, COUNT(*) AS cnt
                FROM read_parquet(?)
                WHERE date >= ? AND date <= ?
                GROUP BY symbol, year(date)
            )
            SELECT
                a.symbol,
                a.market,
                a.name,
                y.generate_series AS year,
                COALESCE(c.cnt, 0) AS row_count,
                a.listing_date,
                a.effective_start_date
            FROM active a
            CROSS JOIN years y
            LEFT JOIN counts c
              ON a.symbol = c.symbol
             AND y.generate_series = c.year
            WHERE y.generate_series >= a.effective_start_year
              AND (
                    (
                        y.generate_series = a.effective_start_year
                        AND a.effective_start_date > date_trunc('year', a.effective_start_date)
                        AND COALESCE(c.cnt, 0) = 0
                    )
                    OR (
                        y.generate_series > a.effective_start_year
                        AND COALESCE(c.cnt, 0) < ?
                    )
                  )
            ORDER BY y.generate_series, a.market, a.symbol
            """,
            [snapshot, args.target_start, args.target_start, args.target_start, args.target_end, snapshot, args.target_start, args.target_end, args.min_rows_per_year],
        ).fetchdf()
        print(f"total={len(missing_or_short)}")
        if not missing_or_short.empty:
            by_year = missing_or_short.groupby("year").size().reset_index(name="short_symbol_years")
            print(by_year.to_string(index=False))
            print()
            print(missing_or_short.head(args.sample_limit).to_string(index=False))
        print()

        print("[missing_symbols_2016_2025]")
        for year, start_date, end_date in [
            (2016, "2016-01-01", "2016-12-31"),
            (2025, "2025-01-01", "2025-12-31"),
        ]:
            missing = con.execute(
                f"""
                WITH first_seen AS (
                    SELECT symbol, MIN(date) AS first_date
                    FROM read_parquet(?)
                    GROUP BY symbol
                ),
                active AS (
                    SELECT u.symbol
                    FROM meta.universe u
                    LEFT JOIN first_seen f USING(symbol)
                    WHERE {collectible_universe_sql('u')}
                      AND COALESCE(u.listing_date, f.first_date, CAST(? AS DATE)) <= CAST(? AS DATE)
                ),
                present AS (
                    SELECT DISTINCT symbol
                    FROM read_parquet(?)
                    WHERE date >= ? AND date <= ?
                )
                SELECT a.symbol
                FROM active a
                LEFT JOIN present p USING(symbol)
                WHERE p.symbol IS NULL
                ORDER BY a.symbol
                """,
                [snapshot, end_date, end_date, snapshot, start_date, end_date],
            ).fetchdf()
            print(f"{year}: missing={len(missing)}")
            if not missing.empty:
                print(missing.head(args.sample_limit).to_string(index=False))
        print()

    finally:
        con.close()

    if args.strict and issues:
        raise SystemExit("snapshot validation failed: " + "; ".join(issues))


if __name__ == "__main__":
    main()
