"""
수집 결과 검증: run_log, collect_state, silver parquet 요약 및 품질 체크.
실행: 프로젝트 루트에서 python -m scripts.verify_collection
"""
from __future__ import annotations
from pathlib import Path
import sys

# 프로젝트 루트를 path에 넣어서 src 임포트 가능하게 (직접 실행 시)
if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb
from src.common.settings import settings
from src.storage.meta_store import ensure_tables

PROJECT_ROOT = settings.project_root
META_DB = PROJECT_ROOT / "meta" / "meta.duckdb"
SILVER_GLOB = (PROJECT_ROOT / "data" / "lake" / "silver" / "ohlcv_daily").as_posix() + "/**/data.parquet"


def main() -> None:
    ensure_tables()
    con_meta = duckdb.connect(META_DB.as_posix(), read_only=True)

    print("=" * 60)
    print("1. run_log (백필/증분 실행 이력)")
    print("=" * 60)
    try:
        df = con_meta.execute("""
            SELECT run_id, job_name, started_at, status, total_symbols, success_symbols, failed_symbols, note
            FROM run_log
            ORDER BY started_at DESC
            LIMIT 30
        """).fetchdf()
        if df.empty:
            print("  (행 없음)")
        else:
            print(df.to_string(index=False))
    except Exception as e:
        print(f"  ERROR: {e}")

    print()
    print("2. collect_state 요약 (1d)")
    print("=" * 60)
    try:
        summary = con_meta.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN last_success_date IS NOT NULL THEN 1 ELSE 0 END) AS success_cnt,
                SUM(CASE WHEN last_error IS NOT NULL THEN 1 ELSE 0 END) AS error_cnt,
                MIN(last_success_date) AS min_date,
                MAX(last_success_date) AS max_date
            FROM collect_state
            WHERE timeframe = '1d'
        """).fetchdf()
        print(summary.to_string(index=False))
        failed = con_meta.execute("""
            SELECT symbol, last_success_date, last_error
            FROM collect_state
            WHERE timeframe = '1d' AND last_error IS NOT NULL
            ORDER BY updated_at DESC
            LIMIT 10
        """).fetchdf()
        if not failed.empty:
            print("\n  최근 실패 종목 (상위 10):")
            print(failed.to_string(index=False))
    except Exception as e:
        print(f"  ERROR: {e}")

    print()
    print("3. universe vs collect_state (활성 종목 대비 수집 상태)")
    print("=" * 60)
    try:
        u = con_meta.execute("SELECT COUNT(*) AS c FROM universe WHERE is_active = TRUE").fetchone()[0]
        c = con_meta.execute("SELECT COUNT(*) FROM collect_state WHERE timeframe = '1d'").fetchone()[0]
        print(f"  universe(활성): {u}, collect_state(1d): {c}")
    except Exception as e:
        print(f"  ERROR: {e}")

    con_meta.close()

    print()
    print("4. Silver Parquet 데이터 요약 (전체 읽기)")
    print("=" * 60)
    parquet_files = list(Path(PROJECT_ROOT, "data", "lake", "silver", "ohlcv_daily").rglob("data.parquet"))
    if not parquet_files:
        print("  silver parquet 파일 없음.")
    else:
        paths = [p.as_posix() for p in parquet_files]
        con = duckdb.connect()
        try:
            df = con.execute("""
                SELECT
                    COUNT(*) AS total_rows,
                    COUNT(DISTINCT symbol) AS symbols,
                    MIN(date) AS min_date,
                    MAX(date) AS max_date,
                    SUM(volume) AS sum_volume
                FROM read_parquet(?)
            """, [paths]).fetchdf()
            print(df.to_string(index=False))
            # 이상치 샘플 (close<=0 or volume<0)
            bad = con.execute("""
                SELECT symbol, date, open, high, low, close, volume
                FROM read_parquet(?)
                WHERE close <= 0 OR volume < 0
                LIMIT 5
            """, [paths]).fetchdf()
            if not bad.empty:
                print("\n  이상 행 샘플 (close<=0 or volume<0):")
                print(bad.to_string(index=False))
            else:
                print("\n  이상 행 없음 (close>0, volume>=0).")
        except Exception as e:
            print(f"  ERROR: {e}")
        finally:
            con.close()

    print()
    print("5. Raw JSON 파일 수 (data/raw/ohlcv)")
    print("=" * 60)
    raw_dir = PROJECT_ROOT / "data" / "raw" / "ohlcv"
    if raw_dir.exists():
        n = len(list(raw_dir.rglob("*.json")))
        print(f"  {n} 개")
    else:
        print("  디렉터리 없음.")

    print()
    print("6. 10y coverage (by year) - 2016~2025 full range check")
    print("=" * 60)
    parquet_files = list(Path(PROJECT_ROOT, "data", "lake", "silver", "ohlcv_daily").rglob("data.parquet"))
    if not parquet_files:
        print("  silver parquet 없음.")
    else:
        paths = [p.as_posix() for p in parquet_files]
        con = duckdb.connect()
        try:
            # 연도별: 종목 수, 행 수, 해당 연도 내 min/max 일자
            by_year = con.execute("""
                SELECT
                    year(date) AS year,
                    COUNT(DISTINCT symbol) AS symbols,
                    COUNT(*) AS rows,
                    MIN(date) AS y_min,
                    MAX(date) AS y_max
                FROM read_parquet(?)
                WHERE date >= '2016-01-01' AND date <= '2025-12-31'
                GROUP BY year(date)
                ORDER BY 1
            """, [paths]).fetchdf()
            if by_year.empty:
                print("  2016~2025 구간 행 없음.")
            else:
                print(by_year.to_string(index=False))
            # 전체 기대 종목 수 (universe)
            _con_meta = duckdb.connect(META_DB.as_posix(), read_only=True)
            n_universe = _con_meta.execute("SELECT COUNT(*) FROM universe WHERE is_active = TRUE").fetchone()[0]
            _con_meta.close()
            # 2016년에 데이터가 있는 종목 수 vs 기대
            row_2016 = con.execute("""
                SELECT COUNT(DISTINCT symbol) AS c
                FROM read_parquet(?)
                WHERE date >= '2016-01-01' AND date < '2017-01-01'
            """, [paths]).fetchone()[0]
            row_2025 = con.execute("""
                SELECT COUNT(DISTINCT symbol) AS c
                FROM read_parquet(?)
                WHERE date >= '2025-01-01' AND date <= '2025-12-31'
            """, [paths]).fetchone()[0]
            print(f"\n  [expected] active symbols: {n_universe}")
            print(f"  [actual] symbols with 2016 data: {row_2016} (short: {n_universe - row_2016})")
            print(f"  [actual] symbols with 2025 data: {row_2025} (short: {n_universe - row_2025})")
            con.close()
        except Exception as e:
            print(f"  ERROR: {e}")
            try:
                con.close()
            except Exception:
                pass

    # 6-2: universe 활성 종목 중 2016년·2025년 silver 데이터 없는 종목 (meta + silver 조인)
    print()
    print("7. Missing 2016/2025 - symbols with no data in that year (active universe)")
    print("=" * 60)
    if not parquet_files:
        print("  (silver 없음)")
    else:
        paths = [p.as_posix() for p in parquet_files]
        con_meta = duckdb.connect(META_DB.as_posix(), read_only=True)
        con = duckdb.connect()
        try:
            # DuckDB에서 다른 DB 테이블 조인: ATTACH meta.duckdb AS meta; SELECT * FROM meta.universe
            con.execute(f"ATTACH '{META_DB.as_posix()}' AS meta (READ_ONLY)")
            # 2016년 데이터 있는 symbol 목록
            have_2016 = set(con.execute("""
                SELECT DISTINCT symbol FROM read_parquet(?)
                WHERE date >= '2016-01-01' AND date < '2017-01-01'
            """, [paths]).fetchdf()["symbol"].tolist())
            have_2025 = set(con.execute("""
                SELECT DISTINCT symbol FROM read_parquet(?)
                WHERE date >= '2025-01-01' AND date <= '2025-12-31'
            """, [paths]).fetchdf()["symbol"].tolist())
            active = con.execute("SELECT symbol FROM meta.universe WHERE is_active = TRUE").fetchdf()["symbol"].tolist()
            active_set = set(active)
            no_2016 = sorted(active_set - have_2016)
            no_2025 = sorted(active_set - have_2025)
            print(f"  symbols missing 2016 data: {len(no_2016)} (of {len(active_set)} active)")
            if no_2016:
                print(f"    sample (first 15): {no_2016[:15]}")
            print(f"  symbols missing 2025 data: {len(no_2025)}")
            if no_2025:
                print(f"    sample (first 15): {no_2025[:15]}")
        except Exception as e:
            print(f"  ERROR: {e}")
        finally:
            con.close()
            con_meta.close()

    print()
    print("8. Recommendation (if section 6/7 show gaps)")
    print("=" * 60)
    print("  To fill 2016~2025 fully, run from project root:")
    print("    python -m scripts.run_full_backfill")
    print("  Then: python -m scripts.verify_collection")
    print()
    print("Done.")


if __name__ == "__main__":
    main()
