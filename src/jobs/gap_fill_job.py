"""
Silver에서 비어 있는 구간(연도별 row 부족)을 찾아 해당 구간만 재수집한다.
갭은 종목별로 인접 연도를 묶어(merge) 호출 횟수를 줄인다.
실행: python -m src.jobs.gap_fill_job [--target-start 2016-01-01] [--target-end 2025-12-31]
"""
from __future__ import annotations
import argparse
import inspect
import time
from datetime import datetime

from src.storage import meta_store
from src.collect.gap_detect import get_gap_intervals
from src.collect.collect_backfill import collect_one
from src.common.logger import get_logger

log = get_logger(__name__)

PROGRESS_LOG_EVERY = 10  # N건마다 진행률 로그
DEFAULT_MIN_ROWS_PER_YEAR = 200
DEFAULT_GAP_FILL_COVERAGE_THRESHOLD = 0.95


def _format_eta(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"


def run_gap_fill(
    *,
    target_start: str = "2016-01-01",
    target_end: str = "2025-12-31",
    min_rows_per_year: int = DEFAULT_MIN_ROWS_PER_YEAR,
    coverage_threshold: float = DEFAULT_GAP_FILL_COVERAGE_THRESHOLD,
    merge: bool = True,
    collector=collect_one,
    gap_loader=get_gap_intervals,
    ensure_tables_fn=meta_store.ensure_tables,
    log_run_fn=meta_store.log_run,
) -> tuple[int, int, int]:
    ensure_tables_fn()
    gap_df = gap_loader(
        target_start=target_start,
        target_end=target_end,
        min_rows_per_year=min_rows_per_year,
        merge=merge,
    )

    success = 0
    failed = 0
    run_id = f"gap_fill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    t0 = time.perf_counter()
    if gap_df.empty:
        log.info("No gaps to fill.")
        log_run_fn(
            run_id,
            "gap_fill_job",
            "success",
            0,
            0,
            0,
            note=f"{target_start}..{target_end}",
        )
        return 0, 0, 0

    n = len(gap_df)
    log.info(
        "Gap fill: %s intervals (merged=%s, coverage_threshold=%.2f), collecting...",
        n,
        merge,
        coverage_threshold,
    )
    collector_params = inspect.signature(collector).parameters
    collector_supports_threshold = (
        "coverage_threshold" in collector_params
        or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in collector_params.values())
    )
    for idx, (_, row) in enumerate(gap_df.iterrows(), start=1):
        symbol = str(row["symbol"])
        market = str(row["market"])
        name = str(row["name"])
        start_date = str(row["start_date"])
        end_date = str(row["end_date"])
        log.info("[%s/%s] %s %s..%s", idx, n, symbol, start_date, end_date)
        if collector_supports_threshold:
            sym, ok, err = collector(
                symbol,
                market,
                name,
                start_date,
                end_date,
                coverage_threshold=coverage_threshold,
            )
        else:
            sym, ok, err = collector(symbol, market, name, start_date, end_date)
        if ok:
            success += 1
        else:
            failed += 1
            log.error("gap fill fail %s: %s", sym, err)

        # 진행률 로그 (N건마다 + 경과/예상 잔여)
        if idx % PROGRESS_LOG_EVERY == 0 or idx == n:
            elapsed = time.perf_counter() - t0
            pct = 100.0 * idx / n
            eta = (elapsed / idx) * (n - idx) if idx > 0 else 0
            log.info("progress %s/%s (%.1f%%) elapsed=%s eta=%s ok=%s fail=%s",
                     idx, n, pct, _format_eta(elapsed), _format_eta(eta), success, failed)

    total_elapsed = time.perf_counter() - t0
    log_run_fn(
        run_id,
        "gap_fill_job",
        "success" if failed == 0 else "partial",
        n,
        success,
        failed,
        note=f"{target_start}..{target_end}",
    )
    log.info("gap_fill finished total=%s success=%s failed=%s in %s",
             n, success, failed, _format_eta(total_elapsed))
    return n, success, failed


def main() -> None:
    parser = argparse.ArgumentParser(description="비어 있는 구간만 재수집 (갭 채우기)")
    parser.add_argument("--target-start", default="2016-01-01", help="목표 구간 시작일")
    parser.add_argument("--target-end", default="2025-12-31", help="목표 구간 종료일")
    parser.add_argument("--min-rows-per-year", type=int, default=DEFAULT_MIN_ROWS_PER_YEAR, help="연도당 이 수 미만이면 갭으로 간주 (기본 200=전체수집 기준)")
    parser.add_argument("--coverage-threshold", type=float, default=DEFAULT_GAP_FILL_COVERAGE_THRESHOLD, help="기존 데이터 재사용 임계값. gap fill은 기본 0.95로 느슨한 재사용을 줄인다.")
    parser.add_argument("--no-merge", action="store_true", help="연도별 병합 없이 연도 단위로만 수집 (느리지만 세밀)")
    args = parser.parse_args()

    run_gap_fill(
        target_start=args.target_start,
        target_end=args.target_end,
        min_rows_per_year=args.min_rows_per_year,
        coverage_threshold=args.coverage_threshold,
        merge=not args.no_merge,
    )


if __name__ == "__main__":
    main()
