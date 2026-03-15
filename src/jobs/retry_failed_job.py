"""
실패한 종목만 재수집한다.
collect_state에서 last_error가 있는 종목을 조회한 뒤, 지정한 기간으로 일봉 수집을 한 번 더 시도한다.
실행: python -m src.jobs.retry_failed_job --start-date 2025-01-01 --end-date 2026-03-09
"""
from __future__ import annotations
import argparse
from datetime import datetime

from src.storage import meta_store
from src.collect.collect_daily import collect_rows
from src.common.logger import get_logger

log = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="실패한 종목만 재수집")
    parser.add_argument("--start-date", required=True, help="수집 시작일 (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="수집 종료일 (YYYY-MM-DD)")
    parser.add_argument("--timeframe", default="1d", help="collect_state timeframe (기본 1d)")
    args = parser.parse_args()

    meta_store.ensure_tables()
    failed_df = meta_store.load_failed_symbols(timeframe=args.timeframe)

    if failed_df.empty:
        log.info("재수집할 실패 종목 없음.")
        return

    log.info("실패 종목 %s개 재수집: %s ~ %s", len(failed_df), args.start_date, args.end_date)
    run_id = f"retry_failed_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    success, failed = collect_rows(failed_df.to_dict(orient="records"), args.start_date, args.end_date)
    note = f"{args.start_date}..{args.end_date}"
    meta_store.log_run(run_id, "retry_failed_job", "success" if failed == 0 else "partial", len(failed_df), success, failed, note)
    log.info("retry_failed finished total=%s success=%s failed=%s", len(failed_df), success, failed)


if __name__ == "__main__":
    main()
