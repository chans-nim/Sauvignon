from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

from src.collect.gap_detect import MIN_ROWS_PER_YEAR
from src.common.settings import settings

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
    args = parser.parse_args()

    snapshot_path = resolve_snapshot_path(args.tag, args.file_path)
    snapshot = snapshot_path.as_posix()
    meta_available = META_DB.exists()
    if not meta_available and not args.allow_missing_meta:
        raise FileNotFoundError(META_DB)

    con = duckdb.connect()
    try:
        print("[summary]")
        print(
            con.execute(
                """
                SELECT
                    COUNT(*) AS total_rows,
                    COUNT(DISTINCT symbol) AS symbols,
                    MIN(date) AS min_date,
                    MAX(date) AS max_date
                FROM read_parquet(?)
                """,
                [snapshot],
            ).fetchdf().to_string(index=False)
        )
        print()

        print("[integrity]")
        print(
            con.execute(
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
            ).fetchdf().to_string(index=False)
        )
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

        if not meta_available:
            print("[universe_vs_snapshot]")
            print("(meta.duckdb not found; skipped. Re-run with meta present for full universe/short-year checks.)")
            print()
            return

        con.execute(f"ATTACH '{META_DB.as_posix()}' AS meta (READ_ONLY)")

        print("[universe_vs_snapshot]")
        print(
            con.execute(
                """
                WITH active AS (
                    SELECT symbol FROM meta.universe WHERE is_active = TRUE
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

        print("[missing_or_short_symbol_years]")
        missing_or_short = con.execute(
            """
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
                WHERE u.is_active = TRUE
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
                """
                WITH first_seen AS (
                    SELECT symbol, MIN(date) AS first_date
                    FROM read_parquet(?)
                    GROUP BY symbol
                ),
                active AS (
                    SELECT u.symbol
                    FROM meta.universe u
                    LEFT JOIN first_seen f USING(symbol)
                    WHERE u.is_active = TRUE
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


if __name__ == "__main__":
    main()
